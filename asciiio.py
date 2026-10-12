"""Robust ASCII data reader for Origin-bound scientific files.

Why not just originpro's from_file: Origin's ASCII import is fussy about
multi-row headers, '#'/'//' comment lines and space-aligned columns, and it
fails by producing an empty sheet rather than an error. Why not the original
the old read_dat: it splits on TAB only, so space- or comma-aligned .dat
files silently turn into a single NaN column.

This reader sniffs the delimiter, handles BOM and GBK, skips comment lines, and
understands the common "long name / units" two-row header.
"""

import codecs
import csv
import os
import re

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "cp1252", "latin-1")
COMMENT_PREFIXES = ("#", "//", "!", ";")
_NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?$")
_DELIMS = ("\t", ",", ";", "|")


def _is_number(token):
    t = token.strip().replace("d", "E").replace("D", "E")
    return bool(t) and bool(_NUM.match(t))


_NAME_KEYWORDS = ("long name", "longname", "name", "display", "l")
_UNIT_KEYWORDS = ("units", "unit", "u")
# "Potential/V", "电流 (mA)", "Z'/ohm": instruments pack the unit into the name.
_NAME_UNIT = re.compile(r"^(?P<name>[^/\(\[]+?)\s*(?:/\s*|\(|\[)\s*(?P<units>[^)\]]+?)\s*[\)\]]?$")
_PAIR = re.compile(r"^\s*(?P<key>[^=:]{2,40}?)\s*[=:]\s*(?P<value>.+?)\s*$")


def _row_cells(row, width):
    return [str(row[c] if c < len(row) else "").strip() for c in range(width)]


def _all_names(row, width):
    cells = _row_cells(row, width)
    return bool(cells) and all(cells) and not any(_is_number(c) for c in cells)


def _units_like(row, width):
    """A units row is short text ("s", "mV", "-") with no name-like slashes."""
    cells = [c for c in _row_cells(row, width) if c]
    return bool(cells) and not any(_is_number(c) for c in cells) \
        and all(len(c) <= 6 and "/" not in c and "(" not in c for c in cells)


def split_name_units(text):
    m = _NAME_UNIT.match(str(text).strip())
    if not m:
        return str(text).strip(), ""
    return m.group("name").strip(), m.group("units").strip()


def _pick_name_row(head, width):
    """Which header row carries the column long names?

    Origin's own exports use a labelled block (Long Name / Units / Comments), so
    that row wins. Instrument dumps (CH Instruments .txt and friends) put a page
    of settings first and the column line immediately above the data, so the last
    all-text row is the name row. Taking head[0] blindly named every column
    "Jan. 31" / 2011 15:12:13 on those files.
    """
    for i, row in enumerate(head):
        cells = _row_cells(row, width)
        if cells and cells[0].lower() in _NAME_KEYWORDS and _all_names(row, width):
            return i
    if head and _all_names(head[-1], width):
        # "Time Signal Flag" followed by "s mV -": the short row is the units row.
        if len(head) >= 2 and _all_names(head[-2], width) and _units_like(head[-1], width):
            return len(head) - 2
        return len(head) - 1
    if len(head) >= 2 and _all_names(head[-2], width) and _units_like(head[-1], width):
        return len(head) - 2
    best_i, best_score = 0, None
    for i, row in enumerate(head):
        toks = _row_cells(row, width)
        uniq = len({t for t in toks if t})
        score = uniq * 10 + sum(1 for t in toks if t)
        if uniq <= 1 and width > 1:
            score -= 50
        if best_score is None or score > best_score:
            best_i, best_score = i, score
    return best_i


def read_metadata(head, k):
    """Key = Value / Key: Value lines above the column row, kept for provenance."""
    meta = {}
    for row in head[:k]:
        line = ", ".join(c for c in row if c).strip()
        m = _PAIR.match(line)
        if m and not _is_number(m.group("key")):
            meta.setdefault(m.group("key").strip(), m.group("value"))
        if len(meta) >= 40:
            break
    return meta


