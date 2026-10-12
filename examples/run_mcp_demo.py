#!/usr/bin/env python3
"""packc end-to-end demo: parse hostel-ops.pack -> check -> emit MCP server ->
exercise it via dispatch() AND via the JSON-RPC stdio loop in a subprocess.

Run from the repo root:  python3 examples/run_mcp_demo.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from packc import emit_mcp  # noqa: E402
from packc.typecheck import check_file  # noqa: E402

PACK = os.path.join(ROOT, "examples", "hostel-ops.pack")


def main() -> int:
    pf, errors = check_file(PACK)
    if errors:
        print("check FAILED:")
        for e in errors:
            print(" ", e)
        return 1
    print(f"check OK: {pf.pack.name} v{pf.pack.version} "
          f"({len(pf.skills)} skills, {len(pf.schemas)} schemas)")

    tmp = tempfile.mkdtemp(prefix="packc-demo-")
    server_path = os.path.join(tmp, "hostel_ops_mcp_server.py")
    with open(server_path, "w", encoding="utf-8") as fh:
        fh.write(emit_mcp.emit(pf))
    print(f"emitted MCP server -> {server_path}")

    spec = importlib.util.spec_from_file_location("demo_server", server_path)
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)

    print("\n-- dispatch() calls --")
    r = srv.dispatch("rooms_available",
                     {"check_in": "2026-10-20", "check_out": "2026-10-22", "guests": 2})
    print("rooms_available valid  ->", r["ok"], r["contract"],
          "| rooms in sample output:", len(r["output"]["rooms"]))
    try:
        srv.dispatch("rooms_available", {"check_in": "2026-10-20", "guests": "two"})
    except srv.ValidationError as e:
        print("rooms_available invalid -> rejected:", e.code, e.path)
    r = srv.dispatch("complaint_brief", {"detail": "AC not working", "sentiment": "angry"})
    print("complaint_brief valid   ->", r["ok"], r["contract"])

    print("\n-- stdio JSON-RPC loop (subprocess) --")
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "digest_build", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "booking_create",
                    "arguments": {"check_in": "x", "check_out": "y", "room_id": 5}}},
    ]
    proc = subprocess.run(
        [sys.executable, server_path],
        input="\n".join(json.dumps(r) for r in reqs),
        capture_output=True, text=True, timeout=30)
    for line in proc.stdout.strip().splitlines():
        resp = json.loads(line)
        rid = resp["id"]
        if "result" in resp:
            res = resp["result"]
            if "content" in res:
                body = json.loads(res["content"][0]["text"])
                print(f"  id={rid} OK   -> ok={body['ok']} tool={body['tool']}")
            elif "tools" in res and res["tools"] and isinstance(res["tools"][0], dict):
                print(f"  id={rid} OK   -> tools/list: {len(res['tools'])} tools")
            else:
                print(f"  id={rid} OK   -> protocol={res.get('protocol')} pack={res.get('pack')}")
        else:
            print(f"  id={rid} ERROR -> {resp['error']['code']}: "
                  f"{resp['error']['message'][:70]}")
    print("\ndemo complete: dispatch + stdio loop both exercised successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
