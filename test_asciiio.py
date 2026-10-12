"""Parser checks for asciiio — runs without Origin, so it is the fastest loop."""

import os
import sys

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
        ("0,5", None, None), ("0,5;1,5", None, None), ("text", None, None)):
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

print()
if fails:
    print("FAILED (%d): %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all asciiio checks passed")
