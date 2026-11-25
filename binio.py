"""Reader for electrochemical-workstation binary exports (.bin).

The files this targets start with 80 F2 1B 00, carry a technique tag (CV / LSV /
IMP / i-t) plus its long name and a build string ("r0815"), and end with one
flat array of little-endian float32. The total element count is stored twice as
adjacent u32 fields inside the header, which is what makes the header length
derivable instead of guessed.

What is NOT claimed here: which of the two arrays is potential and which is
current. Nothing in the header identifies them, and on the sample set both
arrays often sit in the same numeric band, so guessing would silently mislabel
axes. The reader returns the arrays in file order plus the technique name and
lets the caller (or origin_view) decide.
"""

import os
import struct

MAGIC = b"\x80\xf2\x1b\x00"
MAX_HEADER = 2400
# 计数域之后还有一段固定长度的头部（技术名之后的方法表等），到数据区起点为止。
# 原来数据区起点用 `size - 4n` 反推，于是 `start + 4n == size` 恒成立，文件尾部
# 被截断或多出字节在结构上永远查不出来：少 4 字节时 y 整体前移 4 字节（第一个
# 点是头部字节伪造成的 0.0，末值丢失，点数还报 600）；多 4 字节时最后一个点变
# padding 0。真样本实测这段长度是 944（1445 - (493+8)），所以把它当格式常量，
# 用头部位置算数据起点，再用文件长度校验点数。
HEADER_TAIL = 944


class BinFormatError(ValueError):
    pass


def _u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def _technique(buf):
    """The short tag is not NUL padded: 'CV' is 2 bytes, 'LSV'/'IMP'/'i-t' 3, and
    the u32 name length starts immediately after it, so locate that boundary."""
    for p in range(8, 15):
        if p + 4 > len(buf):
            break
        n = _u32(buf, p)
        if not (4 <= n <= 40) or p + 4 + n > len(buf):
            continue
        raw = buf[p + 4:p + 4 + n]
        if all(32 <= c < 127 for c in raw):
            tag = buf[8:p].decode("latin-1").strip().rstrip("\x00")
            build = ""
            at = buf.find(b"r0", p + 4 + n, p + 4 + n + 200)
            if at >= 0:
                end = buf.find(b"\x00", at, at + 16)
                if end > at:
                    build = buf[at:end].decode("latin-1")
            return tag, raw.decode("latin-1"), build
    return buf[8:12].split(b"\x00")[0].decode("latin-1").strip(), "", ""


