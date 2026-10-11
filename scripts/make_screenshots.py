"""Regenerate the storefront screenshots in docs/ through the real engine.

Every image here is a genuine Origin export built from closed-form synthetic
data: no measurement from the user's lab ever appears in a screenshot, and the
files are reproducible by rerunning this script.

    python scripts/make_screenshots.py            # 全部四张
    python scripts/make_screenshots.py --only cv  # 只重做一张

The screenshots are declared to the plugin market by the repository-root
screenshots.json, which points at these paths.
"""

import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import originlab  # noqa: E402

DOCS = os.path.join(ROOT, "docs")


def _cv():
    """A synthetic reversible voltammogram: forward and reverse sweep, mA/cm2."""
    pts = []
    e_up, e_dn, step = 0.8, -0.2, 0.005
    e = e_up
    direction = -1.0
    while True:
        anodic = 0.42 * math.exp(-((e - 0.42) ** 2) / 0.0045)
        cathodic = -0.36 * math.exp(-((e + 0.06) ** 2) / 0.0055)
        base = 0.02 * e + 0.006 * math.sin(e * 9.0)
        pts.append((round(e, 4), round(anodic + cathodic + base, 5)))
        e = round(e + direction * step, 4)
        if direction < 0 and e <= e_dn:
            direction = 1.0
        if direction > 0 and e >= e_up:
            break
    return {"E / V": [p[0] for p in pts], "i / mA cm-2": [p[1] for p in pts]}


def _bode(n=41):
    """Randles-style synthetic impedance: log-frequency sweep."""
    rs, rct, c = 12.0, 180.0, 6e-5
    freq, zre, zim = [], [], []
    for i in range(n):
        f = 1e5 / (10 ** (i * 0.175))
        w = 2 * math.pi * f
        den = 1 + (w * rct * c) ** 2
        freq.append(round(f, 4))
        zre.append(round(rs + rct / den, 4))
        zim.append(round(w * rct ** 2 * c / den, 4))
    return {"Freq / Hz": freq, "Z' / ohm": zre, "Z'' / ohm": zim}


def _grid(rows=12, cols=12):
    return [[round(3.0 * math.sin(i / 2.4) + 2.0 * math.cos(j / 3.1)
                   + 0.6 * math.sin((i + j) / 2.0), 4) for j in range(cols)] for i in range(rows)]


def _workstation_txt():
    """A CH Instruments style export, written to a temp file, to show the .txt route."""
    lines = [
        "Jan. 04, 2026   09:12:07", "Linear Sweep Voltammetry",
        "File: d:\\demo\\synthetic_lsv.txt", "Data Source:  Experiment",
        "Instrument Model:  CHI660E", "Header: ", "Note: ", "",
        "Init E (V) = 0.0", "Final E (V) = 0.6", "Scan Rate (V/s) = 0.05",
        "Sample Interval (V) = 0.002", "Quiet Time (sec) = 2",
        "Sensitivity (A/V) = 0.0001", "", "Results:", "", "Channel 1:",
        "Segment 1:", "", "Potential/V, Current/A", "",
    ]
    for i in range(120):
        e = i * 0.005
        lines.append("%.3f, %.3e" % (e, 1.2e-5 + 3.4e-5 / (1 + math.exp(-(e - 0.24) * 26))
                                     + 4e-7 * math.sin(i / 3.0)))
    fd, path = tempfile.mkstemp(prefix="synthetic_lsv_", suffix=".txt")
    os.close(fd)
    with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write("\r\n".join(lines) + "\r\n")
    return path


def _finish(graph, out, label):
    try:
        originlab.legend_entries(graph, show=False)
    except originlab.OriginError:
        pass   # a heat map has no legend box to hide
    e = originlab.export(graph, out, fmt="png", width=1600)
    print("  %-14s -> %s  (%d 字节, %s)" % (label, os.path.basename(out), e["bytes"],
                                            e.get("dimensions")))
    return e


def _line(graph, color="blue", width=2.5, series=0, symbol=0, size=0.0):
    """A 1 px black hairline reads as a broken chart in a screenshot."""
    args = {"graph": graph, "series": series, "color": color, "line_width": width}
    if symbol:
        args["symbol_kind"] = symbol
    if size:
        args["symbol_size"] = size
    originlab.style(**args)


def shot_cv():
    sheet = originlab.write_columns(_cv())
    p = originlab.plot(sheet["worksheet"], x=1, y=2, plot_type="line",
                       title="Synthetic cyclic voltammogram")
    originlab.style(p["graph"], x_title="E / V", y_title="i / mA cm-2")
    _line(p["graph"], "blue", 2.5)
    _finish(p["graph"], os.path.join(DOCS, "shot-cv.png"), "CV")
    return p["graph"]


def shot_bode():
    data = _bode(n=25)
    freq = data["Freq / Hz"]
    sheet = originlab.write_columns({"Freq / Hz": freq, "|Z| / ohm":
                                     [round(math.hypot(zr, zi), 3) for zr, zi
                                      in zip(data["Z' / ohm"], data["Z'' / ohm"])]})
    p = originlab.plot(sheet["worksheet"], x=1, y=2, plot_type="line_symbol",
                       title="Synthetic Bode plot")
    st = originlab.style(p["graph"], x_title="Frequency / Hz", y_title="|Z| / ohm",
                         x_scale="log10")
    read = [o for o in st.get("applied", []) if o.get("op") == "x_scale"]
    if not read or read[0].get("readback") != 2:
        raise SystemExit("横轴没能改成对数（读回 %r），不导出" % (read or "没有这项",))
    # A log axis auto-ranges over the whole decade grid; without this the curve
    # sits in a third of the panel and the tick labels collide.
    originlab.style(p["graph"], xlim=[min(freq) * 0.6, max(freq) * 1.6])
    _line(p["graph"], "red", 2.0, symbol=2, size=7.0)
    _finish(p["graph"], os.path.join(DOCS, "shot-eis.png"), "EIS")
    return p["graph"]


def shot_heatmap():
    m = originlab.matrix_plot(_grid(), template="heat_map", title="Synthetic response surface")
    graph = m.get("graph") or m.get("graph_page")
    originlab.style(graph, x_title="Applied potential / V", y_title="Cycle",
                    layer=0)
    _finish(graph, os.path.join(DOCS, "shot-heatmap.png"), "Heat map")
    return graph


def shot_workstation():
    path = _workstation_txt()
    try:
        imp = originlab.import_file(path)
        cols = [c["label"] for c in imp["columns"]]
        if cols[:2] != ["Potential", "Current"]:
            raise SystemExit("工作站 txt 的列名没解析成 Potential/Current，而是 %s" % cols)
        p = originlab.plot(imp["worksheet"], x=1, y=2, plot_type="line",
                           title="Workstation .txt import")
        originlab.style(p["graph"], x_title="E / V", y_title="I / A")
        _line(p["graph"], "green", 2.5)
        _finish(p["graph"], os.path.join(DOCS, "shot-workstation.png"), "Workstation txt")
        return p["graph"]
    finally:
        os.remove(path)


SHOTS = {"cv": shot_cv, "eis": shot_bode, "heatmap": shot_heatmap,
         "workstation": shot_workstation}


def main():
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    os.makedirs(DOCS, exist_ok=True)
    for name, fn in SHOTS.items():
        if only and name != only:
            continue
        print("生成 %s ..." % name)
        fn()
    originlab.close_pages("graph")
    originlab.close_pages("workbook")
    originlab.close_pages("matrix")
    print("完成，图片在 %s" % DOCS)


if __name__ == "__main__":
    main()
