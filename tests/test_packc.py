"""packc test suite: lexer, parser, typechecker, emitters, CLI, round trip."""
import json
import os
import subprocess
import sys
import tarfile

import pytest

from packc import emit_mcp, emit_skillmd, emit_tests
from packc.lexer import LexError, lex
from packc.parser import GRAMMAR, N_GRAMMAR_RULES, ParseError, parse_text
from packc.typecheck import check_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")

BASE_PACK = """\
pack t {
  name: "t",
  version: "0.1.0",
  description: "t",
  author: "t",
  tools: ["do_it", "do_other"],
}

schema In {
  name: string,
  count: int = 1,
}

schema Out {
  ok: bool,
}

schema Ctx {
  amount_pkr: int = 0,
  sentiment: string = "neutral",
}

skill do-stuff {
  description: "Do it.",
  trigger: ["do the thing"],
  input: In,
  output: Out,
  tool: "do_it",
  steps: ["Do it."],
}

skill do-more {
  description: "Do more.",
  trigger: ["run another task"],
  input: In,
  tool: "do_other",
  steps: ["Do more."],
}

flow f {
  start: do-stuff,
  on done -> do-more,
}

escalate {
  context: Ctx,
  rule big: when amount_pkr > 100 -> tier: confirm,
  rule fallback: -> tier: auto,
}
"""


def check_codes(text):
    _, errors = check_text(text, "<test>")
    return [e.code for e in errors]


# ----------------------------------------------------------------- lexer ---

class TestLexer:
    def test_hyphenated_ident_vs_arrow(self):
        toks = lex("on done -> check-availability")
        kinds = [(t.kind, t.value) for t in toks if t.kind != "EOF"]
        assert ("IDENT", "check-availability") in kinds
        assert ("ARROW", "->") in kinds

    def test_string_escapes_and_comments(self):
        toks = lex('# comment\nskill x { description: "a \\"b\\" c" }')
        strs = [t.value for t in toks if t.kind == "STRING"]
        assert strs == ['a "b" c']

    def test_numbers_and_bools(self):
        toks = lex("a = -3 b = 2.5 c = true")
        vals = [t.value for t in toks if t.kind in ("NUMBER", "BOOL")]
        assert vals == [-3, 2.5, True]

    def test_unterminated_string_raises(self):
        with pytest.raises(LexError):
            lex('description: "oops')


# ---------------------------------------------------------------- parser ---

class TestParser:
    def test_base_pack_parses(self):
        pf = parse_text(BASE_PACK)
        assert pf.pack.name == "t"
        assert len(pf.schemas) == 3
        assert len(pf.skills) == 2
        assert len(pf.flows) == 1
        assert len(pf.escalate.rules) == 2

    def test_grammar_rule_count(self):
        assert N_GRAMMAR_RULES == len(GRAMMAR) == 19

    def test_missing_brace_raises(self):
        with pytest.raises(ParseError):
            parse_text('pack t { name: "t" ')

    def test_unknown_block_raises(self):
        with pytest.raises(ParseError):
            parse_text(BASE_PACK + "\nwidget w {}\n")

    def test_trailing_commas_ok(self):
        pf = parse_text('pack t { name: "t", version: "1", tools: ["a",], }')
        assert pf.pack.tools == ["a"]

    def test_optional_and_list_types(self):
        pf = parse_text(BASE_PACK.replace(
            "  count: int = 1,",
            "  count: int = 1,\n  tags: list[string]?,\n  kind: enum { a, b }?,"))
        fields = {f.name: f for f in pf.schemas[0].fields}
        from packc import ast as A
        assert isinstance(fields["tags"].type, A.OptionalType)
        assert isinstance(fields["kind"].type, A.OptionalType)


# ------------------------------------------------------------ typechecker ---

