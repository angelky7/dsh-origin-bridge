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
# 缺值写法：这些格子是"没有测量值"，和"有内容但读不成数"必须区分开。
# 放在这里是因为嗅探分隔符、判定注释行、找第一条数据行都要用同一份清单。
_MISSING_TOKENS = ("NA", "NAN", "-", "--", "NIL", "N/A", "NULL", "#N/A", "#DIV/0!")


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
    """A units row is short text ("s", "mV", "µS/cm", "-") with no numbers.

    长度上限原来是 6 并且把 "/"、"(" 一起否掉，实测 "s µS/cm" 两行表头因此被判成
    "名字行"：列名变成 ('s', 'µS')，单位变成空串和 'cm'，因为 'µS/cm' 又被
    split_name_units 按斜杠切走一刀。真实单位本来就常超过 6 字符（µS/cm、mS/cm、
    µmol/L、°C），所以这里只留两条硬约束：没有数字、够短。
    """
    cells = [c for c in _row_cells(row, width) if c]
    return bool(cells) and not any(_is_number(c) for c in cells) \
        and all(len(c) <= 12 for c in cells)


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
    # 先判最后一行是不是单位行：两行表头几乎总是"长名 + 单位"，反过来先接受最后
    # 一行当名字行，就把单位顶成了列名（实测 "s µS/cm" 变成列名 'µS'）。
    if len(head) >= 2 and _all_names(head[-2], width) and _units_like(head[-1], width):
        return len(head) - 2
    if head and _all_names(head[-1], width):
        return len(head) - 1
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

# 这些正则都在几十 MB 的数据文件上跑，所以一律用 C 层的 match/findall，
# 不在 Python 层逐字节循环（否则大文件会白白多花好几秒）。
_GBK_PAIR = re.compile(rb"(?:[\x00-\x7f]+|[\x81-\xfe][\x40-\x7e\x80-\xfe])*\Z")
_ASCII_BYTES = bytes(range(0x80))
_HIGH_RUN = re.compile(rb"[\x80-\xff]+")
# C1 控制字符（U+0080-U+009F）：GBK 第二字节被当单字节西文读时的指纹。
_C1_CHARS = re.compile("[\u0080-\u009f]")
# 西文重音紧贴 ASCII 字母（Größe 的 r+ö、ß+e）。
_ACCENT_NEIGHBOUR = re.compile(rb"[A-Za-z][\xc0-\xff]|[\xc0-\xff][A-Za-z]")


def high_byte_runs(data):
    """Lengths of maximal runs of bytes >= 0x80.

    Kept as a diagnostic only (the warnings quote it): it is NOT the GBK/cp1252
    discriminator any more. GBK's second byte falls in 0x40-0x7E for about a third
    of CJK code points (实测 7308 个), and such a trail byte is ASCII, so one
    character ("玥" = AB 68) turns a run odd while cp1252's "Größe" (F6 DF) gives an
    even run — the parity rule failed in both directions. See encoding_scores.
    """
    return [len(m) for m in _HIGH_RUN.findall(bytes(data))]


def _gbk_structural(data):
    """每个高字节都能配成 GBK 的 lead(0x81-0xFE) + trail(0x40-0x7E/0x80-0xFE) 对吗？

    这是硬约束：一个配不上对的高字节就说明这堆字节不是 GBK 双字节文本。
    反过来，配对成功是"可能是 GBK"，不是"就是 GBK"（cp1252 的两个紧邻重音
    字母也能凑成一对），所以还要下面的打分。

    写成一条正则而不是逐字节循环：这里会被几十 MB 的数据文件调用，Python 层
    的逐字节循环会白白多花几秒。ASCII 用 [\x00-\x7f]+ 整段吃掉也是这个原因——
    逐字节的 (?:a|b)* 在 8 MB 的文件上要 1 秒，整段吃掉只要 10 毫秒。
    """
    return _GBK_PAIR.match(bytes(data)) is not None


