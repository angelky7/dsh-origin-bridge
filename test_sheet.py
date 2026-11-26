"""Checks for origin_read_sheet: the read side of import.

Every other tool puts data INTO Origin, and the engine's own writes read a little
back as proof. This tool is the one that hands the numbers OUT again — a sheet that
arrived by origin_import, was computed by origin_formula, or came from the user's
own .opju — and it also proves the data file it writes.

Origin-free: the worksheet is a stub with the exact surface the engine touches
(`cols`, `rows`, `name`, `get_label`, `to_list`, `cell`), and `call()` runs the body
inline instead of on the COM worker. What is asserted is the decision logic and the
file bytes, not Origin's behaviour; the real Origin path is exercised by
`edge_checks.py` on a machine that has Origin.

Measured facts this suite pins down (originpro 1.1.15 / OriginPro 2024):
  * `to_list(col)` returns floats with `nan` for missing, or strings for text;
  * `cell(row, col)` returns the DISPLAY text, which is the only way to see a date
    as a date — and `as_date()/as_time()` destroy the column, so they are never used;
  * `expASC` cannot be driven through `lt_exec` at all (returns False, writes
    nothing), which is why this module writes the bytes itself.
"""

import csv
import hashlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 控制台代码页不是我们能选的：中文 Windows 默认 cp936，标签里有 µ 这样的字符。
# 一条检查都还没跑就 UnicodeEncodeError，等于把"你可以自己复跑"这条路堵死。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

import originlab  # noqa: E402

fails = []


def check(label, cond, got=""):
    if cond:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s  -> %r" % (label, got))
        fails.append(label)


class Restore(object):
    def __init__(self, module, **attrs):
        self.module = module
        self.new = attrs
        self.old = {}

    def __enter__(self):
        for key, value in self.new.items():
            self.old[key] = getattr(self.module, key)
            setattr(self.module, key, value)
        return self

    def __exit__(self, *_exc):
        for key, value in self.old.items():
            setattr(self.module, key, value)
        return False


def inline_call(fn, *args, **_options):
    """originlab.call's keyword options describe the worker, not the function."""
    return fn(*args)


class StubSheet(object):
    """The exact slice of WSheet the engine touches, with Origin's return shapes."""

    def __init__(self, columns, labels=None, units=None, displays=None,
                 name="Sheet1", declared_rows=None):
        self.columns = [list(c) for c in columns]
        self.cols = len(self.columns)
        self.rows = (max(len(c) for c in self.columns) if self.columns else 0) \
            if declared_rows is None else declared_rows
        self.name = name
        self._labels = list(labels or [""] * self.cols)
        self._units = list(units or [""] * self.cols)
        self._displays = list(displays or [""] * self.cols)

    def get_label(self, col, kind="L"):
        table = {"L": self._labels, "U": self._units, "D": self._displays}.get(kind, self._labels)
        return table[col] if 0 <= col < len(table) else ""

    def to_list(self, col):
        return list(self.columns[col])

    def cell(self, row, col):
        value = self.columns[col][row]
        if value is None or (isinstance(value, float) and value != value):
            return "--"
        if isinstance(value, float) and float(value).is_integer():
            return str(int(value))
        return str(value)


def bound(sheet):
    """Register the stub the way the engine hands handles out, and return the ref."""
    return originlab._bind("ws", sheet, sheet.name)


def call_sheet(sheet, *args, **kwargs):
    ref = bound(sheet)
    try:
        with Restore(originlab, call=inline_call):
            return originlab.read_sheet(ref, *args, **kwargs)
    finally:
        originlab._handles.pop(ref, None)
        originlab._names.pop(ref, None)


def close_to(a, b, tol=1e-12):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tol


# ------------------------------------------------------------- cell text


def test_cell_text():
    cases = [
        (1.0, "1"), (1.5, "1.5"), (0.0, "0"), (-2.0, "-2"),
        (0.1 + 0.2, repr(0.1 + 0.2)), (1e20, repr(1e20)),
        (float("nan"), ""), (float("inf"), ""), (float("-inf"), ""),
        (None, ""), ("007", "007"), ("a,b", "a,b"),
        (12345678901234567890.0, repr(12345678901234567890.0)),
    ]
    bad = [(v, originlab._cell_text(v), want) for v, want in cases
           if originlab._cell_text(v) != want]
    check("整数值不写成 1.0", not any(v == 1.0 for v, _g, _w in bad),
          [b for b in bad if b[0] == 1.0])
    check("缺值与 inf 写成空而不是 nan", not any(
        (isinstance(v, float) and v != v) or v in (float("inf"), float("-inf"))
        for v, _g, _w in bad), bad)
    check("文本列原样保留（007 不丢前导零）", originlab._cell_text("007") == "007")
    check("浮点按 repr 保留可回读精度", originlab._cell_text(0.1 + 0.2) == "0.30000000000000004")
    check("其余 cell_text 用例全部符合", bad == [], bad)