class TestTypecheck:
    def test_good_pack_has_no_errors(self):
        assert check_codes(BASE_PACK) == []

    def test_flagship_has_no_errors(self):
        for name in ("hostel-ops.pack", "restaurant.pack", "clinic.pack"):
            with open(os.path.join(EXAMPLES, name), encoding="utf-8") as fh:
                assert check_codes(fh.read()) == [], name

    def test_unknown_schema_ref(self):
        bad = BASE_PACK.replace("  input: In,", "  input: Nope,")
        assert "SKILL002" in check_codes(bad)

    def test_unknown_output_schema_ref(self):
        bad = BASE_PACK.replace("  output: Out,", "  output: Nope,")
        assert "SKILL003" in check_codes(bad)

    def test_bad_default_type(self):
        bad = BASE_PACK.replace("  count: int = 1,", '  count: int = "one",')
        assert "SCHEMA005" in check_codes(bad)

    def test_bad_enum_default(self):
        bad = BASE_PACK.replace(
            "  count: int = 1,",
            "  count: int = 1,\n  kind: enum { a, b } = c,")
        assert "SCHEMA005" in check_codes(bad)

    def test_trigger_overlap(self):
        bad = BASE_PACK.replace('trigger: ["do the thing"]', 'trigger: ["book the red room"]')
        bad = bad.replace('trigger: ["run another task"]', 'trigger: ["book the blue room"]')
        codes = check_codes(bad)
        assert "TRIG001" in codes, codes

    def test_trigger_no_overlap_single_shared_token(self):
        # shares only "task" after stopword removal -> must NOT flag
        bad = BASE_PACK.replace('trigger: ["run another task"]',
                                'trigger: ["finish the task"]')
        assert "TRIG001" not in check_codes(bad)

    def test_flow_to_undefined_skill(self):
        bad = BASE_PACK.replace("on done -> do-more,", "on done -> nowhere,")
        assert "FLOW002" in check_codes(bad)

    def test_flow_start_undefined(self):
        bad = BASE_PACK.replace("start: do-stuff,", "start: nowhere,")
        assert "FLOW001" in check_codes(bad)

    def test_invalid_tier(self):
        bad = BASE_PACK.replace("-> tier: auto,", "-> tier: sometimes,")
        assert "ESC001" in check_codes(bad)

    def test_duplicate_enum_value(self):
        bad = BASE_PACK.replace(
            "  count: int = 1,",
            "  count: int = 1,\n  kind: enum { a, b, a },")
        assert "SCHEMA003" in check_codes(bad)

    def test_duplicate_field(self):
        bad = BASE_PACK.replace("  count: int = 1,", "  count: int = 1,\n  name: string,")
        assert "SCHEMA002" in check_codes(bad)

    def test_unknown_field_in_escalate_condition(self):
        bad = BASE_PACK.replace("when amount_pkr > 100", "when nosuch > 100")
        assert "ESC002" in check_codes(bad)

    def test_condition_without_context(self):
        bad = BASE_PACK.replace("  context: Ctx,\n", "")
        assert "ESC002" in check_codes(bad)

    def test_condition_type_mismatch(self):
        bad = BASE_PACK.replace("when amount_pkr > 100", 'when sentiment > 100')
        assert "ESC003" in check_codes(bad)

    def test_condition_bool_literal_mismatch(self):
        bad = BASE_PACK.replace("when amount_pkr > 100", 'when amount_pkr == "high"')
        assert "ESC003" in check_codes(bad)

    def test_unresolvable_tool_ref(self):
        bad = BASE_PACK.replace('tool: "do_it",', 'tool: "missing_tool",')
        assert "SKILL004" in check_codes(bad)

    def test_schema_reference_cycle(self):
        bad = BASE_PACK + "\nschema A { b: B }\nschema B { a: A }\n"
        assert "SCHEMA006" in check_codes(bad)

    def test_duplicate_skill(self):
        bad = BASE_PACK.replace("skill do-more {", "skill do-stuff {")
        assert "SKILL001" in check_codes(bad)

    def test_missing_pack_block(self):
        assert "PACK002" in check_codes("schema S { a: string }")

    def test_syntax_error_code(self):
        _, errors = check_text("pack t { name: ", "<test>")
        assert errors and errors[0].code == "SYNTAX"


# --------------------------------------------------------------- emitters ---

def _load_server(path, modname="test_server_mod"):
    import importlib.util
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestEmitSkillmd:
    def test_frontmatter_and_sections(self):
        pf, errors = check_text(BASE_PACK, "<test>")
        assert not errors
        md = emit_skillmd.emit(pf)
        assert md.startswith("---\nname: t\n")
        assert "description: t" in md.split("---")[1]
        assert "## Skills" in md and "### do-stuff" in md
        assert "## Flows" in md and "## Escalation policy" in md
        assert "| `big` |" in md

    def test_flagship_skillmd(self, tmp_path):
        with open(os.path.join(EXAMPLES, "hostel-ops.pack"), encoding="utf-8") as fh:
            pf, errors = check_text(fh.read(), "hostel-ops.pack")
        assert not errors
        md = emit_skillmd.emit(pf)
        assert "14 skills" in md
        assert "### check-availability" in md
        assert "`rooms_available`" in md