def _decodes_clean(data, enc):
    """解出来不许有 C1 控制字符（U+0080-U+009F）。

    GBK 的第二个字节被当单字节西文读时正好落进这一段：latin-1 把它们原样映射成
    控制码（实测 "³Æ\\x81@"），cp1252 在这种字节上直接抛错。任一种都说明这不是
    单字节西文文件，该候选必须淘汰。
    """
    try:
        text = data.decode(enc)
    except UnicodeDecodeError:
        return False
    return _C1_CHARS.search(text) is None


def _accents_next_to_ascii(data):
    """C0-FF 的高字节紧贴 ASCII 字母的次数（"Größe" 的 ö/ß 就是这种形态）。

    西文重音字母总是长在单词里（ö 左右是 r 和 e），GBK 汉字的两个字节彼此相邻，
    不会贴着 ASCII 字母。这是 cp1252 唯一稳的证据；0xB0/0xB5（°、µ）这类单个
    符号不产生证据，交给打分持平时的默认顺序处理。
    """
    return len(_ACCENT_NEIGHBOUR.findall(bytes(data)))


# 打分持平时用哪张"更保守"的牌：单字节西文是默认假设，因为猜错成 GBK 会把
# 一段正常的 µs/°C 变成汉字（原有的 祍/癈 事故），而真 GBK 文件几乎总带着
# 成对的汉字，会由分数自己赢上去。
_ENC_PRIORITY = {"cp1252": 0, "latin-1": 1, "gb18030": 2}
_GUESSED = ("gb18030", "cp1252", "latin-1")


def encoding_scores(data):
    """每个候选编码与这堆字节的相容度（分越高越像；负分=基本可以排除）。

    Why not parity: see high_byte_runs. 这里改用"结构 + 证据"：
      * GBK 要求每个高字节都是合法的 lead/trail 对，且最长游程 >=4 或高字节占比
        高（真中文列名不会整段都是 ASCII）；
      * cp1252/latin-1 要求解出来的文本里没有 C1 控制字符；
      * 0xC0-0xFF 的高字节紧贴 ASCII 字母是西文重音的强证据。
    """
    data = bytes(data)
    high = len(data.translate(None, _ASCII_BYTES))
    if not high:
        # 纯 ASCII：一个高字节都没有，就没有编码歧义可判（UTF-8 必然能解开），
        # 不必为"猜的顺序"把几十 MB 的文件解三遍。
        return {"gb18030": -1, "cp1252": 1, "latin-1": 0.5}
    runs = high_byte_runs(data)
    longest = max(runs) if runs else 0
    ratio = high / float(len(data)) if data else 0.0
    accents = _accents_next_to_ascii(data)
    gbk_ok = _gbk_structural(data) and _decodes_clean(data, "gb18030")
    scores = {
        "gb18030": (1 + (2 if longest >= 4 else 0) + (2 if ratio >= 0.5 else 0)
                    + (1 if longest >= 2 else 0)) if gbk_ok else -1,
    }
    for enc in ("cp1252", "latin-1"):
        clean = _decodes_clean(data, enc)
        if enc == "latin-1":
            # latin-1 永远解得开，所以它只配当兜底：没有证据就不许盖过 cp1252。
            scores[enc] = 0.5 if clean else -1
        else:
            scores[enc] = (1 + 3 + min(accents, 3)) if (clean and accents) \
                else (1 if clean else -1)
    return scores


def candidate_encodings(data, scores=None):
    """按"与这些字节的相容度"排出尝试顺序，UTF-8 永远先试。

    UTF-8 自带校验，解不开就是真不是 UTF-8；剩下的都是猜，猜的结果必须由
    read_text 写进 warning（见 encoding_note），不许静默 mojibake。
    """
    data = bytes(data)
    scored = encoding_scores(data) if scores is None else scores
    order = [enc for enc in ENCODINGS if enc in ("utf-8-sig", "utf-8")]
    rest = sorted(_GUESSED, key=lambda enc: (-scored[enc], _ENC_PRIORITY[enc]))
    return order + rest


def _score_text(scores):
    """把候选评分排成一行写进 warning：用户能核对"试过哪些、各自多少分"。"""
    return "，".join("%s=%s" % (enc, scores.get(enc, "?")) for enc in _GUESSED)