def test_column_kind():
    check("全是数字 -> numeric", originlab._column_kind([1.0, 2.0]) == "numeric")
    check("全是文本 -> text", originlab._column_kind(["a", "b"]) == "text")
    check("数字与文本混排 -> mixed", originlab._column_kind([1.0, "a"]) == "mixed")
    check("只有 nan 也算空列", originlab._column_kind([float("nan"), None]) == "empty")
    check("空列表 -> empty", originlab._column_kind([]) == "empty")
    check("bool 不算数字", originlab._column_kind([True, False]) == "empty")


def test_unique_headers():
    names, notes = originlab._unique_headers(["V", "V", "", "V"], 4)
    check("重复的长名被改成唯一表头", names == ["V", "V_2", "col3", "V_3"], names)
    check("改名这件事会说出来", len(notes) == 2, notes)
    names, notes = originlab._unique_headers([], 2)
    check("没有长名时用 col1/col2", names == ["col1", "col2"], names)
    check("没有改名就不该有提示", notes == [], notes)


# --------------------------------------------------------- file verification


def write_csv(path, header, rows, delim=","):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=delim, lineterminator="\r\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def test_check_data_file(tmp):
    good = os.path.join(tmp, "good.csv")
    write_csv(good, ["V", "I"], [["1", "2"], ["3", "4"]])
    count, problems = originlab._check_data_file(good, "utf-8-sig", ",", ["V", "I"], 2,
                                                 [(0, 0, "1"), (1, 1, "4")])
    check("写得对的文件回读无问题", count == 2 and problems == [], (count, problems))

    short = os.path.join(tmp, "short.csv")
    write_csv(short, ["V", "I"], [["1", "2"]])
    count, problems = originlab._check_data_file(short, "utf-8-sig", ",", ["V", "I"], 2, [])
    check("少一行能被抓到", any("应为 2" in p for p in problems), problems)

    narrow = os.path.join(tmp, "narrow.csv")
    write_csv(narrow, ["V", "I"], [["1", "2"], ["3"]])
    count, problems = originlab._check_data_file(narrow, "utf-8-sig", ",", ["V", "I"], 2, [])
    check("某行少一格能被抓到", any("格" in p for p in problems), problems)

    wronghead = os.path.join(tmp, "wronghead.csv")
    write_csv(wronghead, ["X", "I"], [["1", "2"]])
    count, problems = originlab._check_data_file(wronghead, "utf-8-sig", ",", ["V", "I"], 2, [])
    check("表头对不上能被抓到", any("表头" in p for p in problems), problems)

    wrongcell = os.path.join(tmp, "wrongcell.csv")
    write_csv(wrongcell, ["V", "I"], [["9", "2"], ["3", "4"]])
    count, problems = originlab._check_data_file(wrongcell, "utf-8-sig", ",", ["V", "I"], 2,
                                                 [(0, 0, "1")])
    check("抽查格子对不上能被抓到", any("内存里" in p for p in problems), problems)

    empty = os.path.join(tmp, "empty.csv")
    open(empty, "w").close()
    count, problems = originlab._check_data_file(empty, "utf-8-sig", ",", ["V"], 0, [])
    check("空文件被当成问题而不是通过", any("空" in p for p in problems), problems)


# ------------------------------------------------------------ argument checks


def raises(code, fn):
    try:
        fn()
    except originlab.OriginError as exc:
        return exc.code
    return "no-raise"


def test_arguments(tmp):
    sheet = StubSheet([[1.0, 2.0], [3.0, 4.0]], labels=["V", "I"])
    check("未知句柄 -> worksheet_not_found",
          raises("worksheet_not_found", lambda: originlab.read_sheet("ws-999")) ==
          "worksheet_not_found")
    for bad, want in ((lambda: call_sheet(sheet, fmt="xlsx"), "bad_format"),
                      (lambda: call_sheet(sheet, encoding="latin-1"), "bad_encoding"),
                      (lambda: call_sheet(sheet, rows=0), "bad_arguments"),
                      (lambda: call_sheet(sheet, rows=999), "bad_arguments"),
                      (lambda: call_sheet(sheet, rows="many"), "bad_arguments")):
        check("%s 被明确拒绝" % want, raises(want, bad) == want, raises(want, bad))
    check("列名不存在 -> bad_column",
          raises("bad_column", lambda: call_sheet(sheet, columns=["nope"])) == "bad_column")
    check("列号从 1 开始",
          raises("bad_column", lambda: call_sheet(sheet, columns=[0])) == "bad_column")


