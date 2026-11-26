"""origin-bridge MCP server.

Deliberately a newline-delimited JSON-RPC loop over stdio instead of the official
`mcp` SDK's asyncio transport: on Windows the SDK builds an asyncio
ProactorEventLoop, whose self-pipe falls back to a loopback
socketpair() (127.0.0.1 listen+connect+accept). Host firewalls that block
loopback accepts make that hang forever, the MCP handshake never completes, and
the client stalls for its full 60s request timeout before giving up.

One reader thread parses stdin so that notifications/cancelled can be honored
while a tool runs; tools themselves still execute one at a time on the main
thread (the engine has one COM worker and global handle state).

Usage:  python server.py            # stdio, for dsh / any MCP client
        python server.py --tools   # print the tool table and exit
"""

import json
import os
import queue
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import originlab  # noqa: E402

SERVER_NAME = "origin-bridge"
SERVER_VERSION = "0.4.22"
PROTOCOL = "2024-11-05"


def _enum(*values):
    return sorted({str(v) for v in values if v is not None and str(v) != ""})


# Every enum below is DERIVED from the engine's own tables instead of being typed
# out a second time. Measured drift in 0.4.21: the engine accepted `gaussian`,
# `nlfit`, `natural_log`, `workbook`, `tiff`, `jpeg` while the schemas did not list
# them, so a model that used a legitimate value was refused, and a model that used a
# schema-listed value the engine rejected got an engine error. Two lists, one truth.
_FIT_KINDS = _enum("linear", "nlfitsing", "nlfit", *originlab.FIT_PRESETS)
_SCALE_NAMES = _enum(*list(originlab.SCALE_TYPES) + list(originlab.SCALE_NAMES.values()))
_PAGE_KINDS = _enum(*originlab.PAGE_KINDS)
_IMAGE_FMTS = _enum("png", "tif", "tiff", "jpg", "jpeg", "svg", "pdf", "emf")
_DATA_FMTS = _enum(*originlab._DATA_FMTS)
_DATA_ENCODINGS = _enum(*originlab._DATA_ENCODINGS)
_PLOT_TYPES = _enum(*originlab.PLOT_CODES)
_LAYER_TYPES = _enum(*originlab.LAYER_TYPES)
_LEGEND_POSITIONS = _enum(*originlab.LEGEND_POSITIONS)
_MATRIX_TEMPLATES = _enum(*originlab.MATRIX_TEMPLATES)


def _write(a):
    if a.get("rows"):
        return originlab.write_block(a.get("headers") or [], a["rows"], a.get("book_name", ""))
    return originlab.write_columns(a.get("columns") or {}, a.get("book_name", ""))

