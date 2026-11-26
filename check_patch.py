"""Hold cordis.patch.yml to the two things that silently break an install.

    python check_patch.py            # exit 1 on any problem
    python check_patch.py path/to/cordis.patch.yml

Why it exists (both measured 2026-11-26 against dsh 0.2.2-rc.5):

* The server.py path used to be built from a literal `profiles/desktop/...`. Install
  the plugin into any other profile (`web`, a custom one) and python cannot open the
  file, `failOnStartupError: false` downgrades that to a log line the UI never shows,
  dsh starts fine — and the session has zero origin_* tools while the plugin card says
  "installed". The path has to come from the host: `ctx.get('profileContext').dir`.
  (`process.env.DSH_PROFILE` / `DSH_HOME` are not set for the host process by dsh, so
  they cannot be the primary source.)

* Both insert entries must be present: `mcp-origin-bridge` is what registers the
  tools, `dsh-origin-bridge` is what makes the plugin show up as its own card.

The loader tolerates `!!js` (dsh registers that tag itself; plain PyYAML refuses it).
"""

import io
import os
import sys

try:
    import yaml
except ImportError:                       # pragma: no cover - install hint
    sys.exit("check_patch.py needs PyYAML: python -m pip install pyyaml")


class Tolerant(yaml.SafeLoader):
    """dsh supports !!js expressions in patch files; do not choke on them."""


Tolerant.add_constructor("tag:yaml.org,2002:js",
                         lambda loader, node: loader.construct_scalar(node))


def load_inserts(path):
    with io.open(path, encoding="utf-8") as fh:
        text = fh.read()
    data = yaml.load(io.StringIO(text), Loader=Tolerant)
    if not isinstance(data, list):
        raise ValueError("%s is not a YAML list (got %r)" % (path, type(data).__name__))
    return [entry for block in data if isinstance(block, dict) and "insert" in block
            for entry in (block["insert"] or [])]


def main(argv):
    path = argv[1] if len(argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "cordis.patch.yml")
    if not os.path.isfile(path):
        print("FAIL: patch file not found:", path)
        return 1
    try:
        entries = load_inserts(path)
    except Exception as exc:              # noqa: BLE001 - any parse failure is a fail
        print("FAIL: %s does not parse: %s" % (path, exc))
        return 1
    ids = [e.get("id") for e in entries]
    tools = next((e for e in entries if e.get("id") == "mcp-origin-bridge"), None)
    args = " ".join(str(a) for a in (
        (tools or {}).get("config", {}).get("args") or []))
    problems = []
    for want in ("mcp-origin-bridge", "dsh-origin-bridge"):
        if want not in ids:
            problems.append("the insert for %s is missing" % want)
    if tools is None:
        problems.append("no entry registers the tools")
    else:
        if "profileContext" not in args:
            problems.append("the server.py path does not come from ctx.get('profileContext') "
                            "— a literal profile name breaks every profile but that one")
        for literal in ("profiles/desktop", "profiles\\\\desktop"):
            if literal in args:
                problems.append("the server.py path hardcodes %r" % literal)
    print("inserted ids:", ids)
    if args:
        print("server.py arg:", args[-140:])
    for problem in problems:
        print("FAIL:", problem)
    if problems:
        return 1
    print("patch looks installable for any profile")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))