# -------------------------------------------------------------- reading back


def test_read(tmp):
    sheet = StubSheet([[1.0, 2.0, 3.0], [0.5, float("nan"), 1.5], ["a", "b", "c"]],
                      labels=["Voltage", "Current", "Name"], units=["V", "A", ""])
    out = call_sheet(sheet, rows=2)
    check("读回行数与列数", out["rows"] == 3 and out["cols"] == 3, (out["rows"], out["cols"]))
    check("样本只取 rows 行", out["sample"]["Voltage"] == [1.0, 2.0], out["sample"])
    check("缺值给 null 而不是 nan", out["sample"]["Current"] == [0.5, None], out["sample"])
    check("文本列原样", out["sample"]["Name"] == ["a", "b"], out["sample"])
    kinds = {c["name"]: c["kind"] for c in out["columns"]}
    check("列类型逐列判定", kinds == {"Voltage": "numeric", "Current": "numeric",
                                      "Name": "text"}, kinds)
    filled = {c["name"]: c["filled"] for c in out["columns"]}
    check("有效行数把缺值排除了", filled["Current"] == 2, filled)
    check("单位随列返回", out["columns"][0]["units"] == "V", out["columns"][0])
    check("句柄能继续传下去", str(out["worksheet"]).startswith("ws-"), out["worksheet"])
    check("没给 path 时说明只读了样本", "path" in (out.get("note") or ""), out.get("note"))
    check("没给 path 就没有 exported", "exported" not in out, out.get("exported"))

    few = call_sheet(StubSheet([[1.0], [2.0]], labels=["A", "B"]), rows=50)
    check("行数少于请求时按实际返回", few["sample_rows"] == 1, few["sample_rows"])

    only = call_sheet(StubSheet([[1.0, 2.0], [3.0, 4.0]], labels=["V", "I"]), columns=["I"])
    check("columns 只读指定列", list(only["sample"].keys()) == ["I"], only["sample"])
    check("只读一列时列数是 1", only["cols"] == 1, only["cols"])


def test_read_edge_shapes(tmp):
    dup = call_sheet(StubSheet([[1.0], [2.0]], labels=["V", "V"]))
    check("重名列被改名", [c["name"] for c in dup["columns"]] == ["V", "V_2"], dup["columns"])
    check("改名有警告", any("同为" in w for w in dup["warnings"]), dup["warnings"])

    ragged = StubSheet([[1.0, 2.0, 3.0], [1.0]], labels=["A", "B"])
    out = call_sheet(ragged)
    check("长短不齐时按最长的列定行数", out["rows"] == 3, out["rows"])
    check("长短不齐会点名哪几列补空",
          any("B(1/3)" in w for w in out["warnings"]), out["warnings"])

    declared = StubSheet([[1.0, 2.0]], labels=["A"], declared_rows=9)
    out = call_sheet(declared)
    check("空行按工作表报告的行数照实写出",
          out["rows"] == 9 and any("后 7 行是空行" in w for w in out["warnings"]),
          (out["rows"], out["warnings"]))

    empty_col = StubSheet([[1.0], [float("nan")]], labels=["A", "B"])
    out = call_sheet(empty_col)
    check("空列被判成 empty", out["columns"][1]["kind"] == "empty", out["columns"][1])
    check("空列也会被点名警告", any("一行有效值都没有" in w for w in out["warnings"]),
          out["warnings"])

    dated = StubSheet([[45292.0, 45293.5], [1.0, 2.0]], labels=["Stamp", "I"],
                      displays=["yyyy-MM-dd", ""])
    out = call_sheet(dated)
    check("带显示格式的列会警告导出的是序列号",
          any("45292.0 = 2024-01-01" in w for w in out["warnings"]), out["warnings"])
    check("显示格式随列返回", out["columns"][0]["display"] == "yyyy-MM-dd", out["columns"][0])
    check("sample_display 给出屏幕上的文字",
          out.get("sample_display", {}).get("Stamp", {}).get("1") == "45292",
          out.get("sample_display"))


# ---------------------------------------------------------------- writing out


