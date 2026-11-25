"""Parser checks for asciiio — runs without Origin, so it is the fastest loop."""

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

import asciiio

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")

fails = []


def check(label, cond, got=""):
    if cond:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s  -> %r" % (label, got))
        fails.append(label)


def load(name):
    return asciiio.parse(os.path.join(FIX, name))


def view(d):
    return [(c["name"], c["units"], c["numeric_rows"]) for c in d["columns"]]


print("=" * 60)
print("asciiio parser checks")
print("=" * 60)

d = load("a_tab.dat")
check("tab -> 2 cols", len(d["columns"]) == 2, view(d))
check("tab names", [c["name"] for c in d["columns"]] == ["X", "Y"], view(d))
check("tab delimiter recorded", d["delimiter"] == "\t", d["delimiter"])

d = load("b_csv.csv")
check("csv delimiter", d["delimiter"] == ",", d["delimiter"])
check("csv 3 cols", len(d["columns"]) == 3, view(d))
check("csv long name + units rows",
      [(c["name"], c["units"]) for c in d["columns"]] ==
      [("Time", "s"), ("Signal", "mV"), ("V", "")], view(d))

d = load("c_semicolon.txt")
check("semicolon delimiter", d["delimiter"] == ";", d["delimiter"])

d = load("d_space.dat")
check("whitespace fallback", d["delimiter"] == "whitespace", d["delimiter"])
check("whitespace 3 cols", len(d["columns"]) == 3, view(d))
check("headerless cols get generated names",
      [c["name"] for c in d["columns"]] == ["col1", "col2", "col3"], view(d))

d = load("e_comment_lines.dat")
check("#, //, ! and ; comments all skipped", len(d["columns"]) == 2, view(d))
check("comment file keeps numeric rows", d["rows"] == 3, d["rows"])
check("comment file values", d["columns"][1]["values"] == [1.0, 2.0, 3.0],
      d["columns"][1]["values"])

d = load("f_multi_header.dat")
check("repeated filler row not used as names",
      [c["name"] for c in d["columns"]] == ["Time", "Signal", "Flag"], view(d))
check("units row follows the name row",
      [c["units"] for c in d["columns"]] == ["s", "mV", "-"], view(d))

d = load("g_na_and_D.dat")
c = {x["name"]: x for x in d["columns"]}
check("uppercase D exponent parsed", c["Y"]["numeric_rows"] == 2, c["Y"]["numeric_rows"])
check("lowercase d exponent parsed", c["Y"]["values"][:2] == [1.5, 2.5], c["Y"]["values"])
check("NA and '-' become None", c["Y"]["values"][2] is None, c["Y"]["values"])
check("sparse column survives with its one value", c["Z"]["numeric_rows"] == 1,
      c["Z"]["values"])
check("full column unaffected", c["X"]["numeric_rows"] == 3, c["X"]["values"])

d = load("h_gbk_units.dat")
check("gb18030 decoded", d["encoding"] == "gb18030", d["encoding"])
check("cjk long names survive",
      [c["name"] for c in d["columns"]] == ["温度", "电导"], view(d))

d = load("i_bom_utf8.csv")
check("BOM stripped from first name", d["columns"][0]["name"] == "波长", view(d))
check("utf-8-sig reported", d["encoding"] == "utf-8-sig", d["encoding"])

# ---------------------------------------------------------------- other encodings
print()
print("encodings a real instrument writes")

d = load("j_utf16_bom.dat")
check("utf-16 with BOM parses", [(c["name"], c["numeric_rows"]) for c in d["columns"]]
      == [("X", 2), ("Y", 2)], view(d))
check("utf-16 encoding named", d["encoding"] == "utf-16-le", d["encoding"])

d = load("k_utf16_nobom.dat")
check("utf-16 without BOM still parses", d["columns"][1]["values"] == [1.5, 2.5], d["columns"])
check("the guess is admitted", "无 BOM" in d["encoding"], d["encoding"])

