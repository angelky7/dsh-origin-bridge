"""Checks for binio — the electrochemical-workstation .bin reader.

The synthetic case pins the format down without needing the user's data, and the
real case cross-checks every value against the instrument's own text export,
which is the only ground truth available for a vendor binary format.
"""

import glob
import os
import re
import struct
import sys

# 控制台代码页不是我们能选的：中文 Windows 默认 cp936、西文 cp1252，而检查标签里
# 有 µ、°、≤ 和中文。一条检查都还没跑就 UnicodeEncodeError，等于把"你可以自己复跑"
# 这条路堵死——所以先把两个输出流设成"写不出来的字符用 ? 代替"，而不是崩。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

import binio
import originlab

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
REAL = os.environ.get("ORIGIN_BRIDGE_REAL_BIN_DIR") or os.path.expanduser(
    os.path.join("~", "Desktop", "shiyan"))
HEADER = 1445
BASE = HEADER - binio.PARAM_BLOCK_BACK
# 计数域相对数据区起点的距离是格式常量（真样本实测 944），所以合成文件必须把它
# 摆在 493/501，而不是早先随手写的 489/497，否则合成布局和真布局差 4 字节。
COUNT_OFF = 493                 # = HEADER - 944 - 8

fails = []


def check(label, cond, got=""):
    if cond:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s  -> %r" % (label, got))
        fails.append(label)


def build(path, tag, name, params, values, segments_field=2, count_field=None, tail=b""):
    """Synthesise a .bin.

    count_field lets the declared point count differ from the array actually
    written: that is how truncation / trailing padding look on disk, and the old
    helper derived total from len(values) so it could never build those cases.
    """
    buf = bytearray(b"\x00" * HEADER)
    buf[0:4] = binio.MAGIC
    struct.pack_into("<I", buf, 4, segments_field)
    head = tag.encode("latin-1") + struct.pack("<I", len(name)) + name.encode("latin-1")
    buf[8:8 + len(head)] = head
    buf[8 + len(head):8 + len(head) + 6] = b"r0815\x00"
    declared = len(values) if count_field is None else count_field
    struct.pack_into("<I", buf, COUNT_OFF, declared)
    struct.pack_into("<I", buf, COUNT_OFF + 8, declared)  # the real headers repeat it 8 apart
    struct.pack_into("<f", buf, BASE + 0, params["init"])
    struct.pack_into("<f", buf, BASE + 8, params["high"])
    struct.pack_into("<f", buf, BASE + 12, params["low"])
    struct.pack_into("<f", buf, BASE + 28, float(params["segments"]))
    struct.pack_into("<f", buf, BASE + 32, params["interval"])
    struct.pack_into("<f", buf, BASE + 40, params.get("rate", 0.1))
    struct.pack_into("<f", buf, BASE + 44, params.get("quiet", 2.0))
    with open(path, "wb") as fh:
        fh.write(bytes(buf) + struct.pack("<%df" % len(values), *values) + tail)
    return path


print("=" * 60)
print("binio .bin reader checks")
print("=" * 60)

os.makedirs(FIX, exist_ok=True)
# 合成布局必须和真布局一致：计数域到数据区起点 = HEADER_TAIL（实测 944）。
check("合成布局与 binio.HEADER_TAIL 一致",
      getattr(binio, "HEADER_TAIL", None) == HEADER - COUNT_OFF - 8,
      getattr(binio, "HEADER_TAIL", None))
# A 20-leg CV between 0.1 and 0.13 V at 1 mV steps: 30 points per leg, 600 total.
params = {"init": 0.1, "high": 0.13, "low": 0.1, "segments": 20, "interval": 0.001}
truth_x, _ = binio.build_axis(params, 600)
truth_y = [i * 1e-6 for i in range(600)]
p = build(os.path.join(FIX, "synthetic_cv.bin"), "CV", "Cyclic Voltammetry", params, truth_y)
d = binio.read_bin(p)
check("technique and full name", d["technique"] == "CV" and
      d["technique_name"] == "Cyclic Voltammetry", (d["technique"], d["technique_name"]))