def encoding_note(data, enc, scores=None):
    """解释"为什么按这个编码解"，以及列名乱码时怎么改。猜的编码必须带一条。"""
    data = bytes(data)
    if enc.startswith("utf-16"):
        # NUL 分布这种证据和候选评分不是一回事，混进来只会让人更糊涂。
        evidence = "依据：文件没有 BOM，但一多半的字节是 0x00"
    elif enc in ("gb18030", "cp1252", "latin-1"):
        scored = encoding_scores(data) if scores is None else scores
        if enc == "gb18030":
            why = "文件里 %d 个高字节都能配成 GBK 的前导+后继字节对，最长连续 %d 个" \
                % (len(data.translate(None, _ASCII_BYTES)),
                   max(high_byte_runs(data) or [0]))
        elif enc == "latin-1":
            why = "cp1252 在这份文件上解不开或解出 C1 控制码，只能逐字节按 latin-1 映射"
        else:
            why = "有 %d 处 0xC0-0xFF 的高字节紧贴 ASCII 字母，像 ö/ß 这类西文重音" \
                % _accents_next_to_ascii(data)
        evidence = "依据：%s；候选评分 %s" % (why, _score_text(scored))
    else:
        return None
    return ("编码不是 UTF-8、也没有 BOM，按 %s 解出来的（%s）。"
            "如果列名或单位显示成乱码，说明这次猜错了：请在仪器软件里改用 UTF-8 "
            "重新导出，或把文件另存为带 BOM 的 UTF-8 后再导入。" % (enc, evidence))


def read_text(path):
    """Decode a data file, trying the encodings that actually occur in labs.

    Returns (text, encoding, note)。note 只在编码是"猜"的时候非空：BOM 和能通过
    校验的 UTF-8 是证据，其余（gb18030 / cp1252 / latin-1 / 无 BOM 的 utf-16）
    都可能猜错，猜错的表现是列名乱码，必须让调用方把它写进 warnings。
    """
    with open(path, "rb") as fh:
        data = fh.read()
    for bom, enc in _BOMS:
        if data.startswith(bom):
            body = data[len(bom):]
            if enc == "utf-8-sig":
                return body.decode("utf-8"), enc, None
            try:
                return body.decode(enc), enc, None
            except UnicodeDecodeError as exc:
                raise ValueError("文件带 %s 字节头但解不开：%s（%s）" % (enc, path, exc))
    if len(data) >= 8 and data.count(b"\x00") / float(len(data)) > _NUL_RATIO:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                text = data.decode(enc)
            except UnicodeDecodeError:
                continue
            if "\x00" not in text:
                named = "%s（无 BOM，按 NUL 分布判断）" % enc
                return text, named, encoding_note(data, enc)
    last = None
    scores = encoding_scores(data)
    for enc in candidate_encodings(data, scores):
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError) as exc:
            last = exc
            continue
        # UTF-8 解开了就是对的，不算猜；其余都要在 warnings 里交代依据。
        return text, enc, encoding_note(data, enc, scores)
    raise ValueError("无法解码 %s（试过 %s），最后错误：%s" % (path, "/".join(ENCODINGS), last))


def _sniff_numeric(cell, decimal_comma):
    """嗅探评分用的"这格能不能读成数"：和 cell_number 同一套宽恕口径。

    只认 _is_number 会误判：tab 文件里 "25 mV" 整列不算命中，而空白切出来的
    'mV' 列也不算命中——两边都低估，评分就更分不清"真的两列"和"一格塞了数值+单位"。
    先用 _is_number 走快路，只有不认识的格子才付出 cell_number 的完整代价：
    评分要扫过每个候选的每一格，大文件上这一层不能慢。
    """
    text = str(cell).strip()
    if _is_number(text) or (decimal_comma and _CELL_DECCOMA.match(text)):
        return True
    value, _ = cell_number(text, decimal_comma=decimal_comma)
    return value is not None


