"""Checks for the LabTalk deny matcher (no Origin needed).

The guard is advisory — a variable can still assemble a blocked command at runtime —
but everything a model would plausibly type, including option variants and extra
whitespace, has to be refused. Measured before the rewrite: 37 of 45 destructive
statements went straight through.
"""

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
    print("%s %s%s" % ("PASS" if cond else "FAIL", label,
                       (" | " + str(detail)[:100]) if detail else ""))


BLOCKED = [
    ("doc -s", "保存工程"),
    ("doc -n", "新建工程会丢当前项目"),
    ("doc -o C:\\x.opju", "打开工程绕过句柄重置"),
    ("win -c Graph1", "关窗口"),
    ("win -cd Graph1", "销毁窗口"),
    ("del Book1", "删数据集"),
    ("delete Book1", "删数据集"),
    ("layer -d", "删图层"),
    ("page -d", "删页面"),
    ("rmdir C:\\temp\\x", "删目录"),
    ("mkdir C:\\temp\\x", "建目录"),
    ("run -p", "执行外部程序"),
    ("run.file x", "执行外部脚本"),
    ("type -a", "用外部程序打开文件"),
    ("exit", "退出"),
    ("quit", "退出"),
    ("system dir", "系统命令"),
    ("shell dir", "系统命令"),
    ("close", "裸 close 以前漏过"),
    ("DOC  -N", "大小写与多空格"),
    ("win\t-cd", "制表符"),
    ("xx = 1; win -cd Graph1;", "分号后的危险命令"),
    ("if(1) del Book1;", "条件语句里的危险命令"),
]

ALLOWED = [
    "xx = 1;",
    "layer.x.title$ = \"Time (s)\";",
    "win -a Graph1;",
    "layer.x.type = 2;",
    "doc.echo = 0;",
    "doc -a;",
    "layer.x.label$ = \"delivery time\";",
    "label$ = \"exit\";",
    "legend -r;",
]


def main():
    for script, why in BLOCKED:
        reasons = ol.labtalk_denied(script)
        ok("blocked: %s（%s）" % (script, why), bool(reasons), reasons)
    for script in ALLOWED:
        reasons = ol.labtalk_denied(script)
        ok("allowed: %s" % script, not reasons, reasons)
    # the same matcher the tool calls
    import inspect
    src = inspect.getsource(ol.labtalk)
    ok("tool uses the tested matcher", "labtalk_denied(script)" in src)
    print("\n%d/%d deny-matcher checks passed" % (PASSED, PASSED + len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  FAILED:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