d = load("l_cp1252_micro.dat")
check("cp1252 µ survives", d["columns"][1]["units"] == "µs", view(d))
check("not mojibaked into CJK", "祍" not in str(view(d)), view(d))
check("cp1252 named", d["encoding"] == "cp1252", d["encoding"])

d = load("m_cp1252_degree.dat")
check("cp1252 ° survives", d["columns"][0]["units"] == "°C", view(d))
check("° not read as 癈", "癈" not in str(view(d)), view(d))

# the discriminator itself
check("paired high bytes read as GBK",
      all(r % 2 == 0 for r in asciiio.high_byte_runs("温度\t电导".encode("gbk"))),
      asciiio.high_byte_runs("温度\t电导".encode("gbk")))
check("single high bytes read as cp1252",
      asciiio.high_byte_runs("T/°C".encode("cp1252")) == [1],
      asciiio.high_byte_runs("T/°C".encode("cp1252")))
check("gbk is not first when singles appear",
      asciiio.candidate_encodings("T/°C".encode("cp1252")).index("cp1252")
      < asciiio.candidate_encodings("T/°C".encode("cp1252")).index("gb18030"),
      asciiio.candidate_encodings("T/°C".encode("cp1252")))

# ------------------------------------------------------------ decorated numbers
print()
print("decorated numbers, unwrapped and reported")

d = load("n_detection_limit.dat")
check("detection limits become their bound", d["columns"][1]["values"] == [0.001, 0.5, 1e6],
      d["columns"][1]["values"])
check("and say so", any("检测限" in w for w in d["warnings"]), d["warnings"])

d = load("o_plusminus.dat")
check("mean±sd takes the mean", d["columns"][1]["values"] == [2.5, 3.5], d["columns"][1]["values"])
check("both spellings of +/-", any("± 写法" in w for w in d["warnings"]), d["warnings"])

d = load("p_unit_in_cell.dat")
check("unit in the cell does not kill the column",
      d["columns"][1]["values"] == [25.0, 30.0], d["columns"][1]["values"])
check("and is reported", any("单位写在同一格" in w for w in d["warnings"]), d["warnings"])

d = load("q_thousands.csv")
check("quoted thousands stay one column", [c["name"] for c in d["columns"]] == ["time", "signal"],
      view(d))
check("and keep their value", d["columns"][1]["values"] == [1234.5, 2345.6],
      d["columns"][1]["values"])
check("thousands reported", any("千分位" in w for w in d["warnings"]), d["warnings"])

d = load("r_decimal_comma.txt")
check("European file sniffs ';'", d["delimiter"] == ";", d["delimiter"])
check("header split on the right delimiter",
      [c["name"] for c in d["columns"]] == ["Zeit", "Signal"], view(d))
check("comma decimal parsed", d["columns"][1]["values"] == [1.5, 2.5], d["columns"][1]["values"])
check("decimal comma reported", any("小数点" in w for w in d["warnings"]), d["warnings"])

d = load("s_star_flag.dat")
check("star-flagged points keep their number",
      d["columns"][1]["values"] == [2.5, 3.5], d["columns"][1]["values"])
check("flag reported", any("标记的值" in w for w in d["warnings"]), d["warnings"])

d = load("t_paren_negative.dat")
check("parenthesised negative goes negative",
      d["columns"][1]["values"] == [-500.0, 250.0], d["columns"][1]["values"])
check("paren reported", any("括号表示负数" in w for w in d["warnings"]), d["warnings"])

d = load("u_overflow.dat")
check("overflowed cell becomes missing", d["columns"][1]["values"] == [None, 1.2e308],
      d["columns"][1]["values"])
check("overflow reported", any("超出浮点范围" in w for w in d["warnings"]), d["warnings"])
check("nothing infinite escapes the parser",
      all(v is None or (v == v and abs(v) != float("inf"))
          for c in d["columns"] for v in c["values"]), d["columns"])

d = load("v_row_labels.dat")
check("row-label column skipped", [c["name"] for c in d["columns"]] == ["V", "I"], view(d))
check("and explained as text", any("文本" in w for w in d["warnings"]), d["warnings"])