check("build tag", d["build"] == "r0815", d["build"])
check("point count", d["points"] == 600, d["points"])
check("header size", d["header_bytes"] == HEADER, d["header_bytes"])
check("method block read back", abs(d["method"]["high_E"] - 0.13) < 1e-6 and
      d["method"]["segments"] == 20 and abs(d["method"]["step"] - 0.001) < 1e-8,
      d["method"])
check("axis reconstructed", d["axis"] == "reconstructed", d["axis"])
check("axis matches the triangle point for point",
      all(abs(a - b) < 1e-6 for a, b in zip(d["series"]["x"], truth_x)),
      [d["series"]["x"][28:33], truth_x[28:33]])
check("turning point belongs to the next leg",
      abs(d["series"]["x"][30] - 0.13) < 1e-6 and abs(d["series"]["x"][29] - 0.129) < 1e-6,
      d["series"]["x"][28:32])
check("current column survives the float32 round trip",
      all(abs(a - b) <= 1e-9 + 1e-6 * abs(b) for a, b in zip(d["series"]["y"], truth_y)),
      (d["series"]["y"][:3], truth_y[:3]))
brief = originlab.read_file(p)
check("read_file strips the raw values", "series" not in brief and
      brief["proof_level"] == "verified", sorted(brief)[:6])

wrong = build(os.path.join(FIX, "synthetic_badaxis.bin"), "CV", "Cyclic Voltammetry",
              params, truth_y[:597])
dw = binio.read_bin(wrong)
check("point-count mismatch refuses to fake an axis",
      dw["axis"] != "reconstructed" and any("电势轴没重建" in w for w in dw["warnings"]),
      (dw["axis"], dw["warnings"]))

# ------------------------------------------------- data region must match the header
print()
print("数据区长度与头部点数必须对上（截断/padding 不许静默平移测量列）")

size_cases = (
    ("少 1 个 float：尾部被截断 4 字节",
     dict(values=truth_y[:599], count_field=600)),
    ("正文多 4 字节 padding",
     dict(values=truth_y, count_field=600, tail=b"\x00\x00\x00\x00")),
    ("正文少 40 个点，计数域仍报 600",
     dict(values=truth_y[:560], count_field=600)),
)
for ncase, (label, kw) in enumerate(size_cases):
    broken = build(os.path.join(FIX, "synthetic_len_%d.bin" % ncase), "CV",
                   "Cyclic Voltammetry", params, **kw)
    try:
        binio.read_bin(broken)
        check("长度不符就报错：%s" % label, False, "no error raised")
    except binio.BinFormatError as exc:
        check("长度不符就报错：%s" % label, "数据区长度与头部点数不符" in str(exc),
              str(exc)[:90])
    except Exception as exc:
        check("长度不符就报错：%s" % label, False, repr(exc)[:90])

it = build(os.path.join(FIX, "synthetic_it.bin"), "i-t", "Amperometric i-t Curve",
           {"init": 0.5, "high": 0.5, "low": 0.0, "segments": 1, "interval": 0.1},
           [6e-5] * 50)
di = binio.read_bin(it)
check("i-t gets a time axis", di["axis"] == "uniform_from_interval" and
      abs(di["series"]["x"][10] - 1.0) < 1e-6, (di["axis"], di["series"]["x"][:3]))
check("合理间隔不产生时间轴警告",
      not any("时间轴没生成" in w for w in di["warnings"]), di["warnings"])

# ------------------------------------------------- i-t interval gate
print()
print("i-t 的采样间隔要过门禁：反向/天文数字不许造轴")

