<p align="center">
  <img src="icon.svg" width="128" height="128" alt="Origin Bridge">
</p>

<h1 align="center">Origin Bridge</h1>

<p align="center">
  <a href="README.md">中文说明</a> · <b>English</b>
</p>

<p align="center">
  Drive <b>local</b> Origin / OriginPro from chat in plain language:<br>
  hand it a <code>.dat</code> / <code>.csv</code> / Excel / electrochemical-workstation <code>.bin</code> file,<br>
  get back a publication-ready image and an <code>.opju</code> project you can <b>keep editing in Origin</b>.
</p>

<p align="center">
  <sub>27 tools · <b>1139/1139 full-chain tests passing</b> · data never leaves the machine · workstation <code>.txt</code> / <code>.bin</code> supported</sub>
</p>

<p align="center">
  <a href="#requirements"><img alt="platform" src="https://img.shields.io/badge/platform-Windows%20%2B%20Origin%202021%2B-blue"></a>
  <img alt="tests" src="https://img.shields.io/badge/tests-1139%2F1139%20passing-brightgreen">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-lightgrey">
  <a href="https://github.com/angelky7/dsh-origin-bridge/actions/workflows/offline-checks.yml"><img alt="offline checks" src="https://github.com/angelky7/dsh-origin-bridge/actions/workflows/offline-checks.yml/badge.svg"></a>
</p>

## Why

Existing attempts at wiring Origin into chat either upload your data to a cloud or are 200-line toys. Origin Bridge is the opposite:

- **Data never leaves the machine.** No API keys, no cloud component. dsh starts a local Python process that drives the Origin on your machine through OriginLab's official [`originpro`](https://pypi.org/project/originpro/) package.
- **Every step carries evidence.** Origin's X-Functions routinely "succeed" without doing anything. Here, plotting reads the axis range back and compares it against the data span; exporting verifies existence, size and the file-header magic; styling is read back item by item — anything that cannot be read back lands in a `failed` list instead of pretending to succeed.
- **27 tools, not 74.** Each one has a measured use, and each description states its measured limits, so the model does not step into traps.
- **1000+ real tests, not a claim.** 1139 full-chain cases run against a real stdio server + local OriginPro — 1139 passed / 0 failed: every tool has at least 15 cases (up to 135), grouped into five side-effect layers (L0 pure parsing 301, L1 read-only COM 63, L2 session-mutating 531, L3 on-disk 166, L4 process-level 78). Prompts are written the way a person talks, and every case is validated against the live `tools/list` schema first — so a failure can only be a product bug, never a miswritten test. Another **422** offline/unit/edge checks ship **inside the package** (asciiio 84, binio 15, lifecycle 51, smoke 129, edge 34, paths 35, json 29, readfile 12, labtalk 33; binio adds per-point vendor cross-checks when a real workstation export is present) and you can re-run them yourself; the 1139-case bank holds prompts and synthetic data, so it stays a development-side asset and is not distributed.

## What it can do

