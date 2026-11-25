"""Offline checks for origin_read_file's shaping (no Origin needed).

Two behaviours that cost a real debugging cycle:
- a `.xlsx` used to be decoded as text and reported as "找不到数值数据行（只有表头？）",
  which blames the user's file for our mistake;
- the response used to carry no values at all, so a model could not tell a correctly
  parsed column from one whose values slid into the neighbouring column.
"""

import os
import shutil
import sys

# 控制台代码页不是我们能选的：中文 Windows 默认 cp936、西文 cp1252，而检查标签里
# 有 µ、°、≤ 和中文。一条检查都还没跑就 UnicodeEncodeError，等于把"你可以自己复跑"
# 这条路堵死——所以先把两个输出流设成"写不出来的字符用 ? 代替"，而不是崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
import tempfile

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
                       (" | " + str(detail)[:110]) if detail else ""))


def write(dirname, name, text, encoding="utf-8"):
    path = os.path.join(dirname, name)
    with open(path, "w", encoding=encoding, newline="") as fh:
        fh.write(text)
    return path


def code_of(fn):
    try:
        fn()
    except ol.OriginError as exc:
        return exc.code
    except Exception as exc:  # noqa: BLE001 — an unwrapped crash is the failure
        return "UNWRAPPED:" + type(exc).__name__
    return "no-raise"


def main():
    work = tempfile.mkdtemp(prefix="readfile_checks_")
    try:
        csv = write(work, "a.csv", "x,y\n1,2\n3,\n5,6\n")
        res = ol.read_file(csv)
        ok("csv parses", res.get("rows") == 3, res.get("rows"))
        ok("every column carries a 3-value sample",
           [c["sample"] for c in res["columns"]] == [[1.0, 3.0, 5.0], [2.0, None, 6.0]],
           [c["sample"] for c in res["columns"]])
        ok("sample keeps nulls so a gap is visible", None in res["columns"][1]["sample"])
        ok("sample is explained", "原始值" in (res.get("sample_note") or ""),
           res.get("sample_note"))
        ok("full values still stay out of the payload",
           all("values" not in c for c in res["columns"]))

        # the binary-spreadsheet guard
        for name, head in (("b.xlsx", b"PK\x03\x04"), ("c.xls", b"\xd0\xcf\x11\xe0"),
                           ("d.wks", b"\x00\x00\x02\x00")):
            path = os.path.join(work, name)
            with open(path, "wb") as fh:
                fh.write(head + b"\x00" * 64)
            ok("%s is refused as a binary sheet" % name,
               code_of(lambda p=path: ol.read_file(p)) == "unsupported_file_type",
               code_of(lambda p=path: ol.read_file(p)))

        # and the guard says what to do instead, with the file name in the message
        path = os.path.join(work, "b.xlsx")
        try:
            ol.read_file(path)
        except ol.OriginError as exc:
            ok("message names the format", ".xlsx" in exc.message, exc.message)
            ok("next action points at origin_import",
               any("origin_import" in a for a in exc.next_actions), exc.next_actions)

        # import_file must still accept Excel: only read_file refuses it
        import inspect
        src = inspect.getsource(ol.import_file)
        ok("import_file still allows .xlsx", "二进制表格" not in src)

        # 这个 fixture 必须自建：它本来是 test_binio.py 的产物，又写在 .gitignore 里，
        # 所以全新解包（或先跑本文件、README 就是邀请用户单独跑 test_readfile.py）时它
        # 不存在——这一项会假红成“a .bin is still routed to binio, not refused”，
        # 把“路由没被拒绝”误报成失败。内容与 test_binio.py 写的一致：只要有 .bin 后缀、
        # 会被 binio 接手就够；每次覆盖写，重复跑同一份状态，避免上次留下的怪文件干扰。
        bin_fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "fixtures", "not_a_bin.bin")
        os.makedirs(os.path.dirname(bin_fixture), exist_ok=True)
        with open(bin_fixture, "wb") as fh:
            fh.write(b"MZ\x90\x00" + b"\x00" * 2000)

        ok("a .bin is still routed to binio, not refused",
           code_of(lambda: ol.read_file(bin_fixture)) in ("parse_failed", "no-raise"), "binio path")

        print("\n%d/%d read_file checks passed" % (PASSED, PASSED + len(FAILED)))
        if FAILED:
            for f in FAILED:
                print("  FAILED:", f)
            return 1
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