_BOMS = ((codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
         (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"),
         (codecs.BOM_UTF8, "utf-8-sig"))

# Some instrument software and Windows' "Unicode Text" save write UTF-16 with no
# BOM at all. Half of every byte is then 0x00, which no other codec survives.
_NUL_RATIO = 0.2


def high_byte_runs(data):
    """Lengths of maximal runs of bytes >= 0x80.

    GBK-family text pairs them: one Chinese character is two high bytes. cp1252 /
    latin-1 punctuation shows up as isolated singles — µ, °, ±, ·. That is the only
    honest way to tell "温度/C" from "T/°C", because gb18030 will decode *both*
    without raising and silently mojibake the second one into 祍/癈.
    """
    runs, cur = [], 0
    for byte in data:
        if byte >= 0x80:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    return runs


def candidate_encodings(data):
    order = list(ENCODINGS)
    runs = high_byte_runs(data)
    if runs and any(run % 2 for run in runs):
        gb = order.index("gb18030")
        singles = [enc for enc in ("cp1252", "latin-1") if enc in order]
        for enc in singles:
            order.remove(enc)
        order[gb:gb] = singles
    return order


def read_text(path):
    """Decode a data file, trying the encodings that actually occur in labs."""
    with open(path, "rb") as fh:
        data = fh.read()
    for bom, enc in _BOMS:
        if data.startswith(bom):
            body = data[len(bom):]
            if enc == "utf-8-sig":
                return body.decode("utf-8"), enc
            try:
                return body.decode(enc), enc
            except UnicodeDecodeError as exc:
                raise ValueError("文件带 %s 字节头但解不开：%s（%s）" % (enc, path, exc))
    if len(data) >= 8 and data.count(b"\x00") / float(len(data)) > _NUL_RATIO:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                text = data.decode(enc)
            except UnicodeDecodeError:
                continue
            if "\x00" not in text:
                return text, "%s（无 BOM，按 NUL 分布判断）" % enc
    last = None
    for enc in candidate_encodings(data):
        try:
            return data.decode(enc), enc
        except (UnicodeDecodeError, LookupError) as exc:
            last = exc
    raise ValueError("无法解码 %s（试过 %s），最后错误：%s" % (path, "/".join(ENCODINGS), last))


def sniff_delimiter(rows):
    """Pick the delimiter that maximises the number of consistent numeric columns."""
    body = [r for r in rows if r.strip() and not r.lstrip().startswith(COMMENT_PREFIXES)]
    if not body:
        return None
    best, best_score = None, (-1, -1)
    for delim in _DELIMS:
        grid = [next(csv.reader([line], delimiter=delim)) for line in body]
        widths = {len(g) for g in grid}
        if not widths:
            continue
        width = max(widths)
        if width < 2:
            continue
        # score: numeric columns first, then how many rows actually have that width,
        # then FEWER columns. Ranking by width alone let a wrong comma split of a
        # European "0,0;1,5" file beat the correct ';' because it made three columns.
        consistent = sum(1 for g in grid if len(g) == width) / float(len(grid))
        # The first row is a header candidate, so keep it out of the numeric ratio —
        # otherwise a European "Zeit;Signal / 0,0;1,5" file scores every delimiter as
        # non-numeric and the widest wrong split wins by default.
        data = grid[1:] if len(grid) >= 2 else grid
        hits = 0
        for col in range(width):
            vals = [g[col] for g in data if len(g) > col]
            if vals and sum(1 for v in vals if _is_number(v) or _CELL_DECCOMA.match(v.strip())) \
                    >= 0.8 * len(vals):
                hits += 1
        score = (hits, round(consistent, 3), -width)
        if score > best_score:
            best, best_score = delim, score
    if best is not None:
        return best
    # fall back to whitespace alignment
    grid = [line.split() for line in body]
    width = max(len(g) for g in grid)
    if width >= 2:
        return None
    if width == 1:
        # "识别不出分隔符" would send the reader chasing encoding or comment
        # prefixes; the real reason is that a single column cannot make a plot.
        raise ValueError("文件只有一列数据：至少需要两列（x 和 y）才能解析成数据表")
    raise ValueError("识别不出分隔符，也没有多列空白对齐的数据")


_NUM_SRC = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eEdD][+-]?\d+)?"
# Lab exports decorate their numbers. Dropping a whole column because one cell is
# decorated silently loses real data, so each shape below is recognised, unwrapped,
# and reported as a warning instead of quietly skipped.
_CELL_DL = re.compile(r"^(?:[<>]=?|≥|≤)\s*(%s)$" % _NUM_SRC)          # <0.001, >1e6
_CELL_PM = re.compile(r"^(%s)\s*(?:±|\+/-|\+-)\s*\d*\.?\d+$" % _NUM_SRC)  # 2.5±0.1
_CELL_UNIT = re.compile(r"^(%s)\s+\S+$" % _NUM_SRC)                   # "25 mV"
_CELL_FLAG = re.compile(r"^(%s)[*†‡°\s]+$" % _NUM_SRC)                # "2.5*"
_CELL_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")  # "1,234.5"
# European exports use the comma as the decimal mark, which only parses when the
# field separator is something else (usually ';').
_CELL_DECCOMA = re.compile(r"^[+-]?\d+,\d+$")                          # "0,5"
_CELL_PAREN = re.compile(r"^\((%s)\)$" % _NUM_SRC)                     # "(500)"
_MISSING_TOKENS = ("NA", "NAN", "-", "--", "NIL", "N/A", "NULL", "#N/A", "#DIV/0!")