def sniff_delimiter(rows, skip_comments=False):
    """Pick the delimiter that maximises consistent numeric columns.

    空白对齐（line.split()）现在是一个正式候选，和 \t , ; | 用同一套评分比。
    之前它只是"全都不行时的兜底"，于是空白对齐文件里出现一个千分位逗号
    （"1.0 1,234.5"）就让 ',' 赢了：那一格被切成 '1.0 1' 和 '234.5'，值只剩
    234.5（差 10 倍）。"Zeit Signal / 0 0,5" 更糟：逗号被当成分隔符，0.5 变成
    5.0，警告还说"格子里挤了两个数…请把分隔符设为空格"，把解析器选错说成文件本身
    有问题。

    评分仍是 (命中的数字列数, 行宽一致率, -列数)，一致率优先于列数；一列都
    对不上的候选（hits == 0）不许胜出。缺格按"没命中"计入分母，否则只在个别
    行冒出来的尾巴列（空白切出来的 "mV"、"0.2"）能靠 1/1 拿满分。
    """
    if skip_comments:
        # 调用方已经把注释行挑走了（甚至整份文件都是被注释前缀挡住的数据行）。
        body = [r for r in rows if r.strip()]
    else:
        body = [r for r in rows if r.strip() and not r.lstrip().startswith(COMMENT_PREFIXES)]
    if not body:
        return None
    best, best_score, found = None, None, False
    fallback, fallback_width = None, 1
    # 空白对齐排最后：评分持平时先信显式分隔符（"\t" 和 " " 常常等价）。
    for delim in list(_DELIMS) + [None]:
        if delim is None:
            grid = [line.split() for line in body]
        else:
            grid = [next(csv.reader([line], delimiter=delim)) for line in body]
        width = max(len(g) for g in grid)
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
        decimal_comma = delim != ","
        hits = 0
        for col in range(width):
            vals = [g[col] if col < len(g) else "" for g in data]
            ok = 0
            for v in vals:
                # 快路照旧：普通数字不该为一格付出 cell_number 的完整代价。
                if _is_number(v) or (decimal_comma and _CELL_DECCOMA.match(v.strip())):
                    ok += 1
                elif _sniff_numeric(v, decimal_comma):
                    ok += 1
            if vals and ok >= 0.8 * len(vals):
                hits += 1
        if hits == 0:
            if delim is not None and width > fallback_width:
                # 留着这个显式分隔符走完解析，让"找不到数值数据行"报出真正的原因，
                # 别在这里改口成"文件只有一列数据"（那会把用户引到编码/注释上）。
                fallback, fallback_width = delim, width
            continue
        score = (hits, round(consistent, 3), -width)
        if not found or score > best_score:
            best, best_score, found = delim, score, True
    if found:
        return best
    if fallback is not None:
        return fallback
    # 没有任何候选能解释出数字列：再看空白对齐能不能认出多列。
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
# Two numbers sharing one cell (a space-aligned dump read with the wrong delimiter)
# must not be dressed up as "number + unit", or the second one disappears quietly.
_CELL_TWO = re.compile(r"^(%s)\s+(%s)\s*$" % (_NUM_SRC, _NUM_SRC))
_CELL_FLAG = re.compile(r"^(%s)[*†‡°\s]+$" % _NUM_SRC)                # "2.5*"
_CELL_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")  # "1,234.5"
# European exports use the comma as the decimal mark, which only parses when the
# field separator is something else (usually ';').
_CELL_DECCOMA = re.compile(r"^[+-]?\d+,\d+$")                          # "0,5"
# "1,234"（1-3 位 + 恰好 3 位）两个方向都说得通：千分位 1234，或者小数 1.234。
# 旧的顺序先命中 _CELL_DECCOMA，于是 tab/分号文件里的 1,234 被读成 1.234，差 1000 倍。
_CELL_DECCOMA_AMBIG = re.compile(r"^[+-]?\d{1,3},\d{3}$")
# 千分位点 + 小数逗号（欧陆写法）：1.234,5 -> 1234.5。老代码两个方向都解不出。
_CELL_EU_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(?:\.\d{3})+,\d+$")
_CELL_PAREN = re.compile(r"^\((%s)\)$" % _NUM_SRC)                     # "(500)"