# cell_number on its own, so a regression in one shape is not hidden by a file
print()
print("cell_number shapes")
for text, want, kind in (
        ("1.5", 1.5, None), ("1.5D+03", 1500.0, None), (".5", 0.5, None),
        ("NA", None, None), ("--", None, None), ("#N/A", None, None),
        ("<0.001", 0.001, "检测限"), ("≥ 2.5", 2.5, "检测限"), (">1e6", 1e6, "检测限"),
        ("2.5±0.1", 2.5, "不确定度"), ("2.5 +/- 0.1", 2.5, "不确定度"),
        ("25 mV", 25.0, "带单位的数"), ("1,234.5", 1234.5, "千分位"),
        ("2.5*", 2.5, "标记后缀"), ("(500)", -500.0, "括号负数"),
        ("1e400", None, "溢出"), ("Infinity", None, "溢出"),
        # 有内容、又不是缺值标记、又谁都匹配不上的格子，从 D 起统一给 "无法解析"：
        # 再和 NA/空格子同为 (None, None)，"#VALUE!" 这种就变成看不出来的空洞了。
        ("0,5", None, "无法解析"), ("0,5;1,5", None, "无法解析"),
        ("text", None, "无法解析")):
    got, got_kind = asciiio.cell_number(text)
    check("cell %r" % text, got == want and got_kind == kind, (got, got_kind))
check("cell '0,5' with decimal_comma", asciiio.cell_number("0,5", decimal_comma=True) == (0.5, "小数逗号"),
      asciiio.cell_number("0,5", decimal_comma=True))
check("cell '\"2,5\"' quoted", asciiio.cell_number('"2,5"', decimal_comma=True) == (2.5, "小数逗号"),
      asciiio.cell_number('"2,5"', decimal_comma=True))
check("cell '2.5 3.5' is two numbers, not a unit",
      asciiio.cell_number("2.5 3.5") == (2.5, "一格两个数"), asciiio.cell_number("2.5 3.5"))
check("cell '25 mV' stays a unit",
      asciiio.cell_number("25 mV") == (25.0, "带单位的数"), asciiio.cell_number("25 mV"))

# a mis-split column has to say so instead of quietly dropping every second value
d = load("w_two_numbers_cell.csv")
check("two numbers in one cell are reported", any("挤了两个数" in w for w in d["warnings"]),
      d["warnings"])
check("the first number of each cell survives",
      d["columns"][1]["values"] == [2.5, 4.0], d["columns"][1]["values"])

# ---------------------------------------------- A: 注释前缀开头的合法数据行
print()
print("A: 先嗅探分隔符，再判定注释（';' 和 '#' 既是前缀又可能是数据）")

d = load("x_comment_prefix_data.txt")
c = {x["name"]: x for x in d["columns"]}
check("A 首字段为空的数据行不再整行消失（5 行）", d["rows"] == 5, d["rows"])
check("A 值按原行顺序完整", c["I"]["values"] == [1.0, 1.1, 1.2, 1.3, 1.4], c["I"]["values"])
check("A 救回来的行逐行点名",
      any("第 3 行" in w and "已按数据行保留" in w for w in d["warnings"]), d["warnings"])
check("A 第二行也一样点名", any("第 5 行" in w for w in d["warnings"]), d["warnings"])
check("A 真注释丢弃了几行要无条件写进 warnings",
      any("跳过了 1 行注释" in w for w in d["warnings"]), d["warnings"])
check("A 真注释没有被当成一行数据",
      not any("note" in w for w in d["warnings"]), d["warnings"])

d = load("y_comment_hash_missing.csv")
c = {x["name"]: x for x in d["columns"]}
check("A 首列是 #N/A 的行保留（缺值标记不是注释）", d["rows"] == 3, d["rows"])
check("A 保留后值不缺段", c["Signal"]["values"] == [1.0, 2.0, 3.0], c["Signal"]["values"])
check("A 这一行也被点名",
      any("第 3 行" in w and "缺值标记" in w for w in d["warnings"]), d["warnings"])