CELL_KIND_NOTES = {
    "检测限": "第 %d 列 %r 有 %d 个检测限标记（例如 <0.001），已按边界值代入",
    "不确定度": "第 %d 列 %r 有 %d 个 ± 写法（例如 2.5±0.1），已取中心值",
    "带单位的数": "第 %d 列 %r 有 %d 个数值把单位写在同一格（例如 25 mV），已取数值",
    "千分位": "第 %d 列 %r 有 %d 个千分位写法（例如 1,234.5），已去掉分隔逗号",
    "小数逗号": "第 %d 列 %r 有 %d 个用逗号当小数点的写法（例如 0,5），已按小数解析",
    "标记后缀": "第 %d 列 %r 有 %d 个带 * / † 标记的值，已按数值本身代入",
    "括号负数": "第 %d 列 %r 有 %d 个用括号表示负数的写法（例如 (500)），已取负值",
    "溢出": "第 %d 列 %r 有 %d 个数值超出浮点范围（例如 1e400），已按缺值处理",
}


def _to_float(text):
    return float(text.replace("d", "E").replace("D", "E"))


def _finite(value):
    return value == value and value not in (float("inf"), float("-inf"))


def cell_number(cell, decimal_comma=False):
    """One cell -> (value or None, kind or None). kind is what we had to forgive.

    Lab exports decorate their numbers: detection limits, uncertainties, a unit in
    the same cell, thousands separators, outlier flags. Dropping the whole column
    because one cell is decorated silently loses real data, so each shape is
    recognised, unwrapped, and reported as a warning instead of quietly skipped.
    """
    text = str(cell).strip().strip('"').strip("'")
    if not text or text.upper() in _MISSING_TOKENS:
        return None, None
    if decimal_comma and _CELL_DECCOMA.match(text):
        value = _to_float(text.replace(",", "."))
        return (value, "小数逗号") if _finite(value) else (None, "溢出")
    try:
        value = _to_float(text)
    except ValueError:
        pass
    else:
        return (value, None) if _finite(value) else (None, "溢出")
    if _CELL_THOUSANDS.match(text):
        value = _to_float(text.replace(",", ""))
        return (value, "千分位") if _finite(value) else (None, "溢出")
    m = _CELL_PAREN.match(text)
    if m:
        value = -abs(_to_float(m.group(1)))
        return (value, "括号负数") if _finite(value) else (None, "溢出")
    for rx, kind in ((_CELL_DL, "检测限"), (_CELL_PM, "不确定度"),
                     (_CELL_UNIT, "带单位的数"), (_CELL_FLAG, "标记后缀")):
        m = rx.match(text)
        if m:
            try:
                value = _to_float(m.group(1))
            except ValueError:
                return None, None
            return (value, kind) if _finite(value) else (None, "溢出")
    return None, None


