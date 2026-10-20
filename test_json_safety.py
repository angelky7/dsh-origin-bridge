"""Offline checks that the bridge can never emit non-standard JSON.

json.dumps writes a bare Infinity / NaN by default, which RFC 8259 does not allow:
a strict client (dsh, JS JSON.parse) fails on the whole response, so one overflowed
number looks like "the bridge is dead". Two guards, both checked here.
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import originlab as ol  # noqa: E402

PASSED = 0
FAILED = []


def ok(label, cond, detail=""):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILED.append("%s %s" % (label, detail))
    print("%s %s%s" % ("PASS" if cond else "FAIL", label, (" | " + str(detail)[:90]) if detail else ""))


INF = float("inf")


def strict(text):
    """Parse the way a JS client would: no Infinity, no NaN."""
    return json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))


def main():
    # ------------------------------------------------------- sanitize() mapping
    ok("inf -> None", ol.sanitize(INF) is None, ol.sanitize(INF))
    ok("-inf -> None", ol.sanitize(-INF) is None)
    ok("nan -> None", ol.sanitize(float("nan")) is None)
    ok("missing sentinel -> None", ol.sanitize(ol.MISSING_SENTINEL) is None)
    ok("big finite stays", ol.sanitize(1.7976931348623157e308) == 1.7976931348623157e308)
    ok("zero stays", ol.sanitize(0.0) == 0.0)
    ok("int stays", ol.sanitize(42) == 42)
    ok("string stays", ol.sanitize("ok") == "ok")
    nested = ol.sanitize({"a": [1.0, INF, {"b": -INF, "c": float("nan")}], "d": "x"})
    ok("nested cleaned",
       json.dumps(nested, allow_nan=False) == '{"a": [1.0, null, {"b": null, "c": null}], "d": "x"}',
       nested)
    ok("tuple cleaned", ol.sanitize((INF, 1.0)) == [None, 1.0])

    # ------------------------------------------------------- the reply guard
    # Simulate a handler that forgot to sanitize. The child server must still never
    # write bytes a strict parser rejects, and must not die on the way.
    import subprocess

    try:
        json.dumps({"value": math.inf}, allow_nan=False)
        raised = False
    except ValueError:
        raised = True
    ok("allow_nan=False really rejects Infinity", raised)

    child = (
        "import io, json, math, os, sys\n"
        "sys.path.insert(0, os.environ['BRIDGE_DIR'])\n"
        "import server\n"
        "server._handle = lambda msg: {'jsonrpc': '2.0', 'id': msg.get('id'),\n"
        "                               'result': {'value': math.inf}}\n"
        "req = json.dumps({'jsonrpc': '2.0', 'id': 7, 'method': 'tools/list'}) + '\\n'\n"
        "sys.stdin = io.TextIOWrapper(io.BytesIO(req.encode('utf-8')), encoding='utf-8')\n"
        "sys.exit(server.main() or 0)\n"
    )
    env = dict(os.environ, BRIDGE_DIR=os.path.dirname(os.path.abspath(__file__)),
               PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, "-c", child], capture_output=True, env=env, timeout=120)
    written = proc.stdout.decode("utf-8", "replace")
    ok("child exited cleanly", proc.returncode == 0,
       (proc.stderr.decode("utf-8", "replace") or "")[:160])
    ok("guard logged why it downgraded", b"not strict JSON" in proc.stderr,
       proc.stderr.decode("utf-8", "replace")[-160:])
    ok("poisoned reply produced exactly one line", written.count("\n") == 1, repr(written[:160]))
    ok("no bare Infinity on the wire", "Infinity" not in written, written[:160])
    payload = {}
    try:
        payload = strict(written)
        ok("client still gets a parseable answer", True)
    except ValueError as exc:
        ok("client still gets a parseable answer", False, "%s | %s" % (exc, written[:160]))
    ok("answered with a JSON-RPC error", payload.get("error", {}).get("code") == -32603, payload)
    ok("error keeps the request id", payload.get("id") == 7, payload)

    # ------------------------------------------------ oversized request line
    # A single enormous request must be refused without allocating it whole, and the
    # stream has to resync so the NEXT request still works.
    child2 = (
        "import io, json, os, sys\n"
        "sys.path.insert(0, os.environ['BRIDGE_DIR'])\n"
        "import server\n"
        "big = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list',"
        " 'pad': 'x' * 9000}) + chr(10)\n"
        "good = json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}) + chr(10)\n"
        "sys.stdin = io.TextIOWrapper(io.BytesIO((big + good).encode('utf-8')), encoding='utf-8')\n"
        "sys.exit(server.main() or 0)\n"
    )
    env2 = dict(env, ORIGIN_BRIDGE_MAX_LINE="4096")
    proc2 = subprocess.run([sys.executable, "-c", child2], capture_output=True, env=env2, timeout=120)
    lines2 = proc2.stdout.decode("utf-8", "replace").strip().splitlines()
    ok("oversized request produced two replies", len(lines2) == 2, len(lines2))
    first = {}
    try:
        first = strict(lines2[0])
    except Exception as exc:  # noqa: BLE001
        ok("refusal is parseable", False, exc)
    ok("oversized request refused with -32600",
       first.get("error", {}).get("code") == -32600, first)
    second = {}
    try:
        second = strict(lines2[1])
    except Exception:  # noqa: BLE001
        pass
    ok("stream resynced: the next request is answered",
       bool((second.get("result") or {}).get("tools")), second)

    # ------------------------------------------------ argument validation
    # A wrong-typed argument used to reach the engine and surface as
    # "origin_unavailable: TypeError", i.e. it blamed a missing Origin install.
    import server as srv

    def code_for(tool, args):
        try:
            srv._validate_args(tool, args, srv._BY_NAME[tool]["inputSchema"])
        except srv.originlab.OriginError as exc:
            return exc.code
        return "no-raise"

    ok("wrong-typed path is bad_arguments",
       code_for("origin_import", {"path": 123}) == "bad_arguments")
    ok("wrong-typed worksheet is bad_arguments",
       code_for("origin_plot", {"worksheet": ["ws-1"]}) == "bad_arguments")
    ok("boolean is not an integer",
       code_for("origin_export", {"width": True}) == "bad_arguments")
    ok("valid args pass", code_for("origin_import", {"path": "x.csv"}) == "no-raise")
    ok("extra keys are tolerated",
       code_for("origin_import", {"path": "x.csv", "bogus": 1}) == "no-raise")

    print("\n%d/%d json-safety checks passed" % (PASSED, PASSED + len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  FAILED:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
