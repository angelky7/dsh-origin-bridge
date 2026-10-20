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

        ok("a .bin is still routed to binio, not refused",
           code_of(lambda: ol.read_file(os.path.join(
               os.path.dirname(os.path.abspath(__file__)), "fixtures", "not_a_bin.bin")))
           in ("parse_failed", "no-raise"), "binio path")

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
