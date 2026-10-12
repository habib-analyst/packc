"""packc parser: hand-written recursive-descent (PEG-style) over the token stream.

Grammar (19 productions):
"""
from __future__ import annotations

from . import ast as A
from .lexer import Token, lex, LexError


GRAMMAR = [
    "pack_file    := block* EOF",
    "block        := pack_block | schema_block | skill_block | flow_block | escalate_block",
    "pack_block   := 'pack' IDENT '{' pack_field (',' pack_field)* ','? '}'",
    "pack_field   := 'name' ':' STRING | 'version' ':' STRING | 'description' ':' STRING"
    " | 'author' ':' STRING | 'tools' ':' '[' STRING (',' STRING)* ','? ']'",
    "schema_block := 'schema' IDENT '{' schema_field (',' schema_field)* ','? '}'",
    "schema_field := IDENT ':' type ('=' literal)?",
    "type         := base_type '?'?",
    "base_type    := 'string' | 'int' | 'float' | 'bool' | 'list' '[' type ']'"
    " | 'enum' '{' IDENT (',' IDENT)* ','? '}' | IDENT",
    "literal      := STRING | NUMBER | BOOL | IDENT | '[' literal (',' literal)* ','? ']'",
    "skill_block  := 'skill' IDENT '{' skill_field (',' skill_field)* ','? '}'",
    "skill_field  := 'description' ':' STRING | 'trigger' ':' '[' STRING (',' STRING)* ','? ']'"
    " | 'input' ':' IDENT | 'output' ':' IDENT | 'tool' ':' STRING | 'steps' ':' '[' STRING (',' STRING)* ','? ']'",
    "flow_block   := 'flow' IDENT '{' 'start' ':' IDENT (',' flow_edge)* ','? '}'",
    "flow_edge    := 'on' IDENT '->' IDENT",
    "escalate_blk := 'escalate' '{' escalate_item (',' escalate_item)* ','? '}'",
    "escalate_item:= 'context' ':' IDENT | 'defaults' ':' '{' STRING ':' tier (',' STRING ':' tier)* ','? '}' | rule",
    "rule         := 'rule' IDENT ':' ('when' condition)? '->' 'tier' ':' tier",
    "condition    := IDENT comp_op literal",
    "comp_op      := '>' | '>=' | '<' | '<=' | '==' | '!='",
    "tier         := 'auto' | 'confirm' | 'human_only'",
]

N_GRAMMAR_RULES = len(GRAMMAR)

PRIMITIVES = {"string", "int", "float", "bool"}
TIERS = {"auto", "confirm", "human_only"}
COMP_OPS = {"GT": ">", "GTE": ">=", "LT": "<", "LTE": "<=", "EQEQ": "==", "BANGEQ": "!="}


class ParseError(Exception):
    def __init__(self, message: str, token: Token):
        super().__init__(f"{token.line}:{token.col}: {message}")
        self.line = token.line
        self.col = token.col


_MISSING = object()


