# packc: The Vertical Skill-Pack Authoring Language
### Typed declarations in, SKILL.md + working MCP server + conformance tests out

**Habib Ur Rehman** · 2026-10-12

---

## 1. Claim

packc is a small typed language for authoring **vertical agent skill packs**.
You declare skills once — with typed input/output schemas, cross-skill flows,
escalation policies, and pack-level versioning — and the compiler emits
everything the skill ecosystem consumes:

1. a spec-compliant **SKILL.md** (YAML frontmatter + progressive-disclosure body),
2. a **working MCP-style tool server** (self-contained Python, zero third-party
   dependencies, JSON-RPC 2.0 over stdio, one tool per skill with schema-derived
   `inputSchema` and runtime argument validation),
3. a **conformance test suite** (pytest: valid input → ok + schema-conformant
   output; invalid input → typed rejection; trigger coverage; tier validity),
4. a **versioned distribution** (`packc publish`: tarball + sha256 + registry index).

The thesis: the skill ecosystem standardized the *artifact* (SKILL.md) but
left *authoring* as hand-written prose. packc owns authoring — typed source of
truth, compiled downstream — and deliberately does not compete with SKILL.md.

## 2. The gap

SKILL.md (agentskills.io, Dec 2025, 16+ tools) is the universal skill
artifact standard. The 2026 compiler wave works **downstream** of it:

- **SIGIL** (U. Michigan, https://arxiv.org/abs/2607.27309) — compiles prose
  SKILL.md *downstream* into typed harnesses via AG-IR.
- **SkCC** (Sun Yat-sen, https://arxiv.org/abs/2605.03353) — portable SkIR
  compile + security, also *downstream* of hand-written SKILL.md.
- **SkVM** (https://arxiv.org/pdf/2604.03088) — a skill VM/runtime for
  heterogeneous LLMs, again *downstream* at runtime.

All three consume hand-written skills; none own typed authoring, cross-skill
schema checking, escalation-as-code, or versioning/distribution. packc sits
**upstream** of SIGIL/SkCC — its SKILL.md output feeds their compilers —
not against them. (Gap verified by web search 2026-10-12. Killed alternatives:
a robustness-benchmark DSL — YAML already won; an FL-topology DSL — Python
won; an experiment-matrix DSL — Hydra/YAML won. Never build a language where
a config format already won.)

## 3. Method

### 3.1 Grammar

Hand-written recursive-descent parser (PEG-style), pure Python, zero
dependencies. **19 productions**, five block types:

```
pack_file     := block* EOF
block         := pack_block | schema_block | skill_block | flow_block | escalate_block
pack_block    := 'pack' IDENT '{' pack_field (',' pack_field)* ','? '}'
schema_block  := 'schema' IDENT '{' schema_field (',' schema_field)* ','? '}'
skill_block   := 'skill' IDENT '{' skill_field (',' skill_field)* ','? '}'
flow_block    := 'flow' IDENT '{' 'start' ':' IDENT (',' flow_edge)* ','? '}'
escalate_blk  := 'escalate' '{' escalate_item (',' escalate_item)* ','? '}'
```

### 3.2 Type system

`string | int | float | bool`, inline `enum { ... }`, `T?` optionals,
`list[T]`, named schema refs (with cycle detection), and `tool` refs that
must resolve against the pack's declared tool list. Defaults allowed
(`guests: int = 1`); enum defaults must be members; defaults are
literal-checked against the declared type.

### 3.3 `packc check`: the typed error list

Beyond standard reference/type checking, three checks are packc-specific:

- **Trigger-word disjointness** (`TRIG001`): pairwise across skills, two
  skills are flagged when any trigger-phrase pair shares ≥2 non-stopword
  tokens — the lexical proxy for "two skills will fight over the same
  utterance."
- **Escalation-condition checking** (`ESC002`/`ESC003`): rule conditions may
  only reference fields of the declared `context` schema, and comparisons
  must be type-consistent (`amount_pkr == "high"` is rejected).
- **Tool-ref resolution** (`SKILL004`): every skill's `tool` must be declared
  in the pack's `tools` list; duplicate tool assignment is rejected.

Full code list: `PACK002/3/4`, `SCHEMA001–008`, `SKILL001–007`,
`TRIG001`, `FLOW001/2`, `ESC001–005`, `SYNTAX`.

### 3.4 Backends

- **SKILL.md**: frontmatter (`name`, `description`, `version`, `author`);
  body in progressive disclosure — overview → per-skill detail (triggers,
  typed schema tables, steps) → flows → escalation table → metadata.
- **MCP server**: one self-contained `.py` file. `dispatch(tool, args)`
  validates args against the input schema (wrong type / missing / unknown
  field → `ValidationError` with code + path), fills declared defaults, and
  runs a **typed contract shell** returning a structured, schema-conformant
  sample result. Business logic plugs in via a sibling `handlers` module
  (one function per tool name). stdio loop handles `initialize`,
  `tools/list`, `tools/call` with JSON-RPC error codes.
- **Conformance tests**: standalone pytest file with the server path baked
  in — per skill: valid input → ok + output validates; corrupted input →
  rejection; unknown field → rejection; defaults applied; unknown tool →
  `KeyError`; plus registry, trigger-coverage, and tier-validity tests.
- **Publish**: check → emit all three → `<name>-<version>.tar.gz` + sha256 →
  `registry.json` entry (duplicate name+version rejected).

### 3.5 Flagship dogfood

`examples/hostel-ops.pack` re-authors a real production vertical —
innkeeper's hostel pack (`innkeeper/skills/hostel/pack.py`,
`innkeeper/core/escalation.py`): 14 skills (greeting, availability, booking,
cancel/modify, check-in/out, late checkout, housekeeping, payment info,
complaint, review, owner digest, owner approve), 24 schemas, 3 flows, and
the escalation policy's defaults + overrides + fail-closed fallback,
transcribed faithfully into packc. Two more worked examples: a restaurant
pack (4 skills) and a clinic pack (4 skills). `examples/run_mcp_demo.py`
exercises the emitted server via `dispatch()` and via the stdio loop in a
subprocess.

## 4. Experiments

All numbers measured with `time.perf_counter`
(`python3 experiments/measure.py`), pure CPU, $0.

### 4.1 Parser

| File | Lines | Parse time (mean ± sd, 11 runs) |
|---|---|---|
| `hostel-ops.pack` (flagship) | 403 | 5.6 ms ± 0.5 |
| `restaurant.pack` | 139 | 1.8 ms ± 0.0 |
| `clinic.pack` | 133 | 1.9 ms ± 0.3 |
| `corpus/synth-small.pack` | 86 | 1.5 ms ± 0.7 |
| `corpus/synth-medium.pack` | 350 | 5.3 ms ± 0.6 |
| `corpus/synth-large.pack` | 1266 | 23.9 ms ± 5.9 |

19 grammar productions; parse time scales ~linearly with file size
(~19 µs/line on the large synthetic pack).

### 4.2 `packc check` violation detection

8 planted bad packs, one per violation class — detection is by construction:

| Violation class | Code | Detected |
|---|---|---|
| unknown schema ref | SKILL002 | 1/1 |
| bad default type | SCHEMA005 | 1/1 |
| trigger-word overlap | TRIG001 | 1/1 |
| flow edge to undefined skill | FLOW002 | 1/1 |
| invalid escalation tier | ESC001 | 1/1 |
| duplicate enum value | SCHEMA003 | 1/1 |
| escalate condition on undeclared field | ESC002 | 1/1 |
| unresolvable tool-ref | SKILL004 | 1/1 |
| **Total** | | **8/8** |

### 4.3 Round trip on the flagship

| Stage | Time (mean ± sd, 5 runs) |
|---|---|
| `check` (parse + typecheck) | 10.9 ms ± 1.0 |
| emit SKILL.md | 0.3 ms ± 0.3 |
| emit MCP server | 0.5 ms ± 0.1 |
| emit conformance tests | 0.2 ms ± 0.0 |
| run generated conformance suite (50 tests) | 370.4 ms ± 50.2 |
| **Total** | **382.2 ms** |

The compiler itself is ~12 ms end to end; the suite time is pytest startup
plus 50 tests. Kill-gate result: **50/50 conformance tests pass** on the
flagship; tools callable via `dispatch()` **and** via the stdio loop in a
subprocess (verified: valid calls return `ok`, wrong types → `-32602`,
unknown methods → `-32601`).

### 4.4 Test suite

54 tests covering lexer, parser, typechecker (incl. all 8 planted violation
classes), all three emitters, CLI, publish, and the full flagship round
trip — **54/54 green in a clean venv**
(`python3 -m venv /tmp/packc-venv`, pip install, `pytest tests/`).

## 5. Honest limits

- Emitted tools are **typed contract shells**, not business logic: packc
  guarantees the schema contract, validation, and routing. Sample outputs
  are schema-conformant placeholders. The `handlers` module is the honest
  seam where authors plug in real backends.
- The server is **MCP-style JSON-RPC 2.0 over stdio**, not the full MCP SDK
  protocol (no sampling, resources, or auth).
- Trigger disjointness is a **lexical heuristic** (≥2 shared non-stopword
  tokens), not semantic intent disambiguation — near-synonym triggers can
  still slip through, and the checker says so in its message.
- packc targets vertical skill packs; it is not a general-purpose language.
- Adoption is the real risk (INVENTIONS.md): a DSL nobody uses fails no
  matter how clean the grammar. The innkeeper dogfood is load-bearing —
  the flagship pack is a real product vertical, not a toy.

## 6. Related work

- **SKILL.md / agentskills.io** — the artifact standard packc compiles *to*;
  complementary, not competitive.
- **SIGIL** (U. Michigan, arXiv:2607.27309), **SkCC** (Sun Yat-sen,
  arXiv:2605.03353), **SkVM** (arXiv:2604.03088) — downstream compilers and
  runtimes that consume hand-written SKILL.md. packc is the upstream
  authoring end; its output feeds these systems.
- **SMT/solver-aided config checkers** (e.g. YAML linters) — check values,
  not cross-skill contracts; none model triggers, flows, or escalation.

## 7. Conclusion

packc closes the authoring-side gap in the skill-pack pipeline with a small,
fully working language: 19-production grammar, a typechecker with 20+
typed error codes (including trigger disjointness and escalation-condition
checking), three real compiler backends, a CLI, and a publish path —
dogfooded on a production hostel vertical, 54/54 tests green, 8/8 violation
classes detected, full round trip in ~0.4 s. Next: PyPI release, a
`handlers/` reference backend for the flagship pack, and packc-authored
packs in innkeeper pilots as the adoption metric.