for ncase, (label, bad_step, must) in enumerate((
        ("负间隔", -0.1, "反向"),
        ("1e9 秒的间隔（≈1500 年）", 1e9, "采样间隔"),
        ("1e-30 秒的间隔", 1e-30, "采样间隔"))):
    badit = build(os.path.join(FIX, "synthetic_it_bad_%d.bin" % ncase), "i-t",
                  "Amperometric i-t Curve",
                  {"init": 0.5, "high": 0.5, "low": 0.0, "segments": 1,
                   "interval": bad_step}, [6e-5] * 50)
    dbad = binio.read_bin(badit)
    # 警告里报的必须是"实际读到的"间隔（float32 往返后的值），不是我们写进去的十进制字面量
    stored = repr(struct.unpack("<f", struct.pack("<f", bad_step))[0])
    check("i-t %s 不给轴" % label,
          dbad["axis"] == "none" and dbad["series"]["x"] is None,
          (dbad["axis"], dbad["series"]["x"]))
    check("i-t %s 说明为什么不可信" % label,
          any(("时间轴没生成" in w) and (must in w) and (stored in w)
              for w in dbad["warnings"]),
          dbad["warnings"])

p3 = os.path.join(FIX, "not_a_bin.bin")
with open(p3, "wb") as fh:
    fh.write(b"MZ\x90\x00" + b"\x00" * 2000)
try:
    binio.read_bin(p3)
    check("non-workstation .bin rejected", False, "no error raised")
except binio.BinFormatError as exc:
    check("non-workstation .bin rejected", "文件头" in str(exc), str(exc)[:60])

p4 = os.path.join(FIX, "truncated.bin")
with open(p4, "wb") as fh:
    fh.write(binio.MAGIC + b"\x02\x00")   # magic + 2 bytes: too short for _u32(buf, 4)
try:
    binio.read_bin(p4)
    check("truncated .bin raises BinFormatError", False, "no error raised")
except binio.BinFormatError as exc:
    check("truncated .bin raises BinFormatError", "不完整" in str(exc), str(exc)[:60])
except Exception as exc:
    check("truncated .bin raises BinFormatError", False, repr(exc)[:80])

# A garbage header must not hang the caller: segments is a raw header float.
import time
t0 = time.time()
n = binio.expected_points({"init": 0.0, "high": 1.0, "low": -1.0,
                           "segments": 1e11, "interval": 0.01}, True)
check("garbage segments is clamped, not a day-long loop",
      time.time() - t0 < 5.0 and n > 0, (n, round(time.time() - t0, 3)))


def txt_rows(fn):
    rows = []
    for ln in open(fn, encoding="latin-1").read().splitlines():
        m = re.match(r"^\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*(?:[eE][-+]?\d+)?)\s*$", ln)
        if m:
            rows.append((float(m.group(1)), float(m.group(2))))
    return rows


if os.path.isdir(REAL):
    bins = sorted(glob.glob(os.path.join(REAL, "*.bin")))
    check("real folder has files", bool(bins), len(bins))
    for fn in bins:
        try:
            r = binio.read_bin(fn)
            check("real parses: %s" % os.path.basename(fn), r["points"] > 0 and
                  all(v == v for v in r["series"]["y"]), (r["technique"], r["points"]))
            twin = fn[:-4] + ".txt"
            if os.path.isfile(twin):
                ref = txt_rows(twin)
                n = min(len(ref), r["points"])
                badE = sum(1 for i in range(n) if r["series"]["x"] and
                           abs(r["series"]["x"][i] - ref[i][0]) > 5e-4)
                badI = sum(1 for i in range(n)
                           if abs(r["series"]["y"][i] - ref[i][1]) > 1e-3 * max(1e-6, abs(ref[i][1])))
                check("vs vendor export: %s" % os.path.basename(fn),
                      n > 0 and badE == 0 and badI == 0,
                      (n, badE, badI))
        except Exception as exc:
            check("real parses: %s" % os.path.basename(fn), False, repr(exc)[:90])
else:
    print("  skip  real sample folder not present (%s)" % REAL)

print()
if fails:
    print("FAILED (%d): %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all binio checks passed")
