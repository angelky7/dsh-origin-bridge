"""Offline checks that the bridge can never emit non-standard JSON.

json.dumps writes a bare Infinity / NaN by default, which RFC 8259 does not allow:
a strict client (dsh, JS JSON.parse) fails on the whole response, so one overflowed
number looks like "the bridge is dead". Two guards, both checked here.
"""

import json
import math
import os
import sys

# 控制台代码页不是我们能选的：中文 Windows 默认 cp936、西文 cp1252，而检查标签里
# 有 µ、°、≤ 和中文。一条检查都还没跑就 UnicodeEncodeError，等于把"你可以自己复跑"
# 这条路堵死——所以先把两个输出流设成"写不出来的字符用 ? 代替"，而不是崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

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
    # An unrecognised key is refused instead of being swallowed: measured, a model
    # sending {"colour": "#fff", "xscale": "log"} to origin_style was accepted and
    # changed nothing at all, which looks like a successful call.
    ok("unknown key -> unknown_arguments",
       code_for("origin_import", {"path": "x.csv", "bogus": 1}) == "unknown_arguments")
    ok("misspelled style key is refused",
       code_for("origin_style", {"graph": "gr-1", "colour": "#fff"}) == "unknown_arguments")
    ok("every tool sets additionalProperties=false",
       all((t["inputSchema"] or {}).get("additionalProperties") is False for t in srv.TOOLS),
       [t["name"] for t in srv.TOOLS
        if (t["inputSchema"] or {}).get("additionalProperties") is not False])

    # ------------------------------------------------ schema enums vs the engine
    # Two hand-typed lists drifted: the engine accepted gaussian / nlfit /
    # natural_log / workbook / tiff / jpeg while the schemas did not list them. The
    # enums are now derived from the engine's own tables, and this checks it.
    import originlab as engine

    def enum_of(tool, key):
        spec = (srv._BY_NAME[tool]["inputSchema"]["properties"] or {}).get(key) or {}
        return set(spec.get("enum") or ())

    ok("fit kinds cover every preset plus the two modes",
       set(engine.FIT_PRESETS) | {"linear", "nlfitsing", "nlfit"}
       <= enum_of("origin_fit", "kind"), sorted(enum_of("origin_fit", "kind")))
    ok("axis scale names cover SCALE_TYPES",
       set(engine.SCALE_TYPES) <= enum_of("origin_style", "x_scale"),
       sorted(enum_of("origin_style", "x_scale")))
    ok("page kinds cover PAGE_KINDS",
       set(engine.PAGE_KINDS) <= enum_of("origin_close_pages", "kind"),
       sorted(enum_of("origin_close_pages", "kind")))
    ok("plot types cover PLOT_CODES",
       set(engine.PLOT_CODES) == enum_of("origin_plot", "plot_type"),
       sorted(enum_of("origin_plot", "plot_type")))
    ok("image formats include the tiff/jpeg aliases the engine accepts",
       {"tiff", "jpeg"} <= enum_of("origin_export", "fmt"),
       sorted(enum_of("origin_export", "fmt")))
    ok("data formats cover _DATA_FMTS",
       set(engine._DATA_FMTS) == enum_of("origin_read_sheet", "fmt"),
       sorted(enum_of("origin_read_sheet", "fmt")))
    ok("encodings cover _DATA_ENCODINGS",
       set(engine._DATA_ENCODINGS) == enum_of("origin_read_sheet", "encoding"),
       sorted(enum_of("origin_read_sheet", "encoding")))

    # -------------------------------- schema properties vs the engine's signatures
    # A parameter the schema declares but the engine will not accept (or the other way
    # round) is invisible drift: the model can never use it, or is always refused.
    import inspect

    SIGNATURES = {
        "origin_status": "status", "origin_import": "import_file",
        "origin_read_file": "read_file", "origin_read_sheet": "read_sheet",
        "origin_write": None, "origin_formula": "column_formula",
        "origin_plot": "plot", "origin_style": "style", "origin_annotate": "annotate",
        "origin_legend": "legend_entries", "origin_layer": "add_layer",
        "origin_matrix": "matrix_plot", "origin_fit": "fit", "origin_export": None,
        "origin_view": "view", "origin_save_project": "save_project",
        "origin_open": "open_project", "origin_inspect": "inspect",
        "origin_close_pages": "close_pages", "origin_labtalk": "labtalk",
        "origin_exit": "exit_origin", "origin_instances": "instances",
        "origin_reclaim": "reclaim", "origin_figure": "figure",
        "origin_chart_presets": "chart_presets", "origin_chart_preset": "chart_preset",
        "origin_chart_check": "chart_check", "origin_chart_render": "chart_render",
    }
    drift = []
    for tool, fn_name in SIGNATURES.items():
        if fn_name is None:
            continue
        fn = getattr(engine, fn_name)
        params = set(inspect.signature(fn).parameters)
        # Underscore-prefixed parameters are internal switches (reclaim's _detach),
        # not part of the model-facing contract.
        params = {p for p in params if not p.startswith("_")}
        declared = set((srv._BY_NAME[tool]["inputSchema"]["properties"] or {}))
        if not declared <= params:
            drift.append("%s: schema declares %s" % (tool, sorted(declared - params)))
        if not params <= declared:
            drift.append("%s: engine accepts %s" % (tool, sorted(params - declared)))
    ok("every schema property maps onto an engine parameter (and back)", drift == [], drift)

    # ------------------------------------------------ unknown tool / method
    # A model hallucinating a tool name gets a named, actionable error — not a
    # crash, not a silent null. Driven through the real stdio loop.
    child3 = (
        "import io, json, os, sys\n"
        "sys.path.insert(0, os.environ['BRIDGE_DIR'])\n"
        "import server\n"
        "a = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',\n"
        "     'params': {'name': 'origin_frobnicate', 'arguments': {}}}\n"
        "b = {'jsonrpc': '2.0', 'id': 2, 'method': 'no/such/method'}\n"
        "reqs = json.dumps(a) + chr(10) + json.dumps(b) + chr(10)\n"
        "sys.stdin = io.TextIOWrapper(io.BytesIO(reqs.encode('utf-8')), encoding='utf-8')\n"
        "sys.exit(server.main() or 0)\n"
    )
    proc3 = subprocess.run([sys.executable, "-c", child3], capture_output=True, env=env, timeout=120)
    lines3 = proc3.stdout.decode("utf-8", "replace").strip().splitlines()
    ok("unknown tool + unknown method answered", len(lines3) == 2, len(lines3))
    r1, r2 = {}, {}
    try:
        r1, r2 = strict(lines3[0]), strict(lines3[1])
    except Exception as exc:  # noqa: BLE001
        ok("both replies parseable", False, exc)
    res1 = (r1.get("result") or {})
    body1 = {}
    try:
        body1 = json.loads(res1.get("content", [{}])[0].get("text", "{}"))
    except ValueError:
        pass
    ok("unknown tool -> isError with code unknown_tool",
       res1.get("isError") is True and body1.get("code") == "unknown_tool",
       (res1, body1))
    ok("unknown method -> JSON-RPC -32601",
       (r2.get("error") or {}).get("code") == -32601, r2)

    # ------------------------------- the tool payload is its own JSON document
    # The envelope check above could not see this one: the payload is embedded as a
    # *string* in content[0].text, so allow_nan=False in _encode_reply never looked
    # at the numbers inside it. A client that parses `text` failed on the whole
    # tool result while the envelope stayed perfectly legal.
    poisoned = srv._tool_result(1, {"ok": True, "v": math.nan, "nested": {"w": INF},
                                    "z": [1.0, -INF]}, False)
    blob = srv._encode_reply(poisoned).decode("utf-8")
    envelope = strict(blob)
    text = envelope["result"]["content"][0]["text"]
    try:
        body = strict(text)
        payload_ok = True
    except ValueError as exc:
        body, payload_ok = {}, exc
    ok("tool payload text is strict JSON", payload_ok is True, payload_ok)
    ok("non-finite numbers are folded to null",
       body.get("v") is None and body.get("z") == [1.0, None], body)
    ok("the payload says what it dropped",
       any("非 JSON 数值" in w for w in body.get("warnings") or []), body.get("warnings"))

    # ------------------------------------------------ null vs missing argument
    # `path: null` used to slip through (the validator skipped None wholesale), then
    # os.path.abspath(None) raised TypeError -> "origin_unavailable / the COM channel
    # is damaged" for a tool that never touches Origin, plus the suspect flag forcing
    # a reset of a healthy session.
    ok("explicit null on a required string is bad_arguments",
       code_for("origin_import", {"path": None}) == "bad_arguments")
    before_suspect = srv.originlab._suspect
    srv._handle({"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                 "params": {"name": "origin_read_file", "arguments": {"path": None}}})
    ok("a null argument does not poison the COM channel",
       srv.originlab._suspect == before_suspect, srv.originlab._suspect)

    # ------------------------------------------------ notifications are silent
    ran = []
    real_status = srv._BY_NAME["origin_status"]["run"]
    srv._BY_NAME["origin_status"]["run"] = lambda _a: ran.append(1) or {"ok": True}
    try:
        silent = srv._handle({"jsonrpc": "2.0", "method": "tools/call",
                              "params": {"name": "origin_status", "arguments": {}}})
    finally:
        srv._BY_NAME["origin_status"]["run"] = real_status
    ok("an id-less tools/call gets no reply", silent is None, silent)
    ok("an id-less tools/call does not run the tool", ran == [], ran)

    # A message without a method is an Invalid Request, not silence and not
    # "method not found: None".
    no_method = srv._handle({"jsonrpc": "2.0", "id": 55})
    ok("a message without method -> -32600",
       (no_method.get("error") or {}).get("code") == -32600, no_method)
    ok("the -32600 answer keeps the id", no_method.get("id") == 55, no_method)

    # ------------------------------------------------ integral floats as integers
    # JSON Schema counts 1600.0 as an integer; a model computing a width sends one.
    ok("1600.0 is accepted as an integer", code_for("origin_export", {"width": 1600.0}) == "no-raise")
    ok("1600.5 is still rejected", code_for("origin_export", {"width": 1600.5}) == "bad_arguments")

    # ------------------------------------------------ --tools under a legacy console
    # print() used sys.stdout's locale code page: cp1252 either mojibaked a UTF-8
    # consumer or died with UnicodeEncodeError and exit 1 — and --tools is exactly
    # the command CI and the README tell people to run.
    env3 = dict(env, PYTHONIOENCODING="cp1252")
    proc4 = subprocess.run([sys.executable, os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "server.py"), "--tools"],
        capture_output=True, env=env3, timeout=120)
    raw = proc4.stdout
    try:
        decoded = raw.decode("utf-8")
        utf8_ok = True
    except UnicodeDecodeError as exc:
        decoded, utf8_ok = "", exc
    ok("--tools survives a cp1252 stdout", proc4.returncode == 0,
       proc4.stderr.decode("utf-8", "replace")[-160:])
    ok("--tools output is UTF-8 regardless of the console",
       utf8_ok is True and "origin_status" in decoded, utf8_ok)
    ok("--tools still lists exactly 28 tools",
       len([ln for ln in decoded.splitlines() if ln.strip()]) == 28,
       len(decoded.splitlines()))

    # ------------------------------------------------ suites survive a legacy console
    # The suites print µ / ° / 中文 in their labels. A zh-CN Windows console is cp936,
    # so `python test_asciiio.py` -- the exact command both READMEs tell people to
    # run -- died with UnicodeEncodeError on the FIRST check and exited 1, which
    # reads as "the shipped checks are broken". Measured on 0.4.19 and 0.4.20 alike:
    # 'gbk' codec can't encode character '\xb5'. Every suite now relaxes its own
    # stdout/stderr, so the documented command works on the default console.
    env_cp = dict(env, PYTHONIOENCODING="cp936")
    env_cp.pop("PYTHONUTF8", None)
    suite = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_asciiio.py")
    proc5 = subprocess.run([sys.executable, suite], capture_output=True, env=env_cp, timeout=600)
    tail5 = proc5.stdout.decode("utf-8", "replace").strip().splitlines()
    ok("a suite that prints µ runs on a cp936 console", proc5.returncode == 0,
       proc5.stderr.decode("utf-8", "replace")[-200:])
    ok("and still reports its result there",
       bool(tail5) and "asciiio checks passed" in tail5[-1], tail5[-1] if tail5 else "")

    print("\n%d/%d json-safety checks passed" % (PASSED, PASSED + len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  FAILED:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