TOOLS = [
    {
        "name": "origin_status",
        "description": "检查能否连上本机 Origin/OriginPro，返回版本、Python、已打开的页面。任何绘图操作前先用它确认连接。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "run": lambda a: originlab.status(),
    },
    {
        "name": "origin_import",
        "description": "把本地数据文件导入 Origin 新建的工作表。支持 .dat/.csv/.txt/.tsv/.xls/.xlsx/.wks，以及电化学工作站的二进制 .bin（CV/LSV/EIS/i-t，头部带 80F21B00 魔数那种）。返回 worksheet 句柄和列清单，后续步骤原样传回该句柄。.bin 会带 role_confidence：ambiguous 表示两列同量级、分不清电势/电流，这时先 origin_view 看图再定轴标题。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "数据文件绝对路径"},
                "book_name": {"type": "string", "description": "可选：设给工作表的长名（Long Name），不是短名"},
            },
            "required": ["path"],
        },
        "run": lambda a: originlab.import_file(a["path"], a.get("book_name", "")),
    },
    {
        "name": "origin_write",
        "description": "用内联数据建工作表。窄表用 columns={列名:[数值...]}；宽表（>5 列）用 headers+rows 二维块，一次写完，比逐列快很多。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "columns": {"type": "object", "description": '{"Time": [0,1,2], "Signal": [3,4,5]}'},
                "headers": {"type": "array", "description": "配合 rows 使用：列名列表"},
                "rows": {"type": "array", "description": "配合 headers 使用：二维数据块"},
                "book_name": {"type": "string"},
            },
        },
        "run": lambda a: _write(a),
    },
    {
        "name": "origin_formula",
        "description": "给某列设计算公式（Fx），让它随兄弟列自动重算，用于派生列。列号从 1 开始或给列名。会读回该列算出的行数和首末值。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "worksheet": {"type": "string"},
                "col": {"type": ["integer", "string"], "description": "1 起始列号或列名"},
                "formula": {"type": "string", "description": "如 Col(1)*2.5+0.1；LabTalk 写法"},
                "label": {"type": "string"},
                "units": {"type": "string"},
            },
            "required": ["worksheet", "col", "formula"],
        },
        "run": lambda a: originlab.column_formula(a["worksheet"], a["col"], a["formula"],
                                                  a.get("label", ""), a.get("units", "")),
    },
    {
        "name": "origin_plot",
        "description": "在工作表上画图。x/y 用 1 起始列号或列名；y 可以给列表，一次把多条曲线画进同一层。plot_type: line|scatter|line_symbol|column。给 template 用 Origin 自带图模建页（如 doubley/heat_map/line），给 graph+layer 则往已有图页的指定层里加曲线。返回里有 warnings 时代表图可能不可读（线性轴跨数量级等），要把选项交给用户决定。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "worksheet": {"type": "string", "description": "origin_import/origin_write 返回的 worksheet 句柄"},
                "x": {"type": ["integer", "string"], "default": 1},
                "y": {"type": ["integer", "string", "array"], "default": 2,
                      "description": "列号/列名，或列表 [2,3,4] 一次画多条"},
                "plot_type": {"type": "string", "enum": _PLOT_TYPES, "default": "line"},
                "graph_name": {"type": "string"},
                "title": {"type": "string", "description": "图层标题"},
                "graph": {"type": "string", "description": "已有图页句柄；配合 layer 往该层加曲线"},
                "layer": {"type": "integer", "default": 0, "description": "目标层索引（双 Y 轴的第二层=1）"},
                "template": {"type": "string", "description": "Origin 图模名，如 line/scatter/doubley/heat_map/cmap/mesh"},
            },
            "required": ["worksheet"],
        },
        "run": lambda a: originlab.plot(a["worksheet"], a.get("x", 1), a.get("y", 2),
                                        a.get("plot_type", "line"), a.get("graph_name", ""),
                                        a.get("title", ""), graph=a.get("graph", ""),
                                        layer=a.get("layer", 0), template=a.get("template", "")),
    },
    {
        "name": "origin_read_file",
        "description": "只解析数据文件、不碰 Origin：返回编码、分隔符、表头行数、每列长名/单位/有效行数，以及仪器参数 metadata。拿到陌生 .dat/.csv/.txt 先用它看清结构，再决定 origin_import 用哪几列。支持 #、//、!、; 注释、BOM、GBK、空格对齐、Fortran 的 1.5d+00 指数、NA/- 缺失值、Origin 三行表头（Long Name/Units/Comments），也支持电化学工作站（CH Instruments 等）导出的 txt：前面几十行参数说明会被跳进 metadata，紧贴数据的 Potential/V, Current/A 这类表头按斜杠拆出列名和单位。编码覆盖 UTF-8 / GBK / UTF-16（有无 BOM）/ cp1252；<0.001、2.5±0.1、25 mV、1,234.5、(500)、2.5* 这类被装饰的数值会解出来并在 warnings 里说明代入了什么。每列还带 sample（前 3 个原始值，缺值为 null），用来抽查解析对不对——完整数据仍然只进 Origin。二进制表格 .xls/.xlsx/.wks 不在它的范围内，会明确返回 unsupported_file_type，请用 origin_import。.bin 走另一条解析路线：返回技术标签（CV/LSV/IMP/i-t）、点数、两个 float32 数组的范围和 role_confidence。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "数据文件绝对路径（含 .bin）"},
                "comment": {"type": "string", "description": "可选：强制指定注释前缀，默认自动识别"},
            },
            "required": ["path"],
        },
        "run": lambda a: originlab.read_file(a["path"], a.get("comment")),
    },
    {
        "name": "origin_read_sheet",
        "description": "把 Origin 工作表里的数据读回来（这是 import 的反向）：返回每列的长名/单位/显示格式/类型（numeric、text、mixed、empty）/有效行数，以及前 rows 个值（缺值为 null）。import、write、formula 之后想确认「到底写进去了什么」，或者 open 了别人的 .opju 想知道里面是什么数据，用它。给 path 就把整张表写成 csv/tsv/txt 并回读核对（表头、行数、每行格数、抽查格子），返回字节数与 sha256。实测限制：带显示格式的列导出的是 Origin 原始数值（日期是序列号，45292.0 = 2024-01-01），屏幕上的那段文字只在前 3 行的 sample_display 里；Origin 自带的 expASC 走不通（lt_exec 一律返回 False 且不落盘），文件是桥自己写的。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "worksheet": {"type": "string", "description": "工作表句柄（origin_import / origin_write 返回的那个）"},
                "rows": {"type": "integer", "default": 20,
                         "description": "回传多少行的值，1–200；要全部数据请用 path 导出"},
                "columns": {"type": "array", "description": "只读这几列：1 起始列号或列名；省略=全部"},
                "path": {"type": "string", "description": "可选：把整张表写到这里（扩展名按 fmt 自动修）"},
                "fmt": {"type": "string", "enum": _DATA_FMTS, "default": "csv"},
                "encoding": {"type": "string",
                             "enum": _DATA_ENCODINGS,
                             "default": "utf-8-sig",
                             "description": "默认带 BOM 的 UTF-8，中文 Windows 上 Excel 双击打开不乱码"},
            },
            "required": ["worksheet"],
        },
        "run": lambda a: originlab.read_sheet(a["worksheet"], a.get("rows", 20),
                                              a.get("columns") or [], a.get("path", ""),
                                              a.get("fmt", "csv"),
                                              a.get("encoding", "utf-8-sig")),
    },
    {
        "name": "origin_style",
        "description": "改图并逐项读回验证：轴标题、对数轴、坐标范围与步长、逐曲线颜色/线宽/符号形状/符号大小/填充/透明度。读不回来的项会进 failed 列表，不会假装成功。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graph": {"type": "string", "description": "origin_plot 返回的 graph 句柄"},
                "layer": {"type": "integer", "default": 0, "description": "层索引，0 起"},
                "series": {"type": "integer", "default": 0, "description": "该层里的第几条曲线，0 起"},
                "x_title": {"type": "string"},
                "y_title": {"type": "string"},
                "x_scale": {"type": "string", "enum": _SCALE_NAMES},
                "y_scale": {"type": "string", "enum": _SCALE_NAMES},
                "xlim": {"type": "array", "description": "[from,to] 或 [from,to,step]"},
                "ylim": {"type": "array", "description": "[from,to] 或 [from,to,step]"},
                "xtick": {"type": "number", "description": "X 刻度步长"},
                "ytick": {"type": "number", "description": "Y 刻度步长"},
                "color": {"type": "string", "description": "如 #0072B2 或 Origin 颜色名"},
                "line_width": {"type": "number"},
                "symbol_kind": {"type": "integer", "description": "Origin 符号形状编号，如 3=圆 1=方"},
                "symbol_size": {"type": "number"},
                "symbol_interior": {"type": "integer", "enum": [0, 1, 2, 3],
                                     "description": "0 无符号 1 实心 2 空心 3 点心"},
                "transparency": {"type": "number", "description": "0-100 百分比"},
                "fill_area": {"type": ["boolean", "integer"], "description": "线下填充（面积图效果，1/true 都行）"},
            },
            "required": ["graph"],
        },
        "run": lambda a: originlab.style(
            a["graph"], layer=a.get("layer", 0), series=a.get("series", 0),
            x_title=a.get("x_title", ""), y_title=a.get("y_title", ""),
            x_scale=a.get("x_scale", ""), y_scale=a.get("y_scale", ""),
            xlim=a.get("xlim"), ylim=a.get("ylim"),
            xtick=a.get("xtick", 0.0), ytick=a.get("ytick", 0.0),
            color=a.get("color", ""), symbol_kind=a.get("symbol_kind", 0),
            symbol_size=a.get("symbol_size", 0.0), symbol_interior=a.get("symbol_interior", 0),
            line_width=a.get("line_width", 0.0), transparency=a.get("transparency", 0.0),
            fill_area=a.get("fill_area", False)),
    },
    {
        "name": "origin_fit",
        "description": "拟合。kind=linear 线性（Origin 标准版可用）；非线性直接用预设名 kind=gauss|lorentz|voigt|expdec1|expdec2|sine|power|logistic|boltzmann|doseresp|cubic（本机逐个实测可用；poly3=cubic、expdecay=expdec1 是别名），或 kind=nlfitsing + func 给 Origin 的函数名（区分大小写：Gauss 对、gauss1 不存在）。func 只能是内置函数名，自定义表达式不支持。只给 func 不给 kind 时按非线性执行并在 note 里说明。可固定参数 fixed、给初值 starts、加边界 bounds，并可生成报告表。缺失值已消毒为 null。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "worksheet": {"type": "string"},
                "x": {"type": ["integer", "string"], "default": 1},
                "y": {"type": ["integer", "string", "array"], "default": 2},
                "kind": {"type": "string", "enum": _FIT_KINDS, "default": "linear"},
                "func": {"type": "string", "description": "Origin 内置函数名（区分大小写），如 Gauss / ExpDec1；给了 func 就按非线性拟合，不要写自定义表达式"},
                "fixed": {"type": "object", "description": "固定参数 {参数名: 值}，如 {\"y0\": 0}"},
                "starts": {"type": "object", "description": "初值 {参数名: 值}；linear 用 Slope/Intercept"},
                "bounds": {"type": "object", "description": "边界 {参数名: [下界, 上界]}，null 表示不设"},
                "make_report": {"type": "boolean", "default": False, "description": "生成 Origin 拟合报告表"},
                "full": {"type": "boolean", "default": False,
                         "description": "true 返回 Origin 的完整报告字典（很大）；默认只回参数值与关键统计"},
            },
            "required": ["worksheet"],
        },
        "run": lambda a: originlab.fit(a["worksheet"], a.get("x", 1), a.get("y", 2),
                                       a.get("kind", "linear"), a.get("func", ""),
                                       fixed=a.get("fixed"), starts=a.get("starts"),
                                       bounds=a.get("bounds"), make_report=a.get("make_report", False),
                                       full=a.get("full", False)),
    },
    {
        "name": "origin_export",
        "description": "把图页导出为图片文件，并校验文件确实存在、够大、文件头正确（Origin 有静默失败的历史）。fmt: png/tif/jpg/svg/pdf/emf（tiff→tif、jpeg→jpg 会自动改写扩展名）。path 给绝对路径：中文、空格、长路径实测可用，未映射盘/UNC/把目录当文件名返回 bad_path，已存在但只读或被占用返回 target_locked（不覆盖）。文件名里的 # 会被 Origin 换成编号，桥认出后改名回你要的名字并在 warnings 里说明——那是成功，不是失败。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graph": {"type": "string"},
                "path": {"type": "string", "description": "输出文件绝对路径；all_graphs=true 时是目标目录（每个图页导成 <页名>.<fmt>）"},
                "fmt": {"type": "string", "enum": _IMAGE_FMTS,
                        "default": "png"},
                "width": {"type": "integer", "minimum": 100, "maximum": 12000, "default": 1600},
                "all_graphs": {"type": "boolean", "default": False,
                               "description": "true：把工程里所有图页一次性导出到 path 目录，逐页验证；path 是目录"},
            },
        },
        "run": lambda a: (originlab.export_all(a.get("path", ""), a.get("fmt", "png"), a.get("width", 1600))
                          if a.get("all_graphs")
                          else originlab.export(a.get("graph", ""), a.get("path", ""),
                                                a.get("fmt", "png"), a.get("width", 1600))),
    },
    {
        "name": "origin_save_project",
        "description": "保存为可在 Origin 里继续编辑的 .opju 工程文件（不是图片快照）。省略 path 会自动命名。path 给绝对路径，中文/空格/长路径实测可用；未映射盘、UNC、把目录当文件名一律 bad_path，不会假装存好了。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
        "run": lambda a: originlab.save_project(a.get("path", "")),
    },
    {
        "name": "origin_open",
        "description": "打开已有的 .opju/.opj 工程，返回页面清单。打开会替换工程里的所有页面，所以之前拿到的 ws-/gr- 句柄全部作废（返回 handles_reset: true）——要用就用返回的页面名，或重新 origin_import/origin_plot 拿新句柄；打开失败时旧句柄同样作废。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "run": lambda a: originlab.open_project(a["path"]),
    },
    {
        "name": "origin_inspect",
        "description": "读回当前项目真实状态：有哪些页面、图页有几层、每层轴标题和曲线数、已分配的句柄。改图之前先调用它，别猜索引。",
        "inputSchema": {
            "type": "object",
            "properties": {"graph": {"type": "string"}},
        },
        "run": lambda a: originlab.inspect(a.get("graph", "")),
    },
    {
        "name": "origin_figure",
        "description": "主路径：一次调用完成 导入/写数 → 画图 → 轴标题 → 导出图片 → 保存可编辑 .opju。用户只想“把这份数据画成图并给我文件”时用它，不要拆成多步。返回里若有 warnings，可能是两类：图不可读（例如线性横轴跨了几个数量级，点全挤在最左边）——把 next_actions 里的方案讲给用户让他选，不要自己擅自改轴；或是路径被代劳了（文件名里的 # 被 Origin 换成编号后改回、相对路径落到进程工作目录）——转达给用户即可，交付本身是成功的。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "数据文件路径（与 columns 二选一）"},
                "columns": {"type": "object", "description": "内联数据 {列名: [值...]}，优先用 source"},
                "x": {"type": ["integer", "string"], "default": 1},
                "y": {"type": ["integer", "string", "array"], "default": 2},
                "plot_type": {"type": "string", "enum": _PLOT_TYPES, "default": "line"},
                "title": {"type": "string"},
                "x_title": {"type": "string"},
                "y_title": {"type": "string"},
                "fmt": {"type": "string", "enum": _IMAGE_FMTS,
                        "default": "png"},
                "width": {"type": "integer", "minimum": 100, "maximum": 12000, "default": 1600},
                "output_dir": {"type": "string", "description": "交付目录：图片 + opju 都写到这。不给则只建图、不落盘"},
                "export_path": {"type": "string"},
                "project_path": {"type": "string"},
                "do_project": {"type": "boolean", "default": False,
                               "description": "true 时即使没给目录也存一份 .opju 到 Origin 的 User Files"},
                "template": {"type": "string", "description": "Origin 图模名，如 line/scatter/doubley；省略走默认 plotxy"},
                "legend": {"type": ["boolean", "array"], "default": False,
                           "description": "true=按列长名自动生成图例；或给名称列表写死"},
            },
        },
        "run": lambda a: originlab.figure(
            source=a.get("source", ""), columns=a.get("columns"), x=a.get("x", 1), y=a.get("y", 2),
            plot_type=a.get("plot_type", "line"), title=a.get("title", ""),
            x_title=a.get("x_title", ""), y_title=a.get("y_title", ""), fmt=a.get("fmt", "png"),
            width=a.get("width", 1600), output_dir=a.get("output_dir", ""),
            export_path=a.get("export_path", ""), project_path=a.get("project_path", ""),
            do_project=a.get("do_project"), template=a.get("template", ""),
            legend=a.get("legend", False)),
    },
    {
        "name": "origin_exit",
        "description": "关闭 Origin。默认只退**本桥启动的**实例；如果连上的是用户自己开的 Origin，会报 foreign_instance，确认要关再加 force=true。默认不保存；给 save_to 会先存一份 .opju 再退。",
        "inputSchema": {
            "type": "object",
            "properties": {"save_to": {"type": "string"},
                           "force": {"type": "boolean", "default": False}},
        },
        "run": lambda a: originlab.exit_origin(a.get("save_to", ""),
                                               bool(a.get("force", False))),
    },
    {
        "name": "origin_instances",
        "description": "清点机器上的 Origin 实例：分「后台无窗口」（客户端退出后残留，会占配额）和「前台有窗口」（可能是用户自己开着的项目）。Origin 实例数超上限、或报 connection_error 时先调这个看情况。",
        "inputSchema": {"type": "object", "properties": {}},
        "run": lambda a: originlab.instances(),
    },
    {
        "name": "origin_reclaim",
        "description": "回收残留的 Origin 实例：默认只关**本桥启动过**且没有窗口的实例（本会话记录或上次运行留下的 pid+启动时间记录）；有窗口的一律不动。归属不明的无窗口实例只在 unowned 里报告，不关——确认那确实是本桥的残留后用 include_orphans=true。close_background=false 时只看不动手。强杀后会核实进程确实退出，没退会报 stuck。",
        "inputSchema": {
            "type": "object",
            "properties": {"close_background": {"type": "boolean", "default": True},
                           "include_orphans": {"type": "boolean", "default": False}},
        },
        "run": lambda a: originlab.reclaim_safe(bool(a.get("close_background", True)),
                                                bool(a.get("include_orphans", False))),
    },
    {
        "name": "origin_annotate",
        "description": "在图上加文字标注和参考线（数据坐标系）。比如标峰位、画阈值线。标注没有值可读回，返回 readback_only，要确证用 origin_view 看图。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graph": {"type": "string"},
                "layer": {"type": "integer", "default": 0},
                "labels": {"type": "array", "description": '[{"text":"peak","x":12.5,"y":80}]'},
                "lines": {"type": "array", "description": '[{"x1":0,"y1":50,"x2":23,"y2":50}]'},
            },
            "required": ["graph"],
        },
        "run": lambda a: originlab.annotate(a["graph"], a.get("labels") or [],
                                            a.get("lines") or [], a.get("layer", 0)),
    },
    {
        "name": "origin_layer",
        "description": "给已有图页加一层：双 Y 轴用 layer_type=right，上下多面板用 top/bottom。返回 Origin 自己给这层起的名字（RightY 等）作为证据，以及新层索引。往新层画：origin_plot 时带上 graph 和 layer=该索引。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graph": {"type": "string"},
                "layer_type": {"type": ["string", "integer"], "default": "right",
                               "description": "bottom(=bottom_left)|top|right|left|top_right|bottom_right|inset，或对应整数 0-6"},
            },
            "required": ["graph"],
        },
        "run": lambda a: originlab.add_layer(a["graph"], a.get("layer_type", "right")),
    },
    {
        "name": "origin_legend",
        "description": "图例。不给 entries 时用 legend -r 按各条曲线对应列的长名自动生成；给了就逐行写死。position 用图内位置名（inside_top_right/top_right/bottom_left…）。位置按该层数据坐标写入并读回，文字和可见性都验证。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graph": {"type": "string", "description": "省略则用当前活动图页"},
                "layer": {"type": "integer", "default": 0},
                "entries": {"type": "array", "description": '["sin(x)","cos(x)"]；省略=用列长名自动生成'},
                "position": {"type": "string", "enum": _LEGEND_POSITIONS},
                "show": {"type": "boolean", "default": True},
            },
        },
        "run": lambda a: originlab.legend_entries(a.get("graph", ""), a.get("layer", 0),
                                                  a.get("entries"), a.get("position", ""),
                                                  a.get("show", True)),
    },
    {
        "name": "origin_matrix",
        "description": "网格数据 → Origin 矩阵 → 模板化矩阵图：heat_map 热力图、cmap 三维彩色映射面、mesh 网格面、contline/contgray 等高线、3d/bar3d。rows 是二维数值矩阵。可选 xy_range=[x1,x2,y1,y2] 设坐标范围。给 export_path/project_path 会顺手落盘。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rows": {"type": "array", "description": "二维矩阵 [[...],[...]]，行=Y 方向、列=X 方向"},
                "template": {"type": "string", "enum": _MATRIX_TEMPLATES,
                             "default": "heat_map"},
                "book_name": {"type": "string"},
                "graph_name": {"type": "string"},
                "xy_range": {"type": "array", "description": "[x1,x2,y1,y2] 矩阵坐标范围"},
                "title": {"type": "string"},
                "export_path": {"type": "string"},
                "project_path": {"type": "string"},
            },
            "required": ["rows"],
        },
        "run": lambda a: originlab.matrix_plot(a["rows"], a.get("template", "heat_map"),
                                              a.get("book_name", ""), a.get("graph_name", ""),
                                              a.get("xy_range"), a.get("title", ""),
                                              a.get("export_path", ""), a.get("project_path", "")),
    },
    {
        "name": "origin_chart_presets",
        "description": "列出 40 个科研图表预设（XPS/XRD/UV-Vis/荧光/循环伏安/Arrhenius…）：每个预设带好哪些列、默认轴标题、建议拟合函数。不确定用户要的是哪类图时先查这里，别自己编轴标题。category 可过滤。",
        "inputSchema": {
            "type": "object",
            "properties": {"category": {"type": "string"}},
        },
        "run": lambda a: originlab.chart_presets(a.get("category", "")),
    },
    {
        "name": "origin_chart_preset",
        "description": "看单个预设的完整约定：required_columns / optional_columns / default_axis_titles / default_plot_kind / suggested_fit / extra（如 XPS 的 X 轴要反向）。",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        "run": lambda a: originlab.chart_preset(a["name"]),
    },
    {
        "name": "origin_chart_check",
        "description": "拿数据列名对预设做 dry-run（不碰 Origin）：逐列说清每个必需列匹配到了你哪一列、缺哪列、建议用什么拟合、默认轴标题是什么；也会检查内联数据的形状（文本列、0 行）。渲染前先调它，能省一次失败。列名对不上不代表数据不对——真要用这个预设，origin_chart_render 里用 x=/y= 显式指定列号即可。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "预设名，小写下划线写法（xrd / tg_dtg / bland_altman）"},
                "data": {"type": ["string", "object", "array"], "description": "数据文件路径；也接受内联的 {列名:[值]} 或列名列表"},
                "columns": {"type": "array", "description": "或者直接给列名列表"},
            },
            "required": ["name"],
        },
        "run": lambda a: originlab.chart_check(a["name"], a.get("data"), a.get("columns")),
    },
    {
        "name": "origin_chart_render",
        "description": "按科研预设一步出图：读预设的轴标题/图类型 → 导入数据 → 画图 → 轴标题 → 图例 → 导出图片 + 保存 .opju。用户说“把这个做成 XPS 图”这类需求优先用它。x/y 省略时按预设的必需列自动定位（所以文件列顺序和预设不一致也能画对），列名对不上时用 x=/y= 显式指定列号。实测限制：Origin 只有 line/scatter/line_symbol/column 四种图类型，预设要求 box/radar/sankey/3d 之类会明确报 preset_kind_unsupported 并指出该用哪个工具，不会假装画成折线；预设要双 Y 轴、误差棒、对数轴时这个工具只画主体，缺的部分写在 warnings 里。轴要反向（如 XPS 结合能）时再调 origin_style 设 xlim=[高,低]。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "预设名，小写下划线写法"},
                "source": {"type": "string", "description": "数据文件路径"},
                "columns": {"type": "object", "description": "或内联数据 {列名:[值]}"},
                "x": {"type": ["integer", "string"], "description": "X 列（1 起始或列名）；省略=按预设的第一个必需列"},
                "y": {"type": ["integer", "string"], "description": "Y 列；省略=按预设的第二个必需列"},
                "output_dir": {"type": "string", "description": "交付目录；不给则只建图不落盘"},
                "x_title": {"type": "string", "description": "覆盖预设的 X 轴标题"},
                "y_title": {"type": "string"},
                "title": {"type": "string"},
                "plot_type": {"type": "string", "enum": _PLOT_TYPES},
                "with_legend": {"type": "boolean", "default": True},
                "fmt": {"type": "string", "enum": _IMAGE_FMTS,
                        "default": "png"},
                "width": {"type": "integer", "minimum": 100, "maximum": 12000, "default": 1600},
            },
            "required": ["name"],
        },
        "run": lambda a: originlab.chart_render(
            a["name"], a.get("source", ""), a.get("columns"), a.get("x"), a.get("y"),
            a.get("output_dir", ""), a.get("x_title"), a.get("y_title"), a.get("title"),
            a.get("plot_type"), a.get("with_legend", True), a.get("fmt", "png"),
            a.get("width", 1600)),
    },
    {
        "name": "origin_close_pages",
        "description": "关掉项目里攒下来的页面。长会话里页面攒太多会让 Origin 的 COM 桥报“无效指针”，这时按类型或名字清理。默认只看图页；dry_run=true 只报告不关。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": _PAGE_KINDS, "default": "graph"},
                "names": {"type": "array", "description": '只关这些页面 ["Graph1","Book2"]；省略=该类型全部'},
                "dry_run": {"type": "boolean", "default": False},
            },
        },
        "run": lambda a: originlab.close_pages(a.get("kind", "graph"), a.get("names"),
                                               a.get("dry_run", False)),
    },
    {
        "name": "origin_view",
        "description": "把图页渲染成小图回传（MCP image content），让模型自己看见结果。写后读不回的东西（样式、标注）用它确认，别声称你没看过的效果。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graph": {"type": "string", "description": "省略则用当前活动图页"},
                "width": {"type": "integer", "minimum": 100, "maximum": 2400, "default": 900},
            },
        },
        "run": lambda a: originlab.view(a.get("graph", ""), a.get("width", 900)),
    },
    {
        "name": "origin_labtalk",
        "description": "逃生舱：执行原始 LabTalk，并读回你点名的变量（numeric 读数值表达式，readback 读字符串变量）。实测限制：数值/表达式类语句可用（如 layer.x.type=2、win -a Graph1），但字符串赋值类语句（layer.x.title$=... 、set c1 -l ...）在这台机器上 lt_exec 会返回 False，轴标题等请用 origin_style。returned=false 就是真失败，别重试同一条。破坏性命令会被拦下并返回 labtalk_denied：doc -s/-n/-o（存/新建/打开工程）、win -c/-cd（关/销毁页面）、del/delete、layer -d、page -d、run./run -、system/shell、type -、rmdir/mkdir、quit/exit、close、saveas。这是劝告式护栏、不是沙箱（变量拼接仍可能绕过），别拿它做破坏性操作；页面/句柄管理用 origin_close_pages，保存用 origin_save_project。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "LabTalk 语句，如 'win -a Book1; plotxy iy:=(1,2) plot:=200 ng:=0;'"},
                "readback": {"type": "array", "description": "要读回的字符串变量名，如 ['layer.x.title$']"},
                "numeric": {"type": "array", "description": "要读回的数值表达式，如 ['npy', 'layer.x.to']"},
            },
            "required": ["script"],
        },
        "run": lambda a: originlab.labtalk(a["script"], a.get("readback") or [],
                                           a.get("numeric") or []),
    },
]