| You say | It does |
|---|---|
| "Plot `D:\data\spectrum.dat` as a line-symbol plot, X axis "Time (s)", export to `D:\out`" | `origin_figure`: import → plot → axis titles → PNG + .opju |
| "Put these two files on one graph with a double Y axis" | `origin_layer` (right Y) + `origin_plot` (plot into a named layer) |
| "Add a legend to this spectrum, top right" | `origin_legend` (text / position / visibility all read back) |
| "Fit this EIS data" | `origin_fit` (gauss / lorentz / voigt / expdec1… presets, parameters can be fixed) |
| "Here is a cv.bin from a CHI workstation" | `origin_import` reads the binary; the potential axis is reconstructed from the header's method parameters |
| "Workstation txt export with a wall of parameters first" | `origin_import` skips the header, treats the row next to the data (`Potential/V, Current/A`) as column names, splits units into column Units, keeps parameters in `metadata` |
| "Make an XPS figure" | `origin_chart_render` (40 scientific presets: axis titles / plot kind / suggested fit) |
| "Show this matrix as a heatmap" | `origin_matrix` (heat_map / cmap / mesh / contline, Origin's own templates) |

Sources: `.dat` `.csv` `.txt` `.tsv` (`origin_read_file` parses these only; `.xls/.xlsx/.wks` are refused with `unsupported_file_type` pointing at `origin_import`; delimiter sniffing; encodings UTF-8 / GBK / **UTF-16 with or without BOM** / cp1252; multi-row headers; Fortran exponents; `NA`/`-`; decorated numbers like `<0.001`, `2.5±0.1`, `25 mV`, `1,234.5`, `(500)`, `2.5*` — each unwrapping states in `warnings` what it substituted), `.xls` `.xlsx`, electrochemical-workstation `.txt` exports (CH Instruments and friends; the parameter preamble lands in `metadata`), and workstation binary `.bin` files whose header starts `80 F2 1B 00`.

## Requirements

Windows, Origin/OriginPro **2021 or newer** (hard floor from `originpro`; 2018 measured as unusable), Python 3.10+.

## Install

### With dsh (recommended)

```bash
dsh plugin --profile desktop add dsh-origin-bridge
```

Installs exactly **one package** (the host's `@deepseek-ai/dsh-mcp-client` is an optional peer, so no second loader copy lands in the profile). If the install fails with `ERR_PNPM_NO_MATCHING_VERSION`, that is a **lagging npm mirror**, not a missing release: check `npm config get registry` and either point at the official registry once,

```bash
dsh plugin --profile desktop add dsh-origin-bridge --registry=https://registry.npmjs.org/
```

or install an offline bundle without touching any registry:

```bash
dsh plugin --profile desktop add file:C:\path\to\dsh-origin-bridge
```

npm only distributes the files — **your Python environment is your job**. The dependencies must go into the interpreter dsh actually launches:

```powershell
pip install originpro numpy
```

Two failure modes behave differently (measured, do not guess):

- **Interpreter present, `originpro` missing**: all 27 tools still register, and every call returns `code=missing_originpro` with the reason and `isError: true` — visible and actionable.
- **No `python` on PATH** (the patch's default command): the bridge process never starts. `failOnStartupError: false` keeps dsh alive, but that round has **no tools registered and no error**. Set `ORIGIN_BRIDGE_PYTHON` first.

Origin not running does **not** affect registration: the COM connection is built on the first tool call.

If the dependencies live in a venv, or you use a different profile, set these user environment variables (then reopen the terminal and fully restart dsh):

```powershell
setx ORIGIN_BRIDGE_PYTHON "C:\path\to\.venv\Scripts\python.exe"
setx ORIGIN_BRIDGE_HOME   "$env:USERPROFILE\.dsh\profiles\web\node_modules\dsh-origin-bridge"
```

Fully quit dsh and reopen; in a new chat say "list tools whose names contain origin" — you should see the 27 `mcp__origin_bridge__origin_*` tools. Uninstall: `dsh plugin --profile desktop remove dsh-origin-bridge`.

### Any other stdio MCP client (Claude Desktop, Cursor, Codex…)

`npm install dsh-origin-bridge` ships a launcher at `bin/origin-bridge.mjs`. Point your client's `command` at it (or at `node_modules/dsh-origin-bridge/bin/origin-bridge.mjs`), keep `serverName: origin_bridge`, and the tool names are `mcp__origin_bridge__origin_*`. The launcher picks the interpreter from `ORIGIN_BRIDGE_PYTHON`, then a `.venv` next to the package, then `python` on PATH — and exits with a one-line error instead of a stack trace if none can be spawned.

### From source (to run tests or hack)

```powershell
cd C:\path\to\dsh-origin-bridge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The Origin-free suites run anywhere; `smoke_test.py` and `edge_checks.py` drive a real Origin:

```powershell
.\.venv\Scripts\python.exe smoke_test.py              # 129 checks, needs Origin
.\.venv\Scripts\python.exe edge_checks.py             # 34 checks, needs Origin
.\.venv\Scripts\python.exe test_asciiio.py            # 84 checks, Origin-free
.\.venv\Scripts\python.exe test_binio.py              # 15 checks, Origin-free; cross-checks point-for-point against a vendor .txt when one exists
.\.venv\Scripts\python.exe test_lifecycle.py          # 51 checks, Origin-free
.\.venv\Scripts\python.exe test_paths.py              # 35 checks, Origin-free
.\.venv\Scripts\python.exe test_json_safety.py        # 29 checks, Origin-free
.\.venv\Scripts\python.exe test_readfile.py           # 12 checks, Origin-free
.\.venv\Scripts\python.exe test_labtalk_guard.py      # 33 checks, Origin-free
.\.venv\Scripts\python.exe check_claims.py            # re-measures the suites and holds the README counts to them (CI runs it too)
```

For dsh registration from a source checkout, run `install_dsh.ps1` (backs up your profile patch, writes the insert block, validates the YAML afterwards) — see the [Chinese README](README.md) for the manual YAML and the three things that must be exactly right.

## The 27 tools

| Tool | Purpose |
|---|---|
| `origin_status` | Connection check, Origin version, open pages. **Call this first** |
| `origin_read_file` | Parse a file without touching Origin: encoding, delimiter, header rows, per-column long name/units/valid rows, a 3-value `sample` per column; refuses `.xls/.xlsx/.wks`; `.bin` gets the technique/points/method/axis-source route |
| `origin_import` | Import `.dat/.csv/.txt/.tsv/.xls/.xlsx/.wks` and workstation `.bin`; returns a `ws-N` handle and the column list |
| `origin_write` | Build a sheet from inline data: `columns={name:[values…]}` or `headers`+`rows` |
| `origin_formula` | Set a column formula (Fx); returns computed rows and first/last values |
| `origin_plot` | Single or **multiple** curves; `template` uses Origin's own graph templates; `graph`+`layer` plots into a named layer |
| `origin_layer` | Add a layer (right Y, stacked panels); returns the name Origin gave it as proof |
| `origin_legend` | Legend from column long names or verbatim entries; 13 positions; text/visibility/coordinates read back |
| `origin_style` | Axis titles, **log axes**, ranges and steps, per-curve color/width/symbol/size/fill/transparency — **each item read back** |
| `origin_annotate` | Text labels and reference lines in data coordinates |
| `origin_matrix` | Grid data → matrix → **heatmap / contour / 3D colored surface** |
| `origin_fit` | `linear`; presets `gauss/lorentz/voigt/expdec1/expdec2/sine/power/logistic/boltzmann/doseresp/cubic`; or `nlfitsing`+`func` |
| `origin_chart_presets` | 40 scientific chart presets (XPS/XRD/UV-Vis/fluorescence/CV/Arrhenius…) |
| `origin_chart_preset` | One preset's full contract: required columns, default titles, suggested fit |
| `origin_chart_check` | Dry-run a preset's column expectations against your data, no Origin involved |
| `origin_chart_render` | One call: preset → styled figure (titles/kind/legend/image/opju) |
| `origin_export` | Export with existence/size/magic verification; PNG dimensions are read back; `all_graphs: true` exports every graph page into a directory in one call, each verified |
| `origin_view` | Render the graph and return it **as MCP image content the model can see** |
| `origin_save_project` | Save an editable `.opju` project |
| `origin_open` | Open an existing project |
| `origin_inspect` | Pages / layers / plot counts / live handles — **look before you guess indices** |
| `origin_close_pages` | Clean up accumulated pages (the root cause of stale pointers in long sessions); has `dry_run` |
| `origin_labtalk` | Escape hatch: raw LabTalk with mandatory read-back; a deny list blocks destructive statements (advisory, not a sandbox) |
| `origin_exit` | Quit Origin, optionally saving first |
| `origin_instances` | List Origin instances: windowless background leftovers vs foreground windows, marking which this process started |
| `origin_reclaim` | Reclaim leftovers — **windowless only**; anything with a window is listed for you to close yourself |
| `origin_figure` | Main path: import → plot → titles → legend → image + opju in one call |

## Workstation `.bin` files

Files whose header starts `80 F2 1B 00` and name `Cyclic Voltammetry` / `A.C. Impedance` / `Amperometric i-t Curve` (CHI660E-class exports) end in **one** float32 array — the measured channel (current in A). **The potential axis is not in the file.** Origin Bridge reads the method block (Init/High/Low E, step, segments) from the header, rebuilds the triangle-wave axis, and **only accepts the reconstruction when the point count matches the file exactly** — otherwise it degrades to `axis=none` and says why, never plotting against a wrong X. i-t / CA / CP get a time axis from the sample interval; EIS frequency tables are a different parameter set and are reported as unavailable rather than guessed. The reconstruction was verified point-for-point against the vendor's own `.txt` exports (5 files × 600 points, 0 mismatches).

## Known limits

- `originpro` needs Origin **2021+**; OriginPro 2018C connects but imports 0 rows and `add_plot` kills the connector.
- Nonlinear fitting needs an **OriginPro** license; the standard edition gets an explicit `fit_function_unavailable`.
- `.bin` covers the `80 F2 1B 00` workstation format only; EIS .bin has the measured column only. Other vendors' binaries (Gamry / Autolab / Pine) are not attempted.
- Network paths are refused (`bad_path`): no UNC, no unmapped drives. Local paths with Chinese names, spaces, 200+ characters, `tiff→tif` renaming and auto-created directories all verified on real hardware.
- **Concurrency is guarded by a lock**: two sessions driving one Origin stomp each other's pages and handles. The first bridge to attach holds `%LOCALAPPDATA%\origin-bridge\session.lock`; a second live bridge gets a named `origin_busy` error naming the holder's pid. The lock is released on `origin_exit` or process exit, a dead owner's lock is stolen automatically, and deleting the file is always the manual override.
- Long sessions accumulate pages; past ~200 the per-plot cost grows (0.7 s → 2.8 s at 300 pages). The engine warns every 50 new pages and `origin_close_pages` / `origin_exit` reset the counter.
- A windowless Origin is the *normal* state for a dsh-driven instance, so nothing ever kills instances implicitly: `origin_reclaim` acts only on windowless ones, and only when you call it.
- A close attempt while Origin is under automation pops the modal "being controlled by another program" box, and every COM call queues behind it until someone clicks OK. The bridge matches that box **by body text** — dismissed automatically on the timeout path (with a grace window), swept after each call for instances it spawned, reported per-instance by `origin_instances`, and cleared before `origin_reclaim`/`origin_exit` act. Every other dialog (save prompts, license) is reported but never clicked.
- Statistics (t-test / ANOVA / PCA / survival) are not provided — `originpro` doesn't offer them either.

## Troubleshooting

| Symptom | First check |
|---|---|
| Tools missing in dsh | Is the block a `- insert:` entry; is `name:` exactly `@deepseek-ai/dsh-mcp-client`; did you fully restart dsh; does `python server.py --tools` list 27 |
| `connection_error` | Origin ≥ 2021? `pip show originpro`? Exactly one `Origin64.exe` in Task Manager? |
| "Instance limit exceeded" | `origin_instances` to see them, `origin_reclaim` to close windowless leftovers; windows with titles may be your own unsaved work |
| `import_empty` | `origin_read_file` first; space-aligned and multi-header files are handled, so this means the file truly has no numeric rows |
| `com_timeout` | Origin stuck on some other dialog / license popup; the "being controlled by another program" box is dismissed by the bridge itself, plus a 15 s grace window |
| Image missing | `origin_inspect` → `plots` count; Chinese/space/long paths are verified, no need to ASCII-fy |
| `bad_path` / `target_locked` | The path is wrong (directory as filename, unmapped drive) or the target exists read-only; the bridge will not overwrite |
| `origin_busy` | Another bridge session holds Origin (lock at `%LOCALAPPDATA%\origin-bridge\session.lock`). Wait for it, or delete the lock file if it is wedged |
| Install fails `ERR_PNPM_NO_MATCHING_VERSION` | Mirror lag: add `--registry=https://registry.npmjs.org/` once, or use a `file:` offline bundle |

The [Chinese README](README.md) also carries a maintainer table of **30+ measured originpro/Origin traps** (silent no-ops, the real `plotxy` codes, `set_formula`'s actual 0-based index, COM thread affinity, the missing-value sentinel, the export filter rewriting `#`…) — if you maintain anything that drives Origin from Python, read it.

## Trademark & license

This is an independent third-party tool, **not affiliated with, endorsed by or sponsored by** OriginLab Corporation. Origin and OriginPro are trademarks of OriginLab Corporation; this tool drives Origin the normal way through OriginLab's publicly published `originpro` Python package and distributes none of OriginLab's binaries, icons or documentation.

MIT © Origin Bridge Contributors