CELL_KIND_NOTES = {
    "检测限": "第 %d 列 %r 有 %d 个检测限标记（例如 %r），已按边界值代入",
    "不确定度": "第 %d 列 %r 有 %d 个 ± 写法（例如 %r），已取中心值",
    "带单位的数": "第 %d 列 %r 有 %d 个数值把单位写在同一格（例如 %r），已取数值",
    "一格两个数": "第 %d 列 %r 有 %d 个格子里挤了两个数（例如 %r），已取第一个；"
                  "若是空格把一格数据切成了两列，请改用制表符/CSV 导出",
    "千分位": "第 %d 列 %r 有 %d 个千分位写法（例如 %r），已去掉分隔逗号",
    "千分位歧义": "第 %d 列 %r 有 %d 个形如 1,234 的写法（例如 %r）：既可能是千分位 1234，"
                  "也可能是小数逗号 1.234。已按千分位 1234 解析；如果原意是小数，"
                  "请在仪器软件里改用小数点导出后再导",
    "千分位小数逗号": "第 %d 列 %r 有 %d 个千分位点+小数逗号的写法（例如 %r），"
                      "已按 1234.5 解析",
    "小数逗号": "第 %d 列 %r 有 %d 个用逗号当小数点的写法（例如 %r），已按小数解析",
    "标记后缀": "第 %d 列 %r 有 %d 个带 * / † 标记的值（例如 %r），已按数值本身代入",
    "括号负数": "第 %d 列 %r 有 %d 个用括号表示负数的写法（例如 %r），已取负值",
    "溢出": "第 %d 列 %r 有 %d 个数值超出浮点范围（例如 %r），已按缺值处理",
    # 非空、不是缺值标记、又谁都匹配不上：这些格子必须和 NA/- 区分开，否则
    # "#VALUE!"、"<LOD"、"2.5 V rms" 都变成静默空洞，列里少几个人都不知道。
    "无法解析": "第 %d 列 %r 有 %d 个格子无法解析（既不是数字也不是缺值标记，例如 %r），"
                "已按缺值处理；如果这是文本标签列属正常，否则说明分隔符/编码/表头没对上，"
                "别直接拿去画图",
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

    kind 为 None 只表示两种可能：真的缺值（NA/-/空格子），或者就是普通数字。
    有内容却读不成数的一律给 "无法解析"，免得它和缺值混成一个看不出来的空洞。
    """
    text = str(cell).strip().strip('"').strip("'")
    if not text or text.upper() in _MISSING_TOKENS:
        return None, None
    if decimal_comma:
        # 千分位点 + 小数逗号要在普通千分位之前判，否则 "1.234,5" 两个方向都解不出。
        if _CELL_EU_THOUSANDS.match(text):
            value = _to_float(text.replace(".", "").replace(",", "."))
            return (value, "千分位小数逗号") if _finite(value) else (None, "溢出")
        if _CELL_DECCOMA_AMBIG.match(text):
            # 1-3 位 + 恰好 3 位：千分位和小数逗号都成立，按千分位取大数并明确警告。
            value = _to_float(text.replace(",", ""))
            return (value, "千分位歧义") if _finite(value) else (None, "溢出")
        if _CELL_DECCOMA.match(text):
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
    m = _CELL_TWO.match(text)
    if m:
        # Checked before "number + unit": "2.5 3.5" is a mis-split column, not a
        # measurement with a unit, and calling it a unit loses the second number
        # without ever telling anyone.
        value = _to_float(m.group(1))
        return (value, "一格两个数") if _finite(value) else (None, "溢出")
    for rx, kind in ((_CELL_DL, "检测限"), (_CELL_PM, "不确定度"),
                     (_CELL_UNIT, "带单位的数"), (_CELL_FLAG, "标记后缀")):
        m = rx.match(text)
        if m:
            try:
                value = _to_float(m.group(1))
            except ValueError:
                return None, "无法解析"
            return (value, kind) if _finite(value) else (None, "溢出")
    return None, "无法解析"


def _data_cell_ok(text, decimal_comma):
    """这一格能不能算数据（数字、缺值标记，或逗号小数/千分位写法）。

    只在"整行要不要当注释丢掉"时用：宁可把一条真的像数据的行留下并警告，也不要
    在嗅探之前就把行删掉——实测 ";1.1" 和 "#N/A,2.0" 就是这样整行消失的。
    """
    t = str(text).strip()
    if not t or t.upper() in _MISSING_TOKENS or _is_number(t):
        return True
    if decimal_comma:
        return bool(_CELL_DECCOMA.match(t) or _CELL_DECCOMA_AMBIG.match(t)
                    or _CELL_EU_THOUSANDS.match(t) or _CELL_THOUSANDS.match(t))
    return False


def _is_data_row(row, width, decimal_comma):
    """第一条数据行从哪开始：老判据（60% 数字）+ 只增不减的新判据。

    "0,NA,NA,-" 这种稀疏数据行只有一个数字，60% 判据把它当成表头，于是它被
    并进表头、units 变成 "NA"、表宽还整体错位。只要每个非空格子都是数字或
    缺值标记，这一行本身就只能是数据行（老条件保持不动，避免把表头判成数据）。
    """
    numeric = sum(1 for c in row if _is_number(c)
                  or (decimal_comma and _CELL_DECCOMA.match(c.strip())))
    if numeric >= max(1, int(0.6 * width)):
        return True
    cells = [c.strip() for c in row if c.strip()]
    return bool(cells) and numeric >= 1 \
        and all(_data_cell_ok(c, decimal_comma) for c in cells)


def _comment_prefix(line, comments):
    stripped = line.lstrip()
    for prefix in comments:
        if stripped.startswith(prefix):
            return prefix
    return ""


def _split_line(line, delim):
    """一行切字段：显式分隔符走 csv（认引号），空白对齐走 split()。"""
    if delim is None:
        return line.split()
    return next(csv.reader([line], delimiter=delim))


def parse(path, comment=None):
    """Return {columns:[{name,units,values}], rows, delimiters, encoding, warnings}."""
    text, enc, enc_note = read_text(path)
    comments = COMMENT_PREFIXES if comment is None else (comment,)
    raw_lines = text.splitlines()
    warnings = []
    if enc_note:
        # 猜出来的编码必须自己说出来：猜错的表现是列名乱码，这条 warning 是用户
        # 唯一能看到的线索（cp1252 的 Größe 被当 GBK 读成 Gr鲞e 那类事故）。
        warnings.append(enc_note)

    plain = [(no, ln) for no, ln in enumerate(raw_lines, 1)
             if ln.strip() and not ln.lstrip().startswith(comments)]
    commented = [(no, ln) for no, ln in enumerate(raw_lines, 1)
                 if ln.strip() and ln.lstrip().startswith(comments)]
    if not plain and not commented:
        raise ValueError("文件里只有注释或空行：%s" % path)

    # 先嗅探分隔符，再判定注释：';' 同时是注释前缀和候选分隔符，先删行会把
    # 首字段为空的数据行（";1.1"）整行吞掉。被前缀挡下的行稍后用同一把尺子
    # 复判：字段数等于表宽、且每个非空格子都是数字或缺值标记的，是数据行。
    ruler = [ln for _, ln in (plain or commented)]
    delim = sniff_delimiter(ruler, skip_comments=True)
    # Deciding this here matters twice: the header/data split below and the cell
    # reader both need to know whether a bare "0,5" is a European decimal number
    # rather than two comma-separated fields. 空白对齐同理：空格切出来的格子里
    # 出现的逗号不可能是字段分隔符。
    decimal_comma = delim != ","
    rescued, dropped_comments, sniff_width = [], 0, 0
    if not commented:
        # 没有"像注释"的行：保持快路（一次列表推导），别为几千行的文件多扫一遍。
        kept = [ln for _, ln in plain]
    else:
        # 有像注释的行才需要表宽来复判；plain 的行号集合保证顺序仍是文件顺序。
        sniff_width = max(len(_split_line(ln, delim)) for ln in ruler)
        plain_nos = set(no for no, _ in plain)
        kept = []
        for no, ln in enumerate(raw_lines, 1):
            if not ln.strip():
                continue
            if no in plain_nos:
                kept.append(ln)
                continue
            prefix = _comment_prefix(ln, comments)
            cells = _split_line(ln, delim)
            if sniff_width >= 2 and len(cells) == sniff_width \
                    and any(c.strip() for c in cells) \
                    and all(_data_cell_ok(c, decimal_comma) for c in cells):
                kept.append(ln)
                rescued.append((no, prefix))
            else:
                dropped_comments += 1
    for no, prefix in rescued:
        warnings.append("第 %d 行以注释前缀 %r 开头，但按 %r 切分得到 %d 个字段、"
                        "全是数字或缺值标记，已按数据行保留"
                        % (no, prefix, delim or "空格", sniff_width))
    if dropped_comments:
        # 无条件交代丢了几行注释，用户才能核对行数（哪怕一行数据都没救回来）。
        warnings.append("跳过了 %d 行注释（以 %s 开头）"
                        % (dropped_comments, " ".join(comments)))
    if not kept:
        raise ValueError("文件里只有注释或空行：%s" % path)

    if delim is None:
        grid = [ln.split() for ln in kept]
        warnings.append("按空白对齐解析（无显式分隔符）")
    else:
        grid = [next(csv.reader([ln], delimiter=delim)) for ln in kept]

    width = max(len(r) for r in grid)
    if len({len(r) for r in grid}) > 1:
        warnings.append("行长度不一致，已按最宽 %d 列补齐" % width)
    grid = [r + [""] * (width - len(r)) for r in grid]

    # split leading header rows (rows that are mostly non-numeric) from data
    head, body = [], []
    for i, row in enumerate(grid):
        if _is_data_row(row, width, decimal_comma):
            body = grid[i:]
            break
        head.append(row)
    else:
        raise ValueError("找不到数值数据行：%s（只有表头？）" % path)

    k = _pick_name_row(head, width) if head else 0
    raw_names = _row_cells(head[k], width) if head else [""] * width
    units_below = bool(head) and k + 1 < len(head) and _units_like(head[k + 1], width)
    units_above = bool(head) and k > 0 and bool(head[k - 1]) \
        and head[k - 1][0].strip().lower() in _UNIT_KEYWORDS
    if units_below:
        # 名字行下面那一行只有在"确实像单位行"（无数字、短文本）时才取；
        # 否则它可能是数据行/说明行，把 "0"/"NA" 挂成单位比留空更坏。
        units = _row_cells(head[k + 1], width)
    elif units_above:
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
    used = {k}
    if units_below:
        used.add(k + 1)
    elif units_above:
        used.add(k - 1)
    extra = max(0, len(head) - len(used))
    if extra:
        # metadata 只收 Key=Value 行。没收到就别说"已放进 metadata"——那是假话
        # （实测宽表吞掉两行后明明 metadata 是空的，warning 却说仪器参数已收好）。
        if metadata:
            warnings.append("忽略了 %d 行表头以外的说明行（其中 %d 项 Key=Value 参数已放进 metadata）"
                            % (extra, len(metadata)))
        else:
            warnings.append("忽略了 %d 行表头以外的说明行（没有一处写成 Key=Value，未收进 metadata）"
                            % extra)

    columns = []
    for c in range(width):
        vals = []
        kinds = {}
        for row in body:
            cell = row[c].strip()
            value, kind = cell_number(cell, decimal_comma=decimal_comma)
            if kind:
                if kind in kinds:
                    kinds[kind][0] += 1
                else:
                    kinds[kind] = [1, cell]
            vals.append(value)
        live = sum(1 for v in vals if v is not None)
        name = raw_names[c] or "col%d" % (c + 1)
        # Report what we forgave even when the column ends up dropped: "全为空" alone
        # hides that every cell was text (a row-label column) or overflowed.
        # 例子原样带上，用户才认得出是哪一格（"%r" 会加引号显示原文）。
        for kind, (count, example) in sorted(kinds.items()):
            warnings.append(CELL_KIND_NOTES[kind] % (c + 1, name, count, example))
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