_BY_NAME = {t["name"]: t for t in TOOLS}

# Every tool declares its whole surface. Without this, a model that writes
# `{"colour": "#fff", "xscale": "log"}` got the call accepted, the engine read
# `color`/`x_scale` (absent), and NOTHING happened — the answer looked like success
# while the request was silently ignored. Measured on origin_style. Declaring it in
# the schema is also what a client sees, so the contract and the check agree.
for _tool in TOOLS:
    _schema = _tool.get("inputSchema")
    if isinstance(_schema, dict):
        _schema.setdefault("additionalProperties", False)


def _log(msg):
    # Write UTF-8 bytes rather than text: sys.stderr carries the console code page
    # (cp936 here, cp1252 elsewhere), and a stderr.write of a path or tool name the
    # code page cannot encode raises UnicodeEncodeError *inside the reader thread* —
    # which kills the only thread that reads stdin, i.e. the bridge looks hung with
    # no diagnostic. Dropping an unencodable character is always better than dying.
    line = "[%s] %s\n" % (SERVER_NAME, msg)
    try:
        stream = getattr(sys.stderr, "buffer", None)
        if stream is not None:
            stream.write(line.encode("utf-8", "replace"))
            stream.flush()
            return
    except (AttributeError, OSError, ValueError):
        pass
    try:
        sys.stderr.write(line.encode("ascii", "replace").decode("ascii"))
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 - logging must never take the server down
        pass