class Parser:
    def __init__(self, tokens: list[Token], filename: str = "<string>"):
        self.toks = tokens
        self.pos = 0
        self.filename = filename

    # -- cursor ------------------------------------------------------------
    def peek(self) -> Token:
        return self.toks[self.pos]

    def next(self) -> Token:
        tok = self.toks[self.pos]
        if tok.kind != "EOF":
            self.pos += 1
        return tok

    def at(self, kind: str, value: object = None) -> bool:
        t = self.peek()
        return t.kind == kind and (value is None or t.value == value)

    def expect(self, kind: str, value: object = None) -> Token:
        t = self.peek()
        if t.kind != kind or (value is not None and t.value != value):
            want = f"{kind}" + (f" {value!r}" if value is not None else "")
            raise ParseError(f"expected {want}, found {t.kind} {t.value!r}", t)
        return self.next()

    def expect_ident(self, word: str | None = None) -> Token:
        t = self.expect("IDENT")
        if word is not None and t.value != word:
            raise ParseError(f"expected keyword {word!r}, found {t.value!r}", t)
        return t

    def _comma_list(self, item_fn, end_kind: str) -> list:
        items = []
        if self.at(end_kind):
            return items
        while True:
            items.append(item_fn())
            if self.at("COMMA"):
                self.next()
                if self.at(end_kind):  # trailing comma
                    break
                continue
            break
        return items

    # -- entry -------------------------------------------------------------
    def parse(self) -> A.PackFile:
        pack = None
        schemas: list[A.SchemaBlock] = []
        skills: list[A.SkillBlock] = []
        flows: list[A.FlowBlock] = []
        escalate = None
        while not self.at("EOF"):
            t = self.peek()
            if t.kind != "IDENT":
                raise ParseError(f"expected block keyword, found {t.kind} {t.value!r}", t)
            kw = t.value
            if kw == "pack":
                pack = self._pack_block()
            elif kw == "schema":
                schemas.append(self._schema_block())
            elif kw == "skill":
                skills.append(self._skill_block())
            elif kw == "flow":
                flows.append(self._flow_block())
            elif kw == "escalate":
                escalate = self._escalate_block()
            else:
                raise ParseError(f"unknown block {kw!r}; expected pack|schema|skill|flow|escalate", t)
        return A.PackFile(pack=pack, schemas=schemas, skills=skills,
                          flows=flows, escalate=escalate, filename=self.filename)

    # -- pack --------------------------------------------------------------
    def _pack_block(self) -> A.PackBlock:
        kw = self.expect_ident("pack")
        name = self.expect("IDENT").value
        self.expect("LBRACE")
        fields: dict[str, object] = {}
        present: dict[str, bool] = {}
        for f in self._comma_list(self._pack_field, "RBRACE"):
            k, v, _ = f
            fields[k] = v
            present[k] = True
        self.expect("RBRACE")
        loc = A.Loc(kw.line, kw.col)
        return A.PackBlock(
            name=name,
            version=fields.get("version", ""),
            description=fields.get("description", ""),
            author=fields.get("author", ""),
            tools=list(fields.get("tools", [])),
            loc=loc, present=present,
        )

    def _pack_field(self):
        t = self.expect("IDENT")
        k = t.value
        self.expect("COLON")
        if k in ("name", "version", "description", "author"):
            v = self.expect("STRING").value
        elif k == "tools":
            self.expect("LBRACK")
            v = self._comma_list(lambda: self.expect("STRING").value, "RBRACK")
            self.expect("RBRACK")
        else:
            raise ParseError(f"unknown pack field {k!r}; expected name|version|description|author|tools", t)
        return (k, v, t)

    # -- schema ------------------------------------------------------------
    def _schema_block(self) -> A.SchemaBlock:
        kw = self.expect_ident("schema")
        name = self.expect("IDENT").value
        self.expect("LBRACE")
        fields = self._comma_list(self._schema_field, "RBRACE")
        self.expect("RBRACE")
        return A.SchemaBlock(name=name, fields=fields, loc=A.Loc(kw.line, kw.col))

    def _schema_field(self) -> A.SchemaField:
        t = self.expect("IDENT")
        self.expect("COLON")
        typ = self._type()
        default, has_default = _MISSING, False
        if self.at("EQ"):
            self.next()
            default, has_default = self._literal(), True
        return A.SchemaField(name=t.value, type=typ, default=default,
                             has_default=has_default, loc=A.Loc(t.line, t.col))

    def _type(self):
        t = self.peek()
        if t.kind == "IDENT" and t.value == "list":
            self.next()
            self.expect("LBRACK")
            elem = self._type()
            self.expect("RBRACK")
            typ: object = A.ListType(elem=elem, loc=A.Loc(t.line, t.col))
        elif t.kind == "IDENT" and t.value == "enum":
            self.next()
            self.expect("LBRACE")
            members = self._comma_list(lambda: self.expect("IDENT").value, "RBRACE")
            self.expect("RBRACE")
            typ = A.EnumType(members=tuple(members), loc=A.Loc(t.line, t.col))
        elif t.kind == "IDENT" and t.value in PRIMITIVES:
            self.next()
            typ = A.PrimType(name=t.value, loc=A.Loc(t.line, t.col))
        elif t.kind == "IDENT":
            self.next()
            typ = A.RefType(name=t.value, loc=A.Loc(t.line, t.col))
        else:
            raise ParseError(f"expected a type, found {t.kind} {t.value!r}", t)
        if self.at("QMARK"):
            q = self.next()
            typ = A.OptionalType(inner=typ, loc=A.Loc(q.line, q.col))
        return typ

    def _literal(self):
        t = self.peek()
        if t.kind in ("STRING", "NUMBER", "BOOL"):
            self.next()
            return t.value
        if t.kind == "IDENT":  # enum member / bare identifier literal
            self.next()
            return ("ident", t.value)
        if t.kind == "LBRACK":
            self.next()
            items = self._comma_list(self._literal, "RBRACK")
            self.expect("RBRACK")
            return items
        raise ParseError(f"expected a literal, found {t.kind} {t.value!r}", t)

    # -- skill -------------------------------------------------------------
    def _skill_block(self) -> A.SkillBlock:
        kw = self.expect_ident("skill")
        name = self.expect("IDENT").value
        self.expect("LBRACE")
        vals: dict[str, object] = {}
        for k, v, _ in self._comma_list(self._skill_field, "RBRACE"):
            vals[k] = v
        self.expect("RBRACE")
        return A.SkillBlock(
            name=name,
            description=vals.get("description"),
            triggers=vals.get("trigger"),
            input=vals.get("input"),
            output=vals.get("output"),
            tool=vals.get("tool"),
            steps=vals.get("steps"),
            loc=A.Loc(kw.line, kw.col),
        )

    def _skill_field(self):
        t = self.expect("IDENT")
        k = t.value
        self.expect("COLON")
        if k == "description":
            v = self.expect("STRING").value
        elif k in ("trigger", "steps"):
            self.expect("LBRACK")
            v = self._comma_list(lambda: self.expect("STRING").value, "RBRACK")
            self.expect("RBRACK")
        elif k in ("input", "output"):
            v = self.expect("IDENT").value
        elif k == "tool":
            v = self.expect("STRING").value
        else:
            raise ParseError(
                "unknown skill field %r; expected description|trigger|input|output|tool|steps" % k, t)
        return (k, v, t)

    # -- flow --------------------------------------------------------------
    def _flow_block(self) -> A.FlowBlock:
        kw = self.expect_ident("flow")
        name = self.expect("IDENT").value
        self.expect("LBRACE")
        self.expect_ident("start")
        self.expect("COLON")
        start = self.expect("IDENT").value
        edges: list[A.FlowEdge] = []
        while self.at("COMMA"):
            self.next()
            if self.at("RBRACE"):
                break
            edges.append(self._flow_edge())
        self.expect("RBRACE")
        return A.FlowBlock(name=name, start=start, edges=edges, loc=A.Loc(kw.line, kw.col))

    def _flow_edge(self) -> A.FlowEdge:
        on = self.expect_ident("on")
        event = self.expect("IDENT").value
        self.expect("ARROW")
        target = self.expect("IDENT").value
        return A.FlowEdge(event=event, target=target, loc=A.Loc(on.line, on.col))

    # -- escalate ----------------------------------------------------------
    def _escalate_block(self) -> A.EscalateBlock:
        kw = self.expect_ident("escalate")
        self.expect("LBRACE")
        context = None
        defaults: list[tuple[str, str]] = []
        rules: list[A.Rule] = []
        for kind, val, _ in self._comma_list(self._escalate_item, "RBRACE"):
            if kind == "context":
                context = val
            elif kind == "defaults":
                defaults = val
            else:
                rules.append(val)
        self.expect("RBRACE")
        return A.EscalateBlock(context=context, defaults=defaults, rules=rules,
                               loc=A.Loc(kw.line, kw.col))

    def _escalate_item(self):
        t = self.expect("IDENT")
        k = t.value
        if k == "rule":
            return ("rule", self._rule(), t)
        self.expect("COLON")
        if k == "context":
            return ("context", self.expect("IDENT").value, t)
        if k == "defaults":
            self.expect("LBRACE")
            pairs = self._comma_list(self._default_pair, "RBRACE")
            self.expect("RBRACE")
            return ("defaults", pairs, t)
        raise ParseError(f"unknown escalate item {k!r}; expected context|defaults|rule", t)

    def _default_pair(self):
        k = self.expect("STRING").value
        self.expect("COLON")
        tier = self.expect("IDENT").value
        return (k, tier)

    def _rule(self) -> A.Rule:
        name_t = self.expect("IDENT")
        self.expect("COLON")
        cond = None
        if self.at("IDENT", "when"):
            self.next()
            cond = self._condition()
        self.expect("ARROW")
        self.expect_ident("tier")
        self.expect("COLON")
        tier = self.expect("IDENT").value
        return A.Rule(name=name_t.value, condition=cond, tier=tier,
                      loc=A.Loc(name_t.line, name_t.col))

    def _condition(self) -> A.Condition:
        f = self.expect("IDENT")
        op_t = self.peek()
        if op_t.kind not in COMP_OPS:
            raise ParseError(f"expected comparison operator, found {op_t.kind} {op_t.value!r}", op_t)
        self.next()
        val = self._literal()
        return A.Condition(field=f.value, op=COMP_OPS[op_t.kind], value=val,
                           loc=A.Loc(f.line, f.col))


def parse_text(text: str, filename: str = "<string>") -> A.PackFile:
    return Parser(lex(text, filename), filename).parse()


def parse_file(path: str) -> A.PackFile:
    with open(path, "r", encoding="utf-8") as fh:
        return parse_text(fh.read(), path)