check("A 没有注释行时不许凭空报行数",
      not any("跳过了" in w for w in d["warnings"]), d["warnings"])

# ---------------------------------------------- B: 稀疏数据行 / units 取法
print()
print("B: 稀疏的第一条数据行不能被并进表头")

d = load("z_sparse_body.csv")
c = {x["name"]: x for x in d["columns"]}
check("B 稀疏数据行全部算数据（4 行）", d["rows"] == 4, d["rows"])
check("B 表头只是两行说明+名字行", d["header_rows"] == 3, d["header_rows"])
check("B 列名是 t/A/B/C", [x["name"] for x in d["columns"]] == ["t", "A", "B", "C"], view(d))
check("B 单位留空，不把数据行当单位",
      [x["units"] for x in d["columns"]] == ["", "", "", ""], view(d))
check("B 稀疏列仍有 3 个数值", c["A"]["numeric_rows"] == 3, c["A"]["values"])
check("B 第一条数据行没丢", c["A"]["values"] == [None, 1.1, 1.2, 1.3], c["A"]["values"])
check("B metadata 为空时不许说已收进 metadata",
      d["metadata"] == {} and not any("已放进 metadata" in w for w in d["warnings"]),
      (d["metadata"], d["warnings"]))
check("B 说明行如实说明未收进 metadata",
      any("忽略了 2 行" in w and "未收进 metadata" in w for w in d["warnings"]),
      d["warnings"])

# ---------------------------------------------- C: 空白对齐参与分隔符评分
print()
print("C: 空白对齐作为第五个候选参与同一套评分")

d = load("aa_space_thousands.dat")
check("C 千分位逗号不再抢走分隔符", d["delimiter"] == "whitespace", d["delimiter"])
check("C 只按空白分成两列", [x["name"] for x in d["columns"]] == ["X", "Y"], view(d))
check("C 千分位数值正确（1,234.5 -> 1234.5）",
      d["columns"][1]["values"] == [1234.5, 2345.6, 3456.7], d["columns"][1]["values"])

d = load("ab_space_deccomma.dat")
check("C 小数逗号不再被当成分隔符", d["delimiter"] == "whitespace", d["delimiter"])
check("C 0,5 读成 0.5 而不是 5.0",
      d["columns"][1]["values"] == [0.5, 1.5, 2.5], d["columns"][1]["values"])
check("C 警告不倒打一耙说文件有问题",
      not any("请把分隔符设为空格" in w for w in d["warnings"]), d["warnings"])
check("C 空白候选靠行宽一致率赢过逗号候选",
      asciiio.sniff_delimiter(["X Y", "1.0 1,234.5", "2.0 2345.6"]) is None,
      asciiio.sniff_delimiter(["X Y", "1.0 1,234.5", "2.0 2345.6"]))

# ---------------------------------------------- D: 读不出来 != 缺值
print()
print("D: 读不出来的格子要和缺值区分开")

d = load("ac_unparsable.csv")
check("D #VALUE! 仍然是缺值位",
      d["columns"][1]["values"] == [1.5, None, 2.5], d["columns"][1]["values"])
check("D 但警告里点名该格原文",
      any("无法解析" in w and "#VALUE!" in w for w in d["warnings"]), d["warnings"])

for text in ("N/D", "n.d.", "—", "≤", "<LOD", "2.5 V rms"):
    check("D cell %r 标为无法解析，不再静默 --" % text,
          asciiio.cell_number(text) == (None, "无法解析"), asciiio.cell_number(text))

check("D 1,234 在逗号小数文件里按歧义处理，取千分位 1234",
      asciiio.cell_number("1,234", decimal_comma=True) == (1234.0, "千分位歧义"),
      asciiio.cell_number("1,234", decimal_comma=True))
check("D 1.234,5 认成千分位点+小数逗号",
      asciiio.cell_number("1.234,5", decimal_comma=True) == (1234.5, "千分位小数逗号"),
      asciiio.cell_number("1.234,5", decimal_comma=True))