class TestEmitMcp:
    def _server(self, tmp_path, text=BASE_PACK, name="srv"):
        pf, errors = check_text(text, "<test>")
        assert not errors
        path = str(tmp_path / f"{name}.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(emit_mcp.emit(pf))
        return _load_server(path, name)

    def test_dispatch_valid(self, tmp_path):
        srv = self._server(tmp_path)
        r = srv.dispatch("do_it", {"name": "x"})
        assert r["ok"] is True and r["tool"] == "do_it"
        assert r["validated_args"]["count"] == 1  # default filled

    def test_dispatch_invalid_type_rejected(self, tmp_path):
        srv = self._server(tmp_path)
        with pytest.raises(srv.ValidationError):
            srv.dispatch("do_it", {"name": "x", "count": "many"})

    def test_dispatch_missing_required_rejected(self, tmp_path):
        srv = self._server(tmp_path)
        with pytest.raises(srv.ValidationError):
            srv.dispatch("do_it", {})

    def test_dispatch_unknown_field_rejected(self, tmp_path):
        srv = self._server(tmp_path)
        with pytest.raises(srv.ValidationError):
            srv.dispatch("do_it", {"name": "x", "zzz": 1})

    def test_dispatch_unknown_tool(self, tmp_path):
        srv = self._server(tmp_path)
        with pytest.raises(KeyError):
            srv.dispatch("nope", {})

    def test_output_sample_conforms(self, tmp_path):
        srv = self._server(tmp_path)
        r = srv.dispatch("do_it", {"name": "x"})
        srv.validate(r["output"], ("object", srv.output_spec_for("do_it")), "$output")

    def test_tools_list_jsonrpc(self, tmp_path):
        srv = self._server(tmp_path)
        resp = srv.handle_request({"jsonrpc": "2.0", "id": 7,
                                   "method": "tools/list", "params": {}})
        names = sorted(t["name"] for t in resp["result"]["tools"])
        assert names == ["do_it", "do_other"]
        assert resp["result"]["tools"][0]["inputSchema"]["type"] == "object"

    def test_tools_call_jsonrpc_roundtrip(self, tmp_path):
        srv = self._server(tmp_path)
        resp = srv.handle_request({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                   "params": {"name": "do_it",
                                              "arguments": {"name": "x"}}})
        body = json.loads(resp["result"]["content"][0]["text"])
        assert body["ok"] is True

    def test_tools_call_invalid_args_jsonrpc_error(self, tmp_path):
        srv = self._server(tmp_path)
        resp = srv.handle_request({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                   "params": {"name": "do_it", "arguments": {"name": 5}}})
        assert resp["error"]["code"] == -32602

    def test_unknown_method_jsonrpc_error(self, tmp_path):
        srv = self._server(tmp_path)
        resp = srv.handle_request({"jsonrpc": "2.0", "id": 1, "method": "bogus", "params": {}})
        assert resp["error"]["code"] == -32601

    def test_stdio_loop_subprocess(self, tmp_path):
        srv_path = str(tmp_path / "srv_stdio.py")
        pf, _ = check_text(BASE_PACK, "<test>")
        with open(srv_path, "w", encoding="utf-8") as fh:
            fh.write(emit_mcp.emit(pf))
        reqs = "\n".join(json.dumps(r) for r in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "do_other", "arguments": {"name": "y"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "do_other", "arguments": {"name": 1}}},
        ])
        proc = subprocess.run([sys.executable, srv_path], input=reqs,
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0
        resps = [json.loads(line) for line in proc.stdout.strip().splitlines()]
        assert resps[0]["result"]["pack"] == "t"
        assert json.loads(resps[1]["result"]["content"][0]["text"])["ok"] is True
        assert resps[2]["error"]["code"] == -32602

    def test_zero_third_party_deps(self, tmp_path):
        # the emitted server must import with only the stdlib on the path
        pf, _ = check_text(BASE_PACK, "<test>")
        path = str(tmp_path / "srv_nodeps.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(emit_mcp.emit(pf))
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path = [p for p in sys.path if 'site-packages' not in p]; "
             f"import importlib.util; s = importlib.util.spec_from_file_location('m', {path!r}); "
             "m2 = importlib.util.module_from_spec(s); s.loader.exec_module(m2); "
             "print(m2.dispatch('do_it', {'name': 'x'})['ok'])"],
            capture_output=True, text=True, timeout=30)
        assert proc.stdout.strip() == "True", proc.stderr


