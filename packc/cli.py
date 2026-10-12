"""packc CLI: check | emit | publish."""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__, emit_mcp, emit_skillmd, emit_tests
from .publish import publish
from .typecheck import check_file


def _default_out(pack_name: str, kind: str) -> str:
    base = pack_name.replace("-", "_")
    if kind == "skillmd":
        return f"{pack_name}.SKILL.md"
    if kind == "mcp":
        return f"{base}_mcp_server.py"
    return f"test_{base}_conformance.py"


def cmd_check(args) -> int:
    _, errors = check_file(args.file)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        print(f"packc check: FAILED ({len(errors)} error(s))", file=sys.stderr)
        return 1
    from .parser import parse_file
    pf = parse_file(args.file)
    n_rules = len(pf.escalate.rules) if pf.escalate else 0
    print(f"packc check: OK — pack {pf.pack.name!r} v{pf.pack.version}: "
          f"{len(pf.schemas)} schemas, {len(pf.skills)} skills, "
          f"{len(pf.flows)} flows, {n_rules} escalation rules")
    return 0


def cmd_emit(args) -> int:
    from .lexer import LexError
    from .parser import ParseError, parse_file
    from .typecheck import TypeChecker
    try:
        pf = parse_file(args.file)
    except (ParseError, LexError) as e:
        print(f"SYNTAX {args.file}:{e}", file=sys.stderr)
        print("packc emit: refused — packc check failed", file=sys.stderr)
        return 1
    errors = TypeChecker(pf).check()
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        print("packc emit: refused — packc check failed", file=sys.stderr)
        return 1
    out = args.o
    if os.path.isdir(out):
        out = os.path.join(out, _default_out(pf.pack.name, args.kind))
    if args.kind == "skillmd":
        text = emit_skillmd.emit(pf)
    elif args.kind == "mcp":
        text = emit_mcp.emit(pf)
    else:
        # bake the server path the tests will import: alongside the test file
        # when -o is a directory, else next to the chosen output path
        server_guess = os.path.join(os.path.dirname(os.path.abspath(out)),
                                    _default_out(pf.pack.name, "mcp"))
        text = emit_tests.emit(pf, server_guess)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"packc emit {args.kind}: wrote {out}")
    return 0


def cmd_publish(args) -> int:
    try:
        entry = publish(args.dir, args.o)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 1
    import json
    print(f"packc publish: {entry['tarball']}  sha256={entry['sha256'][:16]}…")
    print(json.dumps(entry, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="packc",
                                 description="The vertical skill-pack authoring language.")
    ap.add_argument("--version", action="version", version=f"packc {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="Parse + typecheck a .pack file.")
    c.add_argument("file", help="Path to the .pack file.")
    c.set_defaults(func=cmd_check)

    e = sub.add_parser("emit", help="Emit an artifact from a .pack file.")
    e.add_argument("kind", choices=["skillmd", "mcp", "tests"])
    e.add_argument("file", help="Path to the .pack file.")
    e.add_argument("-o", required=True, help="Output file or directory.")
    e.set_defaults(func=cmd_emit)

    p = sub.add_parser("publish", help="Build a versioned tarball + registry entry.")
    p.add_argument("dir", help="Directory containing exactly one .pack file.")
    p.add_argument("-o", required=True, help="Registry directory to publish into.")
    p.set_defaults(func=cmd_publish)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