d = load("ah_decimal_ambiguity.dat")
c = {x["name"]: x for x in d["columns"]}
check("D tab 文件里三种逗号写法各归各位",
      c["Signal"]["values"] == [1234.0, 1234.5, 0.5], c["Signal"]["values"])
check("D 歧义写法给出明确警告",
      any("既可能是千分位" in w and "小数逗号" in w for w in d["warnings"]), d["warnings"])

# ---------------------------------------------- E: units 行的 "/" 与长度
print()
print("E: 单位含 / 或超过 6 个字符，不许被当成列名行")

d = load("af_units_slash.dat")
check("E 列名取名字行而不是单位行",
      [x["name"] for x in d["columns"]] == ["Time", "Conductivity"], view(d))
check("E 单位原样保留 µS/cm",
      [x["units"] for x in d["columns"]] == ["s", "µS/cm"], view(d))
check("E 单位行没有被当成数据行",
      d["header_rows"] == 2 and d["rows"] == 2, (d["header_rows"], d["rows"]))
check("E 数值列没被 µS/cm 顶掉",
      d["columns"][1]["values"] == [0.5, 0.6], d["columns"][1]["values"])
check("E 短单位对照组仍然正常",
      asciiio._units_like(["s", "mS"], 2) and asciiio._units_like(["s", "µS/cm"], 2),
      (asciiio._units_like(["s", "mS"], 2), asciiio._units_like(["s", "µS/cm"], 2)))

# ---------------------------------------------- F: 编码启发式
print()
print("F: 编码靠结构校验+打分，不再用高字节游程奇偶")

d = load("ad_cp1252_groesse.txt")
check("F cp1252 的 Größe 不再被读成 GBK", d["encoding"] == "cp1252", d["encoding"])
check("F 列名完整（含 ö/ß）",
      [x["name"] for x in d["columns"]] == ["Messung", "Größe"], view(d))
check("F 猜出来的编码无条件写进 warnings",
      any("编码不是 UTF-8" in w and "cp1252" in w for w in d["warnings"]), d["warnings"])
check("F 并交代列名乱码时怎么改",
      any("乱码" in w and "UTF-8" in w for w in d["warnings"]), d["warnings"])

d = load("ae_gbk_yao.dat")
check("F GBK 的 称玥 不再被读成 cp1252", d["encoding"] == "gb18030", d["encoding"])
check("F 汉字列名完整",
      [x["name"] for x in d["columns"]] == ["称玥", "电导"], view(d))
check("F GBK 的猜测也交代依据（结构校验）",
      any("编码不是 UTF-8" in w and "GBK" in w and "前导" in w for w in d["warnings"]),
      d["warnings"])

gbk_bytes = "称玥\t电导\n25.0\t1.23\n".encode("gbk")
check("F 结构校验把 GBK 排到 cp1252 前面",
      asciiio.candidate_encodings(gbk_bytes).index("gb18030")
      < asciiio.candidate_encodings(gbk_bytes).index("cp1252"),
      asciiio.candidate_encodings(gbk_bytes))
check("F GBK 排在 latin-1 前面（C1 控制码候选淘汰）",
      asciiio.candidate_encodings(gbk_bytes).index("gb18030")
      < asciiio.candidate_encodings(gbk_bytes).index("latin-1"),
      asciiio.candidate_encodings(gbk_bytes))
check("F 解出控制码的单字节候选不许胜出",
      asciiio.candidate_encodings(b"\x81\x40").index("gb18030")
      < asciiio.candidate_encodings(b"\x81\x40").index("latin-1"),
      asciiio.candidate_encodings(b"\x81\x40"))
check("F UTF-8 文件不算猜测，不许加编码警告",
      not any("编码不是 UTF-8" in w for w in load("a_tab.dat")["warnings"]),
      load("a_tab.dat")["warnings"])

print()
if fails:
    print("FAILED (%d): %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all asciiio checks passed")