def _nonfinite(v):
    return isinstance(v, float) and (v != v or v in (float("inf"), float("-inf")))


def _scrub_nonfinite(obj):
    """Replace bare NaN/Infinity with None, reporting how many were replaced.

    Used only on the degraded path: the payload could not be written as strict JSON even
    with allow_nan=False, and answering "internal error" would throw away a result
    the user asked for. Losing one number (null) beats losing the whole answer.
    """
    dropped = []

    def walk(value):
        if _nonfinite(value):
            dropped.append(value)
            return None
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(v) for v in value]
        return value

    return walk(obj), dropped


def _strict_text(body):
    """Serialize a tool payload as strict JSON, or degrade it honestly.

    allow_nan=False has to run HERE and not only in _encode_reply: the payload is
    embedded as a *string* inside content[0].text, so the outer dump never sees the
    numbers nested in it. Measured before the fix: {"v": NaN, "w": Infinity} went
    out as the text `{"v": NaN, "w": Infinity}` while the envelope stayed legal —
    a client that parses `text` as JSON (dsh does) failed on the whole tool result,
    and the "not strict JSON" guard never logged a thing because it was looking at
    the envelope instead of the text.
    """
    try:
        return json.dumps(body, ensure_ascii=False, default=str, allow_nan=False), None
    except (ValueError, TypeError) as exc:
        _log("tool payload was not strict JSON (%r); degrading non-finite numbers" % (exc,))
    if isinstance(body, dict):
        clean, dropped = _scrub_nonfinite(body)
        if dropped:
            notes = list(clean.get("warnings") or [])
            notes.append("返回值里有 %d 个非 JSON 数值（NaN/Infinity），已按 null 交出：%s"
                         % (len(dropped), ", ".join(str(v) for v in dropped[:3])))
            clean["warnings"] = notes
        body = clean
    try:
        return json.dumps(body, ensure_ascii=False, default=str, allow_nan=False), "scrubbed"
    except (ValueError, TypeError):
        return json.dumps({"ok": False, "code": "nonjson_payload",
                           "message": "工具返回值无法序列化成严格 JSON，已丢弃这次结果",
                           "next_actions": ["把参数缩小/改简单后重试",
                                            "若反复出现，用 origin_inspect 确认页面状态"]},
                          ensure_ascii=False), "error"


