# packc

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![GitHub stars](https://img.shields.io/github/stars/habib-analyst/packc)](https://github.com/habib-analyst/packc/stargazers)

A typed authoring language for agent skill packs. Declare skills once — typed
schemas, cross-skill flows, escalation policies, versioned distribution — and
`packc` compiles to SKILL.md + a working MCP-style tool server + conformance
tests. It sits **upstream** of the skill-compiler wave (SIGIL, SkCC, SkVM):
they compile hand-written prose skills; packc is where the typed source of
truth is written.

## Quickstart

```bash
git clone https://github.com/habib-analyst/packc && cd packc
pip install -e .
```

Write a pack (`hello.pack`):

```
pack hello {
  name: "hello",
  version: "0.1.0",
  description: "Minimal greeting pack.",
  author: "Habib Ur Rehman",
  tools: ["guest_greet"],
}

schema Greeting {
  name: string,
}

schema Reply {
  text: string,
}

skill greet {
  description: "Greet a guest by name.",
  trigger: ["hello there", "say hi"],
  input: Greeting,
  output: Reply,
  tool: "guest_greet",
  steps: [
    "Read the guest's name from the input.",
    "Reply with a warm one-line greeting.",
  ],
}

escalate {
  rule fallback: -> tier: auto,
}
```

Typecheck it:

```bash
packc check hello.pack
```

```
packc check: OK — pack 'hello' v0.1.0: 2 schemas, 1 skills, 0 flows, 1 escalation rules
```

Emit all three artifacts and call the generated tool server:

```bash
packc emit mcp hello.pack -o ./out
python3 - <<'EOF'
import sys; sys.path.insert(0, "./out")
import hello_mcp_server as srv
print(srv.dispatch("guest_greet", {"name": "Ayesha"})["output"])
try:
    srv.dispatch("guest_greet", {"name": 42})
except srv.ValidationError as e:
    print("rejected:", e.code, e.path, "-", e.message)
EOF
```

```
packc emit mcp: wrote ./out/hello_mcp_server.py
{'text': 'sample'}
rejected: TYPE_MISMATCH $.name - expected string, got 42
```

The flagship example re-authors a real production pack — innkeeper's hostel
vertical (14 skills, 24 schemas, 3 flows, 5 escalation rules) — in
`examples/hostel-ops.pack`, plus restaurant and clinic mini-packs. A full
demo that exercises the emitted server via `dispatch()` and via the stdio
JSON-RPC loop:

```bash
python3 examples/run_mcp_demo.py
```

## Measured results

All numbers measured on this machine with `time.perf_counter`
(`python3 experiments/measure.py`), no placeholders.

**Parser** — 19 grammar productions, hand-written recursive descent, zero
dependencies:

| File | Lines | Parse time (mean ± sd, 11 runs) |
|---|---|---|
| `examples/hostel-ops.pack` (flagship) | 403 | 5.6 ms ± 0.5 |
| `examples/restaurant.pack` | 139 | 1.8 ms ± 0.0 |
| `examples/clinic.pack` | 133 | 1.9 ms ± 0.3 |
| `corpus/synth-small.pack` | 86 | 1.5 ms ± 0.7 |
| `corpus/synth-medium.pack` | 350 | 5.3 ms ± 0.6 |
| `corpus/synth-large.pack` | 1266 | 23.9 ms ± 5.9 |

**`packc check` violation detection** — 8 planted bad packs, one per
violation class:

| Violation class | Detected |
|---|---|
| unknown schema ref (`SKILL002`) | 1/1 |
| bad default type (`SCHEMA005`) | 1/1 |
| trigger-word overlap (`TRIG001`) | 1/1 |
| flow edge to undefined skill (`FLOW002`) | 1/1 |
| invalid escalation tier (`ESC001`) | 1/1 |
| duplicate enum value (`SCHEMA003`) | 1/1 |
| escalate condition on undeclared field (`ESC002`) | 1/1 |
| unresolvable tool-ref (`SKILL004`) | 1/1 |
| **Total** | **8/8** |

**Round trip on the flagship** (`hostel-ops.pack`):

| Stage | Time (mean ± sd, 5 runs) |
|---|---|
| `check` (parse + typecheck) | 10.9 ms ± 1.0 |
| emit SKILL.md | 0.3 ms ± 0.3 |
| emit MCP server | 0.5 ms ± 0.1 |
| emit conformance tests | 0.2 ms ± 0.0 |
| run generated conformance suite (50 tests) | 370.4 ms ± 50.2 |
| **Total** | **382.2 ms** |

(The suite time is pytest startup + 50 tests; the compiler itself is
~12 ms end to end.)

**Test suite:** 54/54 green in a clean venv
(`python3 -m venv /tmp/packc-venv`, pip install, `pytest tests/`).
**Generated conformance suite on the flagship:** 50/50 pass.

## How it works

```
 hello.pack
     │
     ▼
 ┌─────────┐   ┌──────────────┐
 │  lexer  │──▶│    parser    │  hand-written recursive descent, 19 productions
 └─────────┘   └──────┬───────┘
                      ▼
               ┌──────────────┐
               │ typechecker  │  packc check: unknown refs, bad defaults,
               └──────┬───────┘  duplicate fields/enums, flow edges, tiers,
                      │         escalate-condition fields, tool refs,
                      │         trigger-word disjointness, schema cycles
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
  SKILL.md      MCP server     conformance
   emitter       emitter       test emitter
```

- **SKILL.md emitter** — YAML frontmatter (`name`, `description`, `version`,
  `author`) plus a progressive-disclosure body: overview → per-skill sections
  (triggers, typed input/output tables, steps) → flows → escalation table →
  pack metadata.
- **MCP emitter** — a single self-contained Python file, zero third-party
  dependencies. Speaks JSON-RPC 2.0 over stdio (`initialize`, `tools/list`,
  `tools/call`). Each skill becomes a tool whose `inputSchema` is derived
  from the typed schema. `dispatch(tool, args)` validates arguments
  (wrong types, missing/unknown fields rejected with typed errors), fills
  declared defaults, and runs a **typed contract shell** returning a
  structured, schema-conformant sample result. packc guarantees the typed
  contract and routing; business logic plugs in via a sibling `handlers`
  module (one function per tool name).
- **Conformance-test emitter** — a standalone pytest file per pack: valid
  input → ok + schema-conformant output; wrong-type input → `ValidationError`;
  unknown field → `ValidationError`; defaults applied; unknown tool →
  `KeyError`; plus `tools/list`, trigger coverage, and tier-validity tests.
- **`packc publish`** — typechecks, emits all three artifacts, and writes a
  versioned `<name>-<version>.tar.gz` (sha256) plus an entry in
  `registry.json`.

## Honest limits

- Emitted tools are **typed contract shells**: packc guarantees the schema
  contract, validation, and routing — the business logic is yours to write
  (via the `handlers` module). The sample outputs are schema-conformant
  placeholders, not real backend calls.
- The server speaks **MCP-style JSON-RPC 2.0 over stdio** (`initialize`,
  `tools/list`, `tools/call`); it is not the full MCP SDK protocol
  (no sampling, no resources, no OAuth).
- Trigger disjointness is a lexical heuristic (≥2 shared non-stopword
  tokens), not semantic intent classification.
- The language targets vertical skill packs (front desks, clinics,
  restaurants); it is not a general-purpose programming language.

## CLI reference

```
packc check <file.pack>                  # parse + typecheck; exit 0 on pass
packc emit <skillmd|mcp|tests> <file.pack> -o <out>
packc publish <dir> -o <registry-dir>    # versioned tarball + sha256 + registry.json
```

## Citation

```bibtex
@software{rehman2026packc,
  author = {Habib Ur Rehman},
  title = {packc: The Vertical Skill-Pack Authoring Language},
  year = {2026},
  url = {https://github.com/habib-analyst/packc}
}
```

---
Built by [Habib Ur Rehman](https://github.com/habib-analyst) · ML/AI Engineer & healthcare-AI researcher ·
[LinkedIn](https://www.linkedin.com/in/hur-dev) · [habib.top](https://habib.top)