def test_export(tmp):
    sheet = StubSheet([[1.0, 2.0, 3.0], [0.5, float("nan"), 1.5], ["a,b", "c", "d"]],
                      labels=["Voltage", "Current", "Name"], units=["V", "A", ""])
    target = os.path.join(tmp, "out.csv")
    out = call_sheet(sheet, rows=1, path=target)
    exported = out.get("exported") or {}
    check("文件真的写出来了", os.path.isfile(target), exported)
    check("回读核对通过", exported.get("verified") is True, exported)
    check("回读行数与工作表一致", exported.get("rows_written") == 3, exported)
    check("字节数与磁盘一致", exported.get("bytes") == os.path.getsize(target), exported)
    check("sha256 与磁盘一致",
          exported.get("sha256") == hashlib.sha256(open(target, "rb").read()).hexdigest(),
          exported)
    check("路径与请求一致", exported.get("path") == target, exported)

    raw = open(target, "rb").read()
    check("默认编码带 BOM（Excel 双击不乱码）", raw.startswith(b"\xef\xbb\xbf"), raw[:6])
    text = raw.decode("utf-8-sig")
    lines = text.split("\r\n")
    check("表头带单位", lines[0].startswith("Voltage [V],Current [A],Name"), lines[0])
    check("缺值写成空格而不是 nan", lines[2].split(",")[1] == "", lines[2])
    check("逗号在单元格里被引号包住", '"a,b"' in lines[1], lines[1])
    check("行尾是 CRLF（Excel 友好）", b"\r\n" in raw, raw[:80])

    tsv = os.path.join(tmp, "out.tsv")
    out = call_sheet(sheet, path=tsv, fmt="tsv")
    check("tsv 用制表符分隔", "\t" in open(tsv, encoding="utf-8-sig").read(),
          open(tsv, encoding="utf-8-sig").read()[:60])
    check("tsv 的回读也通过", (out.get("exported") or {}).get("verified") is True,
          out.get("exported"))

    plain = os.path.join(tmp, "noext")
    out = call_sheet(sheet, path=plain, encoding="utf-8")
    got = (out.get("exported") or {}).get("path", "")
    check("没写扩展名时按 fmt 补上", got == plain + ".csv", got)
    check("utf-8 的写法不带 BOM",
          not open(got, "rb").read().startswith(b"\xef\xbb\xbf"), open(got, "rb").read()[:6])

    gbk = os.path.join(tmp, "gbk.csv")
    out = call_sheet(sheet, path=gbk, encoding="gbk")
    check("gbk 真的按 gbk 写", (out.get("exported") or {}).get("encoding") == "gbk",
          out.get("exported"))
    check("gbk 文件回读也通过", (out.get("exported") or {}).get("verified") is True,
          out.get("exported"))

    empty = os.path.join(tmp, "empty_sheet.csv")
    out = call_sheet(StubSheet([[]], labels=["A"]), path=empty)
    exported = out.get("exported") or {}
    check("零行工作表只写表头且不报成功导了数据",
          exported.get("rows_written") == 0 and exported.get("verified") is True, exported)


def test_export_refuses(tmp):
    sheet = StubSheet([[1.0]], labels=["A"])
    directory = os.path.join(tmp, "adir")
    os.makedirs(directory, exist_ok=True)
    check("path 指向目录 -> bad_path",
          raises("bad_path", lambda: call_sheet(sheet, path=directory)) == "bad_path")
    check("path 没有真文件名 -> bad_path",
          raises("bad_path", lambda: call_sheet(sheet, path=os.path.join(tmp, ".csv")))
          == "bad_path")

    locked = os.path.join(tmp, "locked.csv")
    with open(locked, "w") as fh:
        fh.write("x")
    try:
        os.chmod(locked, 0o444)
        readonly = raises("target_locked", lambda: call_sheet(sheet, path=locked))
    finally:
        try:
            os.chmod(locked, 0o666)
        except OSError:
            pass
    check("目标文件只读 -> target_locked（Windows 上可能仍可写，不判失败）",
          readonly in ("target_locked", "no-raise"), readonly)

    # A relative path resolves against the process CWD, so run it from a scratch
    # directory instead of littering the checkout (the resolver is what is under test,
    # not where the repository happens to live).
    here = os.getcwd()
    try:
        os.chdir(tmp)
        rel = call_sheet(sheet, path="relative_sub.csv")
        wrote = os.path.isfile(os.path.join(tmp, "relative_sub.csv"))
    finally:
        os.chdir(here)
    check("相对路径会被解析并给出提示",
          any("相对路径" in w for w in (rel.get("warnings") or [])), rel.get("warnings"))
    check("相对路径落在 CWD 上", wrote)


def main():
    tmp = tempfile.mkdtemp(prefix="origin_bridge_sheet_")
    try:
        test_cell_text()
        test_column_kind()
        test_unique_headers()
        test_check_data_file(tmp)
        test_arguments(tmp)
        test_read(tmp)
        test_read_edge_shapes(tmp)
        test_export(tmp)
        test_export_refuses(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print("\n%d FAILED: %s" % (len(fails), ", ".join(fails)))
        return 1
    print("\nall read-sheet checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())