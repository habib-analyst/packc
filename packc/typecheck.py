"""packc typechecker: `packc check` runs parse + this.

Produces a typed error list. Every error carries a machine-readable code,
a human message, and a source location. Exit code is 0 iff the list is empty.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import ast as A
from .parser import PRIMITIVES, TIERS, _MISSING

TIERS = frozenset(TIERS)


@dataclass
class CheckError:
    code: str
    message: str
    loc: A.Loc | None
    filename: str = "<string>"

    def __str__(self) -> str:
        where = f"{self.filename}:{self.loc}" if self.loc else self.filename
        return f"{self.code} {where}: {self.message}"


# Small built-in stopword list for the trigger-disjointness check.
STOPWORDS = frozenset("""
a an the is are was were be been being to of in on for with and or nor but
my your his her its our their mine yours ours theirs i me we us you he she
it they them this that these those at by from as do does did done please
now just can could would should shall will may might must not no yes hello
hey hi want need get got have has had having make made take took give gave
go going come came see saw look looking know get got check out up down over
""".split())


def _tokens(phrase: str) -> set[str]:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in phrase).split()
            if w and w not in STOPWORDS}


class TypeChecker:
    def __init__(self, pf: A.PackFile):
        self.pf = pf
        self.errors: list[CheckError] = []
        self.schemas: dict[str, A.SchemaBlock] = {}

    # -- driver ------------------------------------------------------------
    def check(self) -> list[CheckError]:
        self._check_pack()
        self._index_schemas()
        for s in self.pf.schemas:
            self._check_schema(s)
        self._check_schema_cycles()
        seen_skills: dict[str, A.SkillBlock] = {}
        for sk in self.pf.skills:
            if sk.name in seen_skills:
                self.err("SKILL001", f"duplicate skill name {sk.name!r}", sk.loc)
            else:
                seen_skills[sk.name] = sk
            self._check_skill(sk)
        self._check_trigger_disjointness()
        for fl in self.pf.flows:
            self._check_flow(fl, seen_skills)
        if self.pf.escalate:
            self._check_escalate(self.pf.escalate)
        return self.errors

    def err(self, code: str, message: str, loc: A.Loc | None) -> None:
        self.errors.append(CheckError(code, message, loc, self.pf.filename))

    # -- pack --------------------------------------------------------------
    def _check_pack(self) -> None:
        if self.pf.pack is None:
            self.err("PACK002", "missing pack block: every .pack file needs exactly one", None)
            return
        p = self.pf.pack
        if not p.present.get("name"):
            self.err("PACK003", "pack block is missing required field 'name'", p.loc)
        if not p.present.get("version"):
            self.err("PACK003", "pack block is missing required field 'version'", p.loc)
        seen: set[str] = set()
        for t in p.tools:
            if t in seen:
                self.err("PACK004", f"duplicate tool declaration {t!r} in pack tools", p.loc)
            seen.add(t)

    def _index_schemas(self) -> None:
        for s in self.pf.schemas:
            if s.name in self.schemas:
                self.err("SCHEMA001", f"duplicate schema name {s.name!r}", s.loc)
            else:
                self.schemas[s.name] = s

    # -- schemas -----------------------------------------------------------
    def _check_schema(self, s: A.SchemaBlock) -> None:
        seen: set[str] = set()
        for f in s.fields:
            if f.name in seen:
                self.err("SCHEMA002",
                         f"schema {s.name!r}: duplicate field {f.name!r}", f.loc)
            seen.add(f.name)
            self._check_type_ref(s.name, f.name, f.type, f.loc)
            if f.has_default:
                self._check_default(s.name, f)

    def _check_type_ref(self, schema: str, field: str, typ, loc: A.Loc) -> None:
        if isinstance(typ, A.PrimType):
            return
        if isinstance(typ, A.EnumType):
            seen: set[str] = set()
            for m in typ.members:
                if m in seen:
                    self.err("SCHEMA003",
                             f"schema {schema!r}: duplicate enum value {m!r} in field {field!r}",
                             typ.loc or loc)
                seen.add(m)
            if not typ.members:
                self.err("SCHEMA007",
                         f"schema {schema!r}: empty enum in field {field!r}", typ.loc or loc)
            return
        if isinstance(typ, A.ListType):
            self._check_type_ref(schema, field, typ.elem, loc)
            return
        if isinstance(typ, A.OptionalType):
            if isinstance(typ.inner, A.OptionalType):
                self.err("SCHEMA008",
                         f"schema {schema!r}: double-optional type on field {field!r} "
                         f"(T?? is not allowed)", typ.loc or loc)
            self._check_type_ref(schema, field, typ.inner, loc)
            return
        if isinstance(typ, A.RefType):
            if typ.name not in self.schemas:
                self.err("SCHEMA004",
                         f"schema {schema!r}: field {field!r} references unknown schema {typ.name!r}",
                         typ.loc or loc)
            return

    def _default_matches(self, typ, value) -> bool:
        """Does a parsed literal satisfy the declared type?"""
        if isinstance(typ, A.OptionalType):
            return self._default_matches(typ.inner, value)
        if isinstance(typ, A.PrimType):
            if typ.name == "string":
                return isinstance(value, str)
            if typ.name == "int":
                return isinstance(value, int) and not isinstance(value, bool)
            if typ.name == "float":
                return isinstance(value, (int, float)) and not isinstance(value, bool)
            if typ.name == "bool":
                return isinstance(value, bool)
        if isinstance(typ, A.EnumType):
            return isinstance(value, tuple) and value[0] == "ident" and value[1] in typ.members
        if isinstance(typ, A.ListType):
            return isinstance(value, list) and all(
                self._default_matches(typ.elem, v) for v in value)
        if isinstance(typ, A.RefType):
            return False  # named-schema defaults are not literal-expressible
        return False

    def _check_default(self, schema: str, f: A.SchemaField) -> None:
        if not self._default_matches(f.type, f.default):
            self.err("SCHEMA005",
                     f"schema {schema!r}: default {f.default!r} does not match "
                     f"type of field {f.name!r}", f.loc)

    def _check_schema_cycles(self) -> None:
        # depth-first search over named schema references
        def refs(s: A.SchemaBlock) -> list[str]:
            out: list[str] = []

            def walk(t) -> None:
                if isinstance(t, A.RefType):
                    out.append(t.name)
                elif isinstance(t, A.ListType):
                    walk(t.elem)
                elif isinstance(t, A.OptionalType):
                    walk(t.inner)

            for fl in s.fields:
                walk(fl.type)
            return out

        visiting: list[str] = []
        done: set[str] = set()

        def dfs(name: str) -> None:
            if name in done or name not in self.schemas:
                return
            if name in visiting:
                cycle = " -> ".join(visiting[visiting.index(name):] + [name])
                self.err("SCHEMA006", f"schema reference cycle: {cycle}",
                         self.schemas[name].loc)
                return
            visiting.append(name)
            for r in refs(self.schemas[name]):
                dfs(r)
            visiting.pop()
            done.add(name)

        for name in self.schemas:
            dfs(name)

    # -- skills ------------------------------------------------------------
    def _check_skill(self, sk: A.SkillBlock) -> None:
        if not sk.description:
            self.err("SKILL006", f"skill {sk.name!r}: missing required field 'description'", sk.loc)
        if not sk.triggers:
            self.err("SKILL006", f"skill {sk.name!r}: missing required field 'trigger'", sk.loc)
        elif not any(t.strip() for t in sk.triggers):
            self.err("SKILL007", f"skill {sk.name!r}: trigger list must not be empty", sk.loc)
        if not sk.input:
            self.err("SKILL006", f"skill {sk.name!r}: missing required field 'input'", sk.loc)
        elif sk.input not in self.schemas:
            self.err("SKILL002",
                     f"skill {sk.name!r}: input references unknown schema {sk.input!r}", sk.loc)
        if sk.output and sk.output not in self.schemas:
            self.err("SKILL003",
                     f"skill {sk.name!r}: output references unknown schema {sk.output!r}", sk.loc)
        if not sk.tool:
            self.err("SKILL006", f"skill {sk.name!r}: missing required field 'tool'", sk.loc)
        elif self.pf.pack and sk.tool not in self.pf.pack.tools:
            self.err("SKILL004",
                     f"skill {sk.name!r}: tool {sk.tool!r} is not declared in the pack's "
                     f"tools list {self.pf.pack.tools!r}", sk.loc)
        if not sk.steps:
            self.err("SKILL006", f"skill {sk.name!r}: missing required field 'steps'", sk.loc)
        elif not any(s.strip() for s in sk.steps):
            self.err("SKILL007", f"skill {sk.name!r}: steps list must not be empty", sk.loc)

    def _check_trigger_disjointness(self) -> None:
        skills = self.pf.skills
        for i in range(len(skills)):
            for j in range(i + 1, len(skills)):
                a, b = skills[i], skills[j]
                for ta in a.triggers or []:
                    for tb in b.triggers or []:
                        shared = _tokens(ta) & _tokens(tb)
                        if len(shared) >= 2:
                            self.err(
                                "TRIG001",
                                f"trigger overlap: skill {a.name!r} phrase {ta!r} and skill "
                                f"{b.name!r} phrase {tb!r} share {len(shared)} non-stopword "
                                f"tokens {sorted(shared)} (need < 2)",
                                b.loc)
                            break
                    else:
                        continue
                    break

    # -- flows -------------------------------------------------------------
    def _check_flow(self, fl: A.FlowBlock, skills: dict[str, A.SkillBlock]) -> None:
        if fl.start not in skills:
            self.err("FLOW001",
                     f"flow {fl.name!r}: start references undefined skill {fl.start!r}", fl.loc)
        for e in fl.edges:
            if e.target not in skills:
                self.err("FLOW002",
                         f"flow {fl.name!r}: on {e.event!r} -> undefined skill {e.target!r}",
                         e.loc)

    # -- escalate ----------------------------------------------------------
    def _check_escalate(self, esc: A.EscalateBlock) -> None:
        ctx_fields: dict[str, object] | None = None
        if esc.context:
            if esc.context not in self.schemas:
                self.err("ESC005",
                         f"escalate: context references unknown schema {esc.context!r}", esc.loc)
            else:
                ctx_fields = {f.name: f.type for f in self.schemas[esc.context].fields}
        for prefix, tier in esc.defaults:
            if tier not in TIERS:
                self.err("ESC001",
                         f"escalate: invalid tier {tier!r} for action prefix {prefix!r}; "
                         f"must be one of {sorted(TIERS)}", esc.loc)
        seen_rules: set[str] = set()
        for r in esc.rules:
            if r.name in seen_rules:
                self.err("ESC004", f"escalate: duplicate rule name {r.name!r}", r.loc)
            seen_rules.add(r.name)
            if r.tier not in TIERS:
                self.err("ESC001",
                         f"escalate: rule {r.name!r} has invalid tier {r.tier!r}; "
                         f"must be one of {sorted(TIERS)}", r.loc)
            if r.condition is not None:
                self._check_condition(r, ctx_fields)

    def _check_condition(self, r: A.Rule, ctx_fields: dict[str, object] | None) -> None:
        c = r.condition
        assert c is not None
        if ctx_fields is None:
            self.err("ESC002",
                     f"escalate: rule {r.name!r} has a condition but no context schema is "
                     f"declared, so field {c.field!r} is undeclared", c.loc)
            return
        if c.field not in ctx_fields:
            self.err("ESC002",
                     f"escalate: rule {r.name!r} condition references undeclared field "
                     f"{c.field!r} (context schema declares {sorted(ctx_fields)})", c.loc)
            return
        ftype = _strip_optional(ctx_fields[c.field])
        lit = c.value[1] if isinstance(c.value, tuple) and c.value[0] == "ident" else c.value
        numeric_op = c.op in (">", ">=", "<", "<=")
        if numeric_op:
            if not (_is_prim(ftype, "int") or _is_prim(ftype, "float")):
                self.err("ESC003",
                         f"escalate: rule {r.name!r}: operator {c.op!r} needs a numeric field, "
                         f"but {c.field!r} is not int/float", c.loc)
            elif isinstance(lit, bool) or not isinstance(lit, (int, float)):
                self.err("ESC003",
                         f"escalate: rule {r.name!r}: operator {c.op!r} needs a numeric literal, "
                         f"got {c.value!r}", c.loc)
        else:  # == / !=
            if _is_prim(ftype, "bool") and not isinstance(lit, bool):
                self.err("ESC003",
                         f"escalate: rule {r.name!r}: comparing bool field {c.field!r} "
                         f"to non-bool {c.value!r}", c.loc)
            elif isinstance(ftype, A.EnumType):
                if not (isinstance(c.value, tuple) and c.value[0] == "ident"
                        and c.value[1] in ftype.members):
                    self.err("ESC003",
                             f"escalate: rule {r.name!r}: comparing enum field {c.field!r} "
                             f"to {c.value!r}, expected one of {list(ftype.members)}", c.loc)
            elif _is_prim(ftype, "string") and not isinstance(c.value, str):
                self.err("ESC003",
                         f"escalate: rule {r.name!r}: comparing string field {c.field!r} "
                         f"to non-string {c.value!r}", c.loc)
            elif (_is_prim(ftype, "int") or _is_prim(ftype, "float")) and (
                    isinstance(lit, bool) or not isinstance(lit, (int, float))):
                self.err("ESC003",
                         f"escalate: rule {r.name!r}: comparing numeric field {c.field!r} "
                         f"to non-numeric {c.value!r}", c.loc)


def _strip_optional(t):
    while isinstance(t, A.OptionalType):
        t = t.inner
    return t


def _is_prim(t, name: str) -> bool:
    return isinstance(t, A.PrimType) and t.name == name


def check_text(text: str, filename: str = "<string>") -> tuple[A.PackFile | None, list[CheckError]]:
    """Parse and typecheck; returns (packfile or None, errors)."""
    from .parser import parse_text, ParseError
    from .lexer import LexError
    try:
        pf = parse_text(text, filename)
    except (ParseError, LexError) as e:
        line = getattr(e, "line", 1)
        col = getattr(e, "col", 1)
        return None, [CheckError("SYNTAX", str(e), A.Loc(line, col), filename)]
    return pf, TypeChecker(pf).check()


def check_file(path: str) -> tuple[A.PackFile | None, list[CheckError]]:
    with open(path, "r", encoding="utf-8") as fh:
        return check_text(fh.read(), path)