class TestEmitTests:
    def test_generated_conformance_suite_passes(self, tmp_path):
        pf, errors = check_text(BASE_PACK, "<test>")
        assert not errors
        srv_path = str(tmp_path / "gen_server.py")
        with open(srv_path, "w", encoding="utf-8") as fh:
            fh.write(emit_mcp.emit(pf))
        test_path = str(tmp_path / "test_gen_conformance.py")
        with open(test_path, "w", encoding="utf-8") as fh:
            fh.write(emit_tests.emit(pf, srv_path))
        proc = subprocess.run([sys.executable, "-m", "pytest", test_path, "-q"],
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "passed" in proc.stdout


# ------------------------------------------------------------------- CLI ---

def _run_cli(*argv):
    from packc.cli import main
    return main(list(argv))


class TestCLI:
    def test_check_exit_0(self, tmp_path, capsys):
        p = tmp_path / "ok.pack"
        p.write_text(BASE_PACK, encoding="utf-8")
        assert _run_cli("check", str(p)) == 0
        assert "OK" in capsys.readouterr().out

    def test_check_exit_1(self, tmp_path):
        p = tmp_path / "bad.pack"
        p.write_text(BASE_PACK.replace('tool: "do_it",', 'tool: "nope",'),
                     encoding="utf-8")
        assert _run_cli("check", str(p)) == 1

    def test_emit_all_kinds(self, tmp_path):
        p = tmp_path / "ok.pack"
        p.write_text(BASE_PACK, encoding="utf-8")
        out = tmp_path / "out"
        out.mkdir()
        for kind in ("skillmd", "mcp", "tests"):
            assert _run_cli("emit", kind, str(p), "-o", str(out)) == 0
        assert (out / "t.SKILL.md").exists()
        assert (out / "t_mcp_server.py").exists()
        assert (out / "test_t_conformance.py").exists()

    def test_emit_refuses_on_check_failure(self, tmp_path):
        p = tmp_path / "bad.pack"
        p.write_text("pack t { name: ", encoding="utf-8")
        assert _run_cli("emit", "skillmd", str(p), "-o", str(tmp_path)) == 1

    def test_publish_roundtrip(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "t.pack").write_text(BASE_PACK, encoding="utf-8")
        reg = tmp_path / "registry"
        assert _run_cli("publish", str(src), "-o", str(reg)) == 0
        assert (reg / "t-0.1.0.tar.gz").exists()
        with tarfile.open(reg / "t-0.1.0.tar.gz") as tar:
            names = tar.getnames()
        assert any(n.endswith("t.SKILL.md") for n in names)
        assert any(n.endswith("t_mcp_server.py") for n in names)
        assert any(n.endswith("test_t_conformance.py") for n in names)
        with open(reg / "registry.json", encoding="utf-8") as fh:
            entries = json.load(fh)
        assert entries[0]["name"] == "t" and len(entries[0]["sha256"]) == 64

    def test_publish_duplicate_version_rejected(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "t.pack").write_text(BASE_PACK, encoding="utf-8")
        reg = tmp_path / "registry"
        assert _run_cli("publish", str(src), "-o", str(reg)) == 0
        assert _run_cli("publish", str(src), "-o", str(reg)) == 1


# -------------------------------------------------------------- round trip ---

class TestRoundTrip:
    def test_flagship_round_trip(self, tmp_path):
        """check -> emit all three -> run generated conformance suite."""
        from packc.parser import parse_file
        from packc.typecheck import TypeChecker
        src = os.path.join(EXAMPLES, "hostel-ops.pack")
        pf = parse_file(src)
        assert TypeChecker(pf).check() == []
        out = tmp_path / "rt"
        out.mkdir()
        assert _run_cli("emit", "skillmd", src, "-o", str(out)) == 0
        assert _run_cli("emit", "mcp", src, "-o", str(out)) == 0
        assert _run_cli("emit", "tests", src, "-o", str(out)) == 0
        test_file = out / "test_hostel_ops_conformance.py"
        proc = subprocess.run([sys.executable, "-m", "pytest", str(test_file), "-q"],
                              capture_output=True, text=True, timeout=180)
        assert proc.returncode == 0, proc.stdout + proc.stderr
