"""Compatibility check with the official MCP SDK client.

smoke_test.py speaks raw JSON-RPC; this one proves a stock MCP client — the same
protocol dsh's @deepseek-ai/dsh-mcp-client speaks — can list and call our tools.

    pip install "mcp>=1.0"
    python client_check.py

The MCP SDK is NOT in requirements.txt: it is only needed by this check, and the
bridge itself deliberately speaks raw newline-delimited JSON-RPC (see server.py's
docstring for why). Calling this on a plain plugin environment used to raise a bare
ModuleNotFoundError traceback, so the import below explains itself instead.
"""

import asyncio
import json
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

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAVE_MCP = True
    MCP_IMPORT_ERROR = ""
except ImportError as _exc:                  # Python deletes the binding at block end
    HAVE_MCP = False
    MCP_IMPORT_ERROR = str(_exc)


async def main():
    if not HAVE_MCP:
        print("client_check.py needs the official MCP SDK, which is not a bridge "
              "dependency (the bridge talks raw JSON-RPC on purpose).", file=sys.stderr)
        print("  install it:  python -m pip install \"mcp>=1.0\"", file=sys.stderr)
        print("  crash was:   %s" % (MCP_IMPORT_ERROR,), file=sys.stderr)
        print("  smoke_test.py covers the same protocol ground without it.",
              file=sys.stderr)
        return 2

    failed = []

    def is_error(res):
        # The SDK renamed CallToolResult.isError to is_error somewhere in 1.x;
        # accept either so this works on old and new installs.
        return bool(getattr(res, "is_error", None) or getattr(res, "isError", False))

    def payload_of(res):
        # An error reply can carry empty content; json.loads("") would crash
        # with a message that hides the actual failure.
        text = "".join(c.text for c in res.content if c.type == "text")
        if not text.strip():
            return {"ok": False, "message": "<empty content> isError=%s" % is_error(res)}
        return json.loads(text)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-u", os.path.join(HERE, "server.py")],
        cwd=HERE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server      :", init.server_info.name, init.server_info.version)
            print("protocol    :", init.protocol_version)

            tools = (await session.list_tools()).tools
            print("tools       :", len(tools))
            for t in tools:
                print("   %-20s %s" % (t.name, t.description.split("。")[0][:60]))

            res = await session.call_tool("origin_status", {})
            payload = payload_of(res)
            ok = payload.get("connected") is True and not is_error(res)
            print("status call :", "connected" if ok else payload)
            if not ok:
                failed.append("status")

            if "--figure" in sys.argv:
                out = os.path.join(HERE, "out")
                os.makedirs(out, exist_ok=True)
                res = await session.call_tool("origin_figure", {
                    "source": os.path.join(HERE, "sample.dat"),
                    "x_title": "Time (s)", "y_title": "Signal (mV)",
                    "output_dir": out, "width": 1400,
                })
                payload = payload_of(res)
                if is_error(res) or not payload.get("ok"):
                    print("figure FAILED:", json.dumps(payload, ensure_ascii=False)[:900])
                    failed.append("figure")
                else:
                    print("figure      :", payload.get("ok"), payload.get("delivered"),
                          payload.get("duration_ms"), "ms")

    # A compatibility check that exits 0 on failure is useless in scripts.
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