def _find_stream(buf):
    """Return (total_floats, data_offset) from the duplicated u32 length field.

    数据区起点由"计数域结束 + HEADER_TAIL"算出，不再由文件长度反推：
    反推出来的起点让"数据区长度 == 4*点数"变成恒等式，截断和尾部 padding
    都检测不到，y 会整体平移却仍然报满点数——静默篡改测量列。
    """
    size = len(buf)
    mismatch = None
    for off in range(16, min(1400, size - 12)):
        n = _u32(buf, off)
        if n != _u32(buf, off + 8) or not (2 <= n <= (size - off) // 4):
            continue
        start = off + 8 + HEADER_TAIL
        if start <= off + 12 or start > MAX_HEADER:
            continue
        if size - start != 4 * n:
            if mismatch is None or n > mismatch[0]:
                mismatch = (n, start, size - start)
            continue
        vals = struct.unpack_from("<%df" % n, buf, start)
        if any(v != v or abs(v) > 1e15 for v in vals):
            continue
        return n, start
    if mismatch is not None:
        n, start, available = mismatch
        raise BinFormatError(
            "数据区长度与头部点数不符：头部计数域说 %d 个 float（%d 字节），"
            "从第 %d 字节到文件尾只有 %d 字节。文件可能被截断或有尾部附加数据，"
            "按这个长度读会把测量列整体平移，所以不读" % (n, 4 * n, start, available))
    raise BinFormatError(
        "找不到成对出现的 float32 计数域（文件可能是别的 .bin 格式，或已被截断）")


PARAM_BLOCK_BACK = 600          # the method block sits 600 bytes before the data
P = {"init": 0, "high": 8, "low": 12, "segments": 28, "interval": 32,
     "rate": 40, "quiet": 44}
# i-t / CA / CP 的采样间隔门禁：一个采样点 100 ns~2.8 小时都可能，再往外就不是
# 电化学采样；总时长 1 µs~3 年之外同样当成头部读错。数字宽是故意的——门禁只拦
# "反向/天文数字"，不替用户的实验方法做判断。
MIN_STEP = 1e-7
MAX_STEP = 1e4
MIN_SPAN = 1e-6
MAX_SPAN = 1e8


def _params(buf, header):
    base = header - PARAM_BLOCK_BACK
    if base < 16 or base + 48 > len(buf):
        return {}
    f = struct.Struct("<f")
    out = {}
    for key, off in P.items():
        raw = f.unpack_from(buf, base + off)[0]
        out[key] = raw if raw == raw and abs(raw) < 1e12 else 0.0
    out["segments"] = int(round(out["segments"]))
    return out


def build_axis(params, n, positive_first=True):
    """Triangle wave between low and high, stepping by the sample interval.

    Each leg emits its own step count and the switching potential belongs to the
    NEXT leg, which is what reproduces the vendor's own text export point for
    point (verified against ecsa100.txt: 600 points, reversals every 30).
    """
    init, high, low = params["init"], params["high"], params["low"]
    step = params["interval"]
    if not (high > low > -1e9) or step <= 0:
        return None, 0
    out = []
    e = init
    d = 1 if positive_first else -1
    guard = 0
    while len(out) < n and guard < 100000:
        guard += 1
        limit = high if d > 0 else low
        steps = int(round(abs(limit - e) / step))
        if steps <= 0:
            d = -d
            continue
        for k in range(steps):
            if len(out) >= n:
                break
            out.append(e)
            e = limit if k == steps - 1 else e + d * step
        d = -d
    return out, guard


def expected_points(params, positive_first=True):
    """Points the declared method produces, used to confirm the sweep polarity."""
    init, high, low = params["init"], params["high"], params["low"]
    step = params["interval"]
    if high <= low or step <= 0:
        return 0
    # segments is a raw float from the header with no plausibility filter of its
    # own; a garbage value like 1e11 would make this loop for days on the COM
    # thread and wedge the whole bridge. Real methods are two-digit.
    segments = min(max(1, int(params["segments"])), 100000)
    total = 0
    e = init
    d = 1 if positive_first else -1
    for _ in range(segments):
        limit = high if d > 0 else low
        steps = int(round(abs(limit - e) / step))
        if steps <= 0:
            d = -d
            limit = high if d > 0 else low
            steps = int(round(abs(limit - e) / step))
        total += steps
        e = limit
        d = -d
    return total


def interval_problem(params, total):
    """i-t / CA / CP 的采样间隔能不能用来造时间轴？返回 None 或"为什么不可信"。

    原来只要头部 interval 非 0 就无条件造 `axis=[i*interval]`，于是一个被读成
    -0.1 的间隔画出反向时间轴、1e9 画出到 4.9e10 秒（约 1500 年）的轴，两者
    都没有一句警告；CV/LSV 那条路有"点数必须吻合"的门禁，i-t 这条完全没有。
    门禁用它自己的量纲判：间隔必须为正、有限，且与总点数/头部 quiet 期给出的
    时间跨度相容。宁可不给轴，也不能给一条反向或天文数字的时间轴。
    """
    step = params.get("interval")
    if step is None:
        return "头部没读到采样间隔（方法块不完整），没法按间隔生成 t 轴"
    if not (step > 0) or step != step or step == float("inf"):
        return "头部采样间隔读成 %r s，不是正数，按它画出来的 t 轴会反向" % (step,)
    span = step * max(1, total)
    quiet = params.get("quiet") or 0.0
    if not (MIN_STEP <= step <= MAX_STEP):
        return ("头部采样间隔读成 %r s，超出电化学采样可能的范围（%g~%g s），"
                "这个值不可信" % (step, MIN_STEP, MAX_STEP))
    if span < MIN_SPAN:
        return ("头部采样间隔 %r s × %d 点 = 总时长 %r s，比一次采样的最短时长 %g s "
                "还小，这个值不可信" % (step, total, span, MIN_SPAN))
    if span > MAX_SPAN:
        return ("头部采样间隔 %r s × %d 点 = 总时长 %r s，比 %g s 还长，"
                "这个值不可信" % (step, total, span, MAX_SPAN))
    if quiet > 0 and span < quiet * 1e-6:
        return ("头部采样间隔 %r s × %d 点 = 总时长 %r s，比头部 quiet 期 %r s "
                "小 6 个数量级以上，这个值不可信" % (step, total, span, quiet))
    if quiet > 0 and span > max(quiet, 1.0) * 1e6:
        return ("头部采样间隔 %r s × %d 点 = 总时长 %r s，比头部 quiet 期 %r s "
                "大 6 个数量级以上，这个值不可信" % (step, total, span, quiet))
    return None


def read_bin(path):
    """Decode a workstation .bin: the measured channel plus a reconstructed axis.

    The file holds ONE float32 array -- the measured channel (current in
    amperes, or impedance for EIS). The potential axis is not stored; it is
    regenerated from the method block in the header. That reconstruction is
    checked against the declared point count, so a wrong guess degrades to "no
    axis" instead of silently plotting against the wrong X.
    """
    with open(path, "rb") as fh:
        buf = fh.read()
    if not buf.startswith(MAGIC):
        raise BinFormatError("文件头不是 %s，这不是电化学工作站的 .bin" % MAGIC.hex())
    if len(buf) < 16:
        # A file with the magic but nothing else must fail HERE: _u32(buf, 4)
        # below would otherwise raise a bare struct.error that the callers
        # (they catch BinFormatError) report as internal_error.
        raise BinFormatError("文件只有 %d 字节，头部都不完整，不是有效的 .bin" % len(buf))
    tag, name, build = _technique(buf)
    nseg = _u32(buf, 4)
    total, start = _find_stream(buf)
    y = list(struct.unpack_from("<%df" % total, buf, start))
    params = _params(buf, start)
    warnings = []
    axis, axis_name, axis_kind = None, "", "none"

    if tag in ("CV", "LSV") and params:
        fwd = expected_points(params, True) == total
        rev = expected_points(params, False) == total
        if fwd or rev:
            axis, _ = build_axis(params, total, positive_first=fwd)
            axis_name, axis_kind = "E / V", "reconstructed"
        else:
            warnings.append("按头部方法参数应有 %d 个点，文件里是 %d 个，电势轴没重建"
                            % (expected_points(params, True), total))
    elif tag in ("i-t", "CA", "CP"):
        why = interval_problem(params, total)
        if why is None:
            axis = [i * params["interval"] for i in range(total)]
            axis_name, axis_kind = "t / s", "uniform_from_interval"
        else:
            warnings.append("%s 的时间轴没生成：%s。请按仪器软件里实际的采样间隔"
                            "（共 %d 个点）自己建 t 轴" % (tag, why, total))
    else:
        warnings.append("%s 的 X 轴不在这个文件里（EIS 的频率表是另一套参数）" % (tag or "?",))

    if not name:
        warnings.append("头部没解析出技术全称，只有短标签 %r" % tag)

    def summary(values, label):
        if not values:
            return None
        return {"name": label, "n": len(values), "first": values[0], "last": values[-1],
                "min": min(values), "max": max(values)}

    return {
        "path": os.path.abspath(path),
        "format": "electrochem-bin",
        "magic": MAGIC.hex(),
        "technique": tag,
        "technique_name": name or tag,
        "build": build,
        "header_segments_field": nseg,
        "header_bytes": start,
        "points": total,
        "method": {"init_E": params.get("init"), "high_E": params.get("high"),
                   "low_E": params.get("low"), "segments": params.get("segments"),
                   "step": params.get("interval"), "scan_rate_V_per_s": params.get("rate"),
                   "quiet_time_s": params.get("quiet")},
        "axis": axis_kind,
        "x": summary(axis, axis_name),
        "y": summary(y, "I / A" if tag != "IMP" else "Z / ohm"),
        "warnings": warnings,
        "series": {"x": axis, "y": y},
        "note": ("测量值只有一列（电流 A）；X 轴由头部方法参数重建，"
                 "axis=reconstructed 表示重建点数与文件点数完全吻合。"),
    }