def _tool_result(call_id, payload, is_error=False):
    content = []
    body = payload
    if isinstance(payload, dict) and payload.get("image_base64"):
        body = {k: v for k, v in payload.items() if k != "image_base64"}
        body["image_returned"] = True
        content.append({"type": "image", "data": payload["image_base64"],
                        "mimeType": payload.get("mime", "image/png")})
    text, degraded = _strict_text(body)
    if degraded == "error":
        is_error = True
    content.append({"type": "text", "text": text})
    return {"jsonrpc": "2.0", "id": call_id, "result": {"content": content, "isError": is_error}}


_JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
               "array": list, "object": dict}


def request_id(value):
    """The id to echo back, or None when it cannot be one.

    JSON-RPC ids are strings or numbers; `true` is not an id, and neither is a list
    or a dict (a dict id would also be unhashable as a key).
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return value


def _validate_args(name, args, schema):
    """Reject wrong-shaped arguments as bad_arguments before the tool runs.

    Only *types* are checked here. Enums and ranges stay with the engine, which
    answers with a more specific code (`bad_position`, `bad_format`, `bad_layer_type`)
    than a generic bad_arguments would. What this fixes is the type confusion: a
    `path: 123` used to reach the engine and surface as `origin_unavailable` with a
    raw TypeError, i.e. the model was told Origin was missing. Extra keys are fine.
    """
    props = (schema or {}).get("properties") or {}
    problems = []
    unknown = [k for k in args if k not in props]
    if unknown:
        # An unrecognised key used to pass silently: the engine read its own names,
        # found nothing, and did nothing. Measured: {"colour": "#fff", "xscale":
        # "log"} on origin_style was accepted and changed no styling at all, which
        # reads as a successful call.
        raise originlab.OriginError(
            "unknown_arguments",
            "%s 不认识这些参数：%s（可用的：%s）"
            % (name, ", ".join(sorted(str(k) for k in unknown)),
               ", ".join(sorted(props)) or "无"),
            ["按工具的 inputSchema 用准确的参数名；拼错的键不会生效",
             "%s 列出全部工具与参数" % "python server.py --tools"])
    for key, spec in props.items():
        if key not in args:
            continue
        value = args[key]
        types = spec.get("type")
        types = types if isinstance(types, list) else ([types] if types else [])
        if value is None:
            # An explicit null is NOT the same as omitting the key: the tool then
            # reads a["path"] -> None, os.path.abspath(None) raises TypeError, and
            # that used to come back as `origin_unavailable` with a "the COM channel
            # is damaged" hint — for origin_read_file, which never touches COM — and
            # it also set the suspect flag, forcing a reset of a healthy session.
            if types and "null" not in types:
                problems.append("%s 不能为 null（应为 %s）" % (key, "/".join(types)))
            continue
        if types:
            def fits(t):
                py = _JSON_TYPES.get(t)
                if py is None:
                    return True
                if t in ("integer", "number") and isinstance(value, bool):
                    return False
                if t == "integer":
                    # JSON Schema counts 1600.0 as an integer (a zero fraction is
                    # still an integer). A model that computed a width sends a
                    # float, and isinstance(1600.0, int) is False — measured:
                    # origin_export(width=1600.0) used to be refused as
                    # bad_arguments while width=1600 passed.
                    if isinstance(value, int):
                        return True
                    return isinstance(value, float) and value.is_integer()
                return isinstance(value, py)
            if not any(fits(t) for t in types):
                problems.append("%s 应为 %s，收到 %s"
                                % (key, "/".join(types), type(value).__name__))
                continue
    if problems:
        raise originlab.OriginError("bad_arguments", "%s 参数不合法：%s" % (name, "；".join(problems)),
                                    ["按工具的 inputSchema 修正参数类型/范围后再调用"])


def _handle(msg):
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "invalid request: expected an object"}}
    method = msg.get("method")
    call_id = msg.get("id")
    params = msg.get("params")
    if not isinstance(params, dict):
        params = {}

    if not isinstance(method, str) or not method:
        # A message with no method is an Invalid Request (-32600), not "method not
        # found: None" and definitely not silence. Measured: `{}` and
        # {"jsonrpc":"2.0"} produced no reply at all (a client waits forever) while
        # {"id":55} answered -32601 "method not found: None" — three different
        # verdicts for the same malformed shape.
        return {"jsonrpc": "2.0", "id": request_id(call_id),
                "error": {"code": -32600, "message": "invalid request: 缺少 method"}}

    if "id" not in msg:
        # No id means this is a *notification*: JSON-RPC forbids answering it, and it
        # must not run a tool either. Measured: an id-less tools/call really executed
        # origin_status and answered with id:null, so a client that simply forgot the
        # id got a real side effect (plus a reply it could never correlate).
        if method == "tools/call":
            _log("tools/call 没有 id，按通知处理：不执行、不应答")
        return None

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": call_id, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}

    if method in ("notifications/initialized", "notifications/cancelled"):
        # This stdio loop is synchronous: a tools/call blocks this thread, so a
        # cancellation arriving while a tool runs is only read after the answer.
        # What CAN be cancelled lives in originlab.call: a queued task is skipped
        # when its call times out, and an in-flight timeout is reported as
        # executed="unknown" instead of being replayed.
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": call_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": call_id, "result": {
            "tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
                      for t in TOOLS]}}

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = _BY_NAME.get(name)
        if tool is None:
            return _tool_result(call_id, {"ok": False, "code": "unknown_tool",
                                          "message": "没有这个工具：%s" % name}, True)
        if not isinstance(args, dict):
            return _tool_result(call_id, {"ok": False, "code": "bad_arguments",
                                          "message": "arguments 必须是对象"}, True)
        try:
            _validate_args(name, args, tool.get("inputSchema"))
            out = tool["run"](args)
            if isinstance(out, dict):
                out = {"ok": True, **out}
            return _tool_result(call_id, out, False)
        except originlab.OriginError as exc:
            return _tool_result(call_id, {"ok": False, "code": exc.code, "message": exc.message,
                                          "next_actions": exc.next_actions, **exc.extra}, True)
        except KeyError as exc:
            # A model can omit a required argument even though the schema names it;
            # that is a bad_arguments answer, not a crash with a stack trace.
            want = (tool.get("inputSchema") or {}).get("required") or []
            return _tool_result(call_id, {"ok": False, "code": "bad_arguments",
                                          "message": "%s 缺少参数 %s（该工具要求 %s）"
                                                     % (name, exc, want or "见 schema"),
                                          "next_actions": ["补上缺的参数再调用"]}, True)
        except Exception as exc:  # noqa: BLE001 - report, never kill the server
            _log("tool %s crashed: %r" % (name, exc))
            bare = isinstance(exc, (SystemError, TypeError, AttributeError))
            if bare:
                originlab.mark_suspect()
            return _tool_result(
                call_id,
                {"ok": False,
                 "code": "origin_unavailable" if bare else "internal_error",
                 "message": "%s: %s" % (type(exc).__name__, exc),
                 "next_actions": (["Origin 的 COM 通道进入了坏状态（多为页面被销毁后残留错误）",
                                   "重新 origin_import / origin_write 拿新句柄，旧句柄已作废",
                                   "还是不行就 origin_exit 让插件重启 Origin，或 origin_reclaim 关后台实例"]
                                  if bare else ["origin_status 看连接是否还活着"])}, True)

    if method == "shutdown":
        return {"jsonrpc": "2.0", "id": call_id, "result": {}}

    if call_id is not None:
        return {"jsonrpc": "2.0", "id": call_id,
                "error": {"code": -32601, "message": "method not found: %s" % method}}
    return None


def _encode_reply(reply):
    """Strict JSON, with a clean JSON-RPC error if a value slipped through.

    allow_nan=False is the last line of defence. A bare Infinity/NaN in a reply
    is not valid JSON, so a strict client (dsh) fails to parse the *entire*
    result rather than one field. Answer with a clean error instead of either
    killing the session or writing bytes no client can read.
    """
    try:
        line = json.dumps(reply, ensure_ascii=False, default=str, allow_nan=False)
    except ValueError as exc:
        _log("reply was not strict JSON (%r) for id=%r" % (exc, reply.get("id")))
        reply = {"jsonrpc": "2.0", "id": reply.get("id"),
                 "error": {"code": -32603,
                           "message": "internal error: 返回值含非 JSON 数值（%s）" % exc}}
        line = json.dumps(reply, ensure_ascii=False, default=str)
    return (line + "\n").encode("utf-8")


def main():
    if "--tools" in sys.argv:
        lines = ["%-20s %s" % (t["name"], t["description"].split("。")[0]) for t in TOOLS]
        # Bytes, not print(): print() uses sys.stdout's locale encoding, so on a
        # cp1252/cp936 console this either emits GBK (mojibake for a UTF-8 reader)
        # or dies outright with UnicodeEncodeError — measured: PYTHONIOENCODING=cp1252
        # made `--tools` exit 1 with zero bytes on stdout, which is exactly the
        # invocation CI and the README tell people to run.
        out = getattr(sys.stdout, "buffer", None)
        payload = ("\n".join(lines) + "\n").encode("utf-8", "replace")
        if out is not None:
            out.write(payload)
            out.flush()
        else:
            sys.stdout.write(payload.decode("utf-8"))
            sys.stdout.flush()
        return 0

    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    # One JSON line is one request; an unbounded readline let a single huge
    # `columns` payload allocate whatever the client sent. The cap only needs to be
    # far above any real dataset (a 1M-point two-column write is ~30 MB of JSON).
    max_line = int(os.environ.get("ORIGIN_BRIDGE_MAX_LINE", 64 * 1024 * 1024))
    _log("ready; python=%s | max line %d" % (sys.executable, max_line))

    # Warm up numpy BEFORE the reader thread exists. On this Windows/CPython
    # build, the first import of numpy's C extension deadlocks in the DLL load
    # when another thread is parked in a blocking stdin read; the COM worker
    # imports originpro (which imports numpy) later. Measured: without this,
    # the worker never leaves _multiarray_umath.create_module.
    try:
        import numpy  # noqa: F401
    except Exception:
        pass

    inbox = queue.Queue()
    pending = {}            # tools/call id -> cancellation Event
    cancelled = set()       # ids whose cancellation arrived
    pending_lock = threading.Lock()
    write_lock = threading.Lock()

    def send(reply):
        if reply is None:
            return
        data = _encode_reply(reply)
        try:
            with write_lock:
                stdout.write(data)
                stdout.flush()
        except OSError:
            # The client closed the pipe; there is nowhere to deliver the answer.
            pass

    def request_key(value):
        # JSON-RPC ids are strings or numbers; anything else is malformed and
        # must not be used as a dict key (a list/dict id would raise).
        return request_id(value)

    def reader():
        while True:
            try:
                raw = stdin.readline(max_line + 1)
            except Exception as exc:  # noqa: BLE001
                # A read error here used to kill this thread silently, and the main
                # loop then waited on inbox.get() for a message nobody would ever
                # produce: a hung bridge, no reply, no log. Treat it as EOF.
                _log("stdin read failed (%r)；按 EOF 处理" % (exc,))
                inbox.put({"__eof__": True})
                return
            if not raw:
                inbox.put({"__eof__": True})
                return
            if len(raw) > max_line and not raw.endswith(b"\n"):
                # Drain the rest of the oversized line so the next request is not
                # read from the middle of this one, then answer with a clean error.
                while True:
                    chunk = stdin.readline(max_line + 1)
                    if not chunk or chunk.endswith(b"\n"):
                        break
                _log("request over %d bytes refused" % max_line)
                inbox.put({"__reply__": {"jsonrpc": "2.0", "id": None,
                                          "error": {"code": -32600,
                                                    "message": "请求太大：单行上限 %d 字节"
                                                               % max_line}}})
                continue
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                _log("bad json: %s" % exc)
                inbox.put({"__reply__": {"jsonrpc": "2.0", "id": None,
                                          "error": {"code": -32700,
                                                    "message": "parse error"}}})
                continue
            if isinstance(msg, dict) and msg.get("method") == "notifications/cancelled":
                # Handled here, not on the executor: this is the only message that
                # must take effect while a tool is blocking the main thread.
                # params MUST be treated as untrusted: JSON-RPC allows an array here
                # (`"params": [1]`), and `(params or {}).get` on a non-empty list
                # raised AttributeError inside this thread — which then died, leaving
                # the main loop parked in inbox.get() forever. Measured: one such line
                # killed the whole session with zero diagnostics.
                try:
                    p = msg.get("params")
                    key = request_key(p.get("requestId") if isinstance(p, dict) else None)
                    ev = None
                    with pending_lock:
                        ev = pending.get(key) if key is not None else None
                        if ev is not None:
                            cancelled.add(key)
                    if ev is not None:
                        _log("cancel request id=%r" % (key,))
                        ev.set()
                except Exception as exc:  # noqa: BLE001
                    _log("cancel notification ignored (%r)" % (exc,))
                continue
            if isinstance(msg, dict) and msg.get("method") == "tools/call":
                key = request_key(msg.get("id"))
                if key is not None:
                    ev = threading.Event()
                    with pending_lock:
                        pending[key] = ev
                    msg["__cancel__"] = ev
            inbox.put(msg)

    threading.Thread(target=reader, daemon=True, name="stdin-reader").start()

    while True:
        item = inbox.get()
        if not isinstance(item, dict):
            send(_handle(item))
            continue
        if item.get("__eof__"):
            _log("stdin closed; exiting")
            return 0
        if "__reply__" in item:
            send(item["__reply__"])
            continue
        ev = item.pop("__cancel__", None)
        rid = item.get("id")
        try:
            if ev is not None:
                originlab.set_cancel_event(ev)
            # A single message must never take the server down: dsh only spawns
            # this process once per session, so dying here means every origin
            # tool is dead until the user restarts the harness.
            reply = _handle(item)
        except Exception as exc:  # noqa: BLE001
            _log("handler crashed on %r: %r" % (
                item.get("method") if isinstance(item, dict) else type(item).__name__, exc))
            reply = {"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32603, "message": "internal error: %s" % exc}}
        finally:
            if ev is not None:
                originlab.set_cancel_event(None)
                key = request_key(rid)
                with pending_lock:
                    pending.pop(key, None)
                    was_cancelled = key in cancelled
                    cancelled.discard(key)
            else:
                was_cancelled = False
        if was_cancelled:
            # The client asked to stop; whatever the engine produced is discarded
            # (a write may still have landed — the engine reports that state in
            # its own error, but the MCP answer is the cancellation).
            reply = {"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32800, "message": "request cancelled"}}
        send(reply)


if __name__ == "__main__":
    sys.exit(main() or 0)