def parse(path, comment=None):
    """Return {columns:[{name,units,values}], rows, delimiters, encoding, warnings}."""
    text, enc = read_text(path)
    comments = COMMENT_PREFIXES if comment is None else (comment,)
    raw_lines = text.splitlines()
    kept = [ln for ln in raw_lines if ln.strip() and not ln.lstrip().startswith(comments)]
    if not kept:
        raise ValueError("文件里只有注释或空行：%s" % path)

    warnings = []
    delim = sniff_delimiter(kept)
    if delim is None:
        grid = [ln.split() for ln in kept]
        warnings.append("按空白对齐解析（无显式分隔符）")
    else:
        grid = [next(csv.reader([ln], delimiter=delim)) for ln in kept]

    width = max(len(r) for r in grid)
    # Deciding this here matters twice: the header/data split below and the cell
    # reader both need to know whether a bare "0,5" is a European decimal number
    # rather than two comma-separated fields.
    decimal_comma = delim not in (",", None)
    if len({len(r) for r in grid}) > 1:
        warnings.append("行长度不一致，已按最宽 %d 列补齐" % width)
    grid = [r + [""] * (width - len(r)) for r in grid]

    # split leading header rows (rows that are mostly non-numeric) from data
    head, body = [], []
    for i, row in enumerate(grid):
        numeric = sum(1 for c in row if _is_number(c)
                      or (decimal_comma and _CELL_DECCOMA.match(c.strip())))
        if numeric >= max(1, int(0.6 * width)):
            body = grid[i:]
            break
        head.append(row)
    else:
        raise ValueError("找不到数值数据行：%s（只有表头？）" % path)

    k = _pick_name_row(head, width) if head else 0
    raw_names = _row_cells(head[k], width) if head else [""] * width
    if k + 1 < len(head):
        units = _row_cells(head[k + 1], width)
    elif k > 0 and head[k - 1] and head[k - 1][0].strip().lower() in _UNIT_KEYWORDS:
        units = _row_cells(head[k - 1], width)
    else:
        units = [""] * width
    names = []
    for c in range(width):
        nm, un = split_name_units(raw_names[c])
        names.append(nm)
        if un and not units[c]:
            units[c] = un
    raw_names = names
    metadata = read_metadata(head, k)
    extra = k if metadata else max(0, len(head) - 2)
    if extra:
        warnings.append("忽略了 %d 行文件说明（仪器参数已放进 metadata）" % extra)

    columns = []
    for c in range(width):
        vals = []
        kinds = {}
        for row in body:
            cell = row[c].strip()
            value, kind = cell_number(cell, decimal_comma=decimal_comma)
            if kind:
                kinds[kind] = kinds.get(kind, 0) + 1
            vals.append(value)
        live = sum(1 for v in vals if v is not None)
        name = raw_names[c] or "col%d" % (c + 1)
        # Report what we forgave even when the column ends up dropped: "全为空" alone
        # hides that every cell was text (a row-label column) or overflowed.
        for kind, count in sorted(kinds.items()):
            warnings.append(CELL_KIND_NOTES[kind] % (c + 1, name, count))
        if live == 0:
            blanks = sum(1 for row in body if not row[c].strip())
            if blanks == len(body):
                continue  # a genuinely empty trailing column, nothing to explain
            warnings.append("第 %d 列 %r 没有一个能读成数值（%d 行），已跳过；"
                            "如果它是行标签/文本列属正常，要画它请选别的列"
                            % (c + 1, name, len(body)))
            continue
        columns.append({
            "index": c + 1,
            "name": name,
            "units": units[c] or "",
            "values": vals,
            "numeric_rows": live,
        })
    if not columns:
        raise ValueError("解析后没有任何有效数值列：%s" % path)
    return {
        "path": os.path.abspath(path),
        "encoding": enc,
        "delimiter": delim or "whitespace",
        "rows": len(body),
        "header_rows": len(head),
        "columns": columns,
        "metadata": metadata,
        "warnings": warnings,
    }
