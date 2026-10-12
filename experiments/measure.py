#!/usr/bin/env python3
"""packc experiments: measured numbers for PAPER.md.

1. Parser: grammar stats + parse time over the corpus (perf_counter).
2. `packc check` violation detection: 8 planted bad packs, per-class rate.
3. Round-trip timing: check + 3 emitters + conformance pytest run, flagship.

Writes experiments/results.json and prints a summary table.
"""
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from packc import emit_mcp, emit_skillmd, emit_tests  # noqa: E402
from packc.parser import GRAMMAR, N_GRAMMAR_RULES, parse_file  # noqa: E402
from packc.typecheck import check_file, check_text  # noqa: E402

RESULTS = {"grammar_rules": N_GRAMMAR_RULES,
           "grammar_productions": GRAMMAR}


def timed(fn, runs=11):
    fn()  # warmup
    ts = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.mean(ts), statistics.stdev(ts)


def exp_parse():
    files = sorted(
        [os.path.join(ROOT, "examples", f) for f in
         ("hostel-ops.pack", "restaurant.pack", "clinic.pack")]
        + [os.path.join(ROOT, "corpus", f) for f in
           ("synth-small.pack", "synth-medium.pack", "synth-large.pack")])
    rows = []
    for f in files:
        mean, sd = timed(lambda: parse_file(f))
        nlines = sum(1 for _ in open(f, encoding="utf-8"))
        rows.append({"file": os.path.basename(f), "lines": nlines,
                     "parse_ms_mean": round(mean, 3), "parse_ms_sd": round(sd, 3)})
    RESULTS["parse"] = rows
    return rows


BASE = open(os.path.join(ROOT, "tests", "test_packc.py"),
            encoding="utf-8").read().split('BASE_PACK = """')[1].split('"""\n')[0]
if BASE.startswith("\\\n"):  # line-continuation backslash after the opening """
    BASE = BASE[2:]


def exp_violations():
    cases = [
        ("unknown schema ref",
         BASE.replace("  input: In,", "  input: Nope,"), "SKILL002"),
        ("bad default type",
         BASE.replace("  count: int = 1,", '  count: int = "one",'), "SCHEMA005"),
        ("trigger overlap",
         BASE.replace('trigger: ["do the thing"]', 'trigger: ["book the red room"]')
             .replace('trigger: ["run another task"]', 'trigger: ["book the blue room"]'),
         "TRIG001"),
        ("flow to undefined skill",
         BASE.replace("on done -> do-more,", "on done -> nowhere,"), "FLOW002"),
        ("invalid tier",
         BASE.replace("-> tier: auto,", "-> tier: sometimes,"), "ESC001"),
        ("duplicate enum value",
         BASE.replace("  count: int = 1,", "  count: int = 1,\n  kind: enum { a, b, a },"),
         "SCHEMA003"),
        ("unknown field in escalate condition",
         BASE.replace("when amount_pkr > 100", "when nosuch > 100"), "ESC002"),
        ("unresolvable tool-ref",
         BASE.replace('tool: "do_it",', 'tool: "missing_tool",'), "SKILL004"),
    ]
    rows = []
    for name, text, want in cases:
        _, errors = check_text(text, "<planted>")
        codes = [e.code for e in errors]
        detected = want in codes
        rows.append({"violation": name, "want": want, "got": codes,
                     "detected": "1/1" if detected else "0/1"})
    RESULTS["violations"] = rows
    return rows


def exp_roundtrip():
    src = os.path.join(ROOT, "examples", "hostel-ops.pack")
    timings = {}

    def step(name, fn):
        mean, sd = timed(fn, runs=5)
        timings[name] = {"ms_mean": round(mean, 3), "ms_sd": round(sd, 3)}

    text = open(src, encoding="utf-8").read()
    step("check (parse+typecheck)", lambda: check_file(src))
    pf, errors = check_file(src)
    assert not errors
    step("emit SKILL.md", lambda: emit_skillmd.emit(pf))
    step("emit MCP server", lambda: emit_mcp.emit(pf))

    tmp = tempfile.mkdtemp(prefix="packc-rt-")
    srv_path = os.path.join(tmp, "hostel_ops_mcp_server.py")
    with open(srv_path, "w", encoding="utf-8") as fh:
        fh.write(emit_mcp.emit(pf))
    step("emit conformance tests", lambda: emit_tests.emit(pf, srv_path))
    test_path = os.path.join(tmp, "test_hostel_ops_conformance.py")
    with open(test_path, "w", encoding="utf-8") as fh:
        fh.write(emit_tests.emit(pf, srv_path))

    def run_pytest():
        subprocess.run([sys.executable, "-m", "pytest", test_path, "-q", "-p", "no:cacheprovider"],
                       capture_output=True, text=True, timeout=180, cwd=tmp)
    step("run conformance suite (50 tests)", run_pytest)
    proc = subprocess.run([sys.executable, "-m", "pytest", test_path, "-q", "-p", "no:cacheprovider"],
                          capture_output=True, text=True, timeout=180, cwd=tmp)
    RESULTS["roundtrip"] = timings
    RESULTS["conformance_pass"] = proc.returncode == 0
    RESULTS["conformance_summary"] = proc.stdout.strip().splitlines()[-1] if proc.stdout else ""
    return timings


def main():
    print("== 1. parser ==")
    print(f"grammar productions: {N_GRAMMAR_RULES}")
    for r in exp_parse():
        print(f"  {r['file']:22s} {r['lines']:5d} lines  {r['parse_ms_mean']:8.3f} ms ± {r['parse_ms_sd']:.3f}")
    print("== 2. violation detection ==")
    n_ok = 0
    for r in exp_violations():
        n_ok += r["detected"] == "1/1"
        print(f"  [{r['detected']}] {r['violation']:32s} want={r['want']} got={r['got']}")
    RESULTS["violation_detection"] = f"{n_ok}/{len(RESULTS['violations'])}"
    print(f"  total: {n_ok}/{len(RESULTS['violations'])}")
    print("== 3. round-trip (flagship) ==")
    for name, t in exp_roundtrip().items():
        print(f"  {name:38s} {t['ms_mean']:9.3f} ms ± {t['ms_sd']:.3f}")
    total = sum(t["ms_mean"] for t in RESULTS["roundtrip"].values())
    print(f"  {'TOTAL':38s} {total:9.3f} ms")
    print("  conformance suite:", RESULTS["conformance_summary"])
    out = os.path.join(ROOT, "experiments", "results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(RESULTS, fh, indent=2)
    print("wrote", out)


if __name__ == "__main__":
    main()
