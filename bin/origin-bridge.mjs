#!/usr/bin/env node
// Standalone launcher for MCP clients that are not DeepSeek Harness
// (Claude Desktop / Cursor / Codex / any stdio MCP client):
//
//   npx dsh-origin-bridge
//   node bin/origin-bridge.mjs --check   诊断模式：解释器 / server.py / numpy / originpro
//
// Interpreter order: ORIGIN_BRIDGE_PYTHON → the venv next to this package →
// every python found on PATH, in PATH order. 每个候选都要“真的跑一句 Python”才算数
// （probe 见下），绝不靠 existsSync 猜。
//
// 为什么改成枚举 + 实测：PATH 上第一个 python 经常不是 Python。Windows 商店会在
//   C:\Users\<user>\AppData\Local\Microsoft\WindowsApps\python.exe
// 放一个“应用执行别名”（PowerShell 里显示 0 字节，Node 里是指向 AppInstallerPythonRedirector.exe
// 的链接、statSync 还会 EACCES），跑它既无 stdout 也无 stderr，只有 exit 9009 ——
// 旧实现取“第一个存在的候选”，于是恰好选中它：MCP 客户端拿到空输出 → JSONDecodeError，
// 而启动器自己一声不吭。现在既按结构认这个别名，又保留实测兜底：.venv 里可能躺着一个
// 大小正常、一启动就崩的解释器（缺 DLL），只有真的跑起来才发现。
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, lstatSync, readdirSync, statSync, readlinkSync } from 'node:fs';
import { basename, delimiter, dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const server = join(root, 'server.py');

const CHECK = process.argv.slice(2).includes('--check');
const DEBUG = Boolean(process.env.ORIGIN_BRIDGE_DEBUG);
const WIN = process.platform === 'win32';

// 3s 只用来钉死“启动即卡住”的候选（死锁的 venv、网络盘上的 exe），正常解释器冷启动
// 远低于 100ms。上限不能大：MCP 客户端等 initialize 的耐心是秒级的，--check 也要秒回。
const PROBE_TIMEOUT_MS = 3000;
const CHECK_TIMEOUT_MS = 30000; // numpy/originpro 首次导入要编译扩展，给足时间

// Same environment the dsh patch sets, so logs are UTF-8 instead of the console
// code page and the child flushes line by line.
const childEnv = { ...process.env };
if (!childEnv.PYTHONIOENCODING) childEnv.PYTHONIOENCODING = 'utf-8';
if (!childEnv.PYTHONUNBUFFERED) childEnv.PYTHONUNBUFFERED = '1';

// 回打版本号：一个字节的 stdout 同时证明“这个 exe 真能起 Python”和“它的输出读得到”，
// 版本号又正好是 --check 要显示的东西。-X utf8 让控制台代码页不是 UTF-8 时也不出解码噪音。
const PROBE_ARGS = ['-X', 'utf8', '-c', 'import sys; sys.stdout.write(sys.version.split()[0])'];

// 把一次失败的探测翻译成中文原因。MCP 场景下这行文字就是用户能看到的全部诊断。
function exitReason(r) {
  if (r.error) {
    const code = r.error.code || String(r.error.message || '');
    const known = {
      ENOENT: '文件不存在，或不是可执行文件',
      EACCES: '没有执行权限',
      EINVAL: '不是有效的可执行文件',
      ETIMEDOUT: `${PROBE_TIMEOUT_MS}ms 内没有响应`,
      EPERM: '被系统拒绝执行（杀软/权限策略）',
      // libuv 在 Windows 上把 ERROR_BAD_EXE_FORMAT(193) 折成 UNKNOWN：
      // 一个改了后缀的文本文件就是这个下场。
      UNKNOWN: '无法创建进程（Windows 报“不是有效的可执行文件”，bad exe format）',
    };
    return `启动失败 ${code}${known[code] ? '：' + known[code] : ''}`;
  }
  if (r.signal) return `被信号 ${r.signal} 杀掉（启动即崩：坏的 venv 或缺少 DLL 都长这样）`;
  if (typeof r.status === 'number' && r.status !== 0) {
    const hex = r.status > 255 ? ` / 0x${(r.status >>> 0).toString(16)}` : '';
    if (r.status === 9009 && WIN) {
      // Windows 商店别名被真正执行时的固定出口：不是“解释器崩了”，而是“这压根不是 Python”。
      return '退出码 9009：Windows 商店“应用执行别名”的固定出口（不是 Python，不写任何输出）';
    }
    const last = ((r.stderr || '').trim().split(/\r?\n/).filter(Boolean).pop() || '').slice(0, 160);
    return `退出码 ${r.status}${hex}${last ? '：' + last : ''}`;
  }
  return '';
}

// Windows 的“应用执行别名”是个指向 AppInstallerPythonRedirector.exe 的符号链接，
// 目标在受 ACL 保护的 WindowsApps 包里：statSync 会 EACCES，真正运行则 exit 9009，
// 而且可能把 Microsoft Store 弹出来。所以在 Windows 上先按结构认它，认出来就直接跳过，
// 不拿它去试 —— 但“商店里真装了 Python”的别名指向的是 python.exe 本身，不是 redirector，
// 那种仍然走下面的实测。
function aliasRedirector(cand) {
  if (!WIN) return null;
  let st;
  try {
    st = lstatSync(cand);
  } catch {
    return null;
  }
  if (!st.isSymbolicLink()) return null;
  let target = '';
  try {
    target = readlinkSync(cand);
  } catch {
    return null;
  }
  if (!/AppInstallerPythonRedirector\.exe$/i.test(target)) return null;
  return `Windows 商店的“应用执行别名”（指向 ${basename(target)}），不是 Python：` +
    '运行它只会 exit 9009，stdout 一个字节都不写';
}

// 返回 { ok, reason, version }。判据只有一个：子进程真的以 0 退出并回打了版本号。
function probe(cand) {
  const alias = aliasRedirector(cand);
  if (alias) return { ok: false, reason: alias };

  let statNote = '';
  try {
    const st = statSync(cand);
    if (!st.isFile()) return { ok: false, reason: '不是普通文件（目录或悬空链接）' };
    if (st.size === 0) {
      // 0 字节的可执行文件不可能跑起来（商店别名、被截断的占位文件都这样）；跳过即可。
      // 但这类判断只用于“快速排除”，判据仍然是下面的实测 —— .venv 里可能躺着一个
      // 大小正常、一启动就崩的解释器（缺 DLL），只有实跑才发现。
      return { ok: false, reason: '0 字节：空文件/商店别名占位，不可能是可用的解释器' };
    }
  } catch (e) {
    // ENOENT 是确定性的“这个路径上没有文件”，spawn 一样起不来，不必再实测；
    // 其余 stat 失败（EACCES 等）才需要实测兜底 —— ACL 保护的商店别名正是这种。
    if (e.code === 'ENOENT') return { ok: false, reason: '不存在（ENOENT：PATH/包目录里没有这个文件）' };
    statNote = `stat 失败（${e.code || e.message}）→ 改用实测`;
  }
  const r = spawnSync(cand, PROBE_ARGS, {
    timeout: PROBE_TIMEOUT_MS,
    killSignal: 'SIGKILL',
    stdio: ['ignore', 'pipe', 'pipe'],
    encoding: 'utf8',
    windowsHide: true,
    env: childEnv,
  });
  const bad = exitReason(r);
  if (bad) return { ok: false, reason: statNote ? `${statNote}；${bad}` : bad };
  return { ok: true, version: (r.stdout || '').trim() };
}

function dedupeKey(p) {
  return WIN ? p.toLowerCase() : p;
}

// 候选顺序就是信任顺序：显式覆盖 → 包内 venv → PATH 顺序上的每一个 python。
// 每条都带 label，失败时诊断里能指名“是谁被跳过、为什么”。
function candidates() {
  const out = [];
  const seen = new Set();
  const add = (p, label) => {
    if (!p) return;
    const clean = String(p).trim().replace(/^"|"$/g, '');
    if (!clean) return;
    if (seen.has(dedupeKey(clean))) return;
    seen.add(dedupeKey(clean));
    out.push({ path: clean, label });
  };

  // 1) 用户显式指定 —— 永远排第一，它坏掉也只跳过它，不挡住后面的真实解释器。
  add(process.env.ORIGIN_BRIDGE_PYTHON, 'ORIGIN_BRIDGE_PYTHON 环境变量');

  // 2) 包内 .venv。两种布局都试（跨平台解包的 .venv 可能与本机不匹配），但仍然要实测。
  add(join(root, '.venv', 'Scripts', 'python.exe'), '包内 .venv（Windows 布局）');
  add(join(root, '.venv', 'bin', 'python'), '包内 .venv（Unix 布局）');

  // 3) PATH 上每一个 python，按 PATH 顺序 —— 不是只取第一个。Windows 上按
  //    PATHEXT 习惯同时认 .exe 后缀（python3 在这台机器上如果存在就是 python3.exe）。
  const names = WIN ? ['python.exe', 'python3.exe', 'python', 'python3'] : ['python3', 'python'];
  let cwd = process.cwd();
  for (const raw of (process.env.PATH || '').split(delimiter)) {
    const dir = raw.trim().replace(/^"|"$/g, '');
    if (!dir) continue; // 空 PATH 项等于“当前目录”，在这个场景里只会带来意外
    let base = dir;
    if (!isAbsolute(dir)) {
      try {
        base = resolve(cwd, dir);
      } catch {
        continue; // cwd 已被删除之类，相对项无从解析
      }
    }
    for (const name of names) add(join(base, name), `PATH 上的 ${name}（目录 ${base}）`);
  }
  return out;
}

function select() {
  const tried = [];
  for (const c of candidates()) {
    const p = probe(c.path);
    if (p.ok) return { chosen: { ...c, version: p.version }, tried };
    tried.push({ ...c, reason: p.reason });
    if (DEBUG) console.error(`[origin-bridge] 跳过 ${c.path}：${p.reason}`);
  }
  return { chosen: null, tried };
}

// 只在“全军覆没”时用：扫常见安装位置，给一条可以直接粘贴的 setx 命令。
// 扫到也不自动改用，候选顺序保持上面定义的样子 —— 这里只负责把话说清楚。
function suggestWindowsInterpreters() {
  const bases = [];
  if (process.env.LOCALAPPDATA) bases.push(join(process.env.LOCALAPPDATA, 'Programs', 'Python'));
  if (process.env.ProgramFiles) bases.push(join(process.env.ProgramFiles, 'Python'));
  if (process.env['ProgramFiles(x86)']) bases.push(join(process.env['ProgramFiles(x86)'], 'Python'));
  const found = [];
  for (const base of bases) {
    let entries = [];
    try {
      entries = readdirSync(base);
    } catch {
      continue;
    }
    // 倒序近似“新版本优先”：Python313 > Python312。
    for (const name of entries.slice().sort().reverse()) {
      const exe = join(base, name, 'python.exe');
      if (existsSync(exe)) found.push(exe);
    }
  }
  return found;
}

function fatal(tried) {
  const lines = [];
  lines.push('[origin-bridge] 启动失败：没有任何候选通过“真的跑一句 Python”的实测，server.py 无法启动。');
  lines.push('  实测过的候选（全部失败；顺序 = 优先级）：');
  tried.forEach((t, i) => {
    lines.push(`    ${i + 1}. ${t.path}`);
    lines.push(`       来源：${t.label}；跳过原因：${t.reason}`);
  });

  const store = tried.find((t) => /商店|9009/.test(t.reason));
  if (WIN && store) {
    lines.push('');
    lines.push(`  其中 ${store.path}`);
    lines.push('  是 Windows 商店的“应用执行别名”（AppInstallerPythonRedirector），不是 Python：');
    lines.push('  它跑起来 exit 9009，连一个字节的 stdout 都不会写 —— 这就是 MCP 客户端只看到');
    lines.push('  JSONDecodeError、并且“启动器什么都没说”的原因。');
  }

  const verified = [];
  for (const exe of suggestWindowsInterpreters()) {
    if (verified.length >= 3) break;
    const p = probe(exe);
    if (p.ok) verified.push({ exe, version: p.version });
  }
  if (verified.length) {
    lines.push('');
    lines.push('  本机实测可用的解释器：');
    for (const v of verified) lines.push(`    ${v.exe}  （Python ${v.version}）`);
    lines.push('  把它写进环境变量，启动器就会优先用它（新开终端后生效）：');
    lines.push(`    setx ORIGIN_BRIDGE_PYTHON "${verified[0].exe}"`);
  } else {
    lines.push('');
    lines.push('  修法（任选其一，改完新开终端）：');
    lines.push('    - Windows：找到真正的 python.exe（不是 ...\\WindowsApps\\python.exe 那个别名），');
    lines.push('      执行 setx ORIGIN_BRIDGE_PYTHON "C:\\path\\to\\python.exe"；');
    lines.push('    - 或把真解释器所在目录移到 PATH 里 WindowsApps 之前；');
    lines.push('    - 或在本包目录建 .venv（python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt）。');
  }
  lines.push('');
  lines.push('  诊断（不启动 MCP、不启动 Origin）：node bin/origin-bridge.mjs --check');
  console.error(lines.join('\n'));
  process.exit(1);
}

// 单行化子进程 stderr，写进 --check 的输出里。
function oneLine(res, limit = 200) {
  const text = (res.err || res.out || '').trim().split(/\r?\n/).filter(Boolean).pop() || '';
  return text.slice(0, limit) || '（子进程没有输出）';
}

// 用选中的解释器跑一段 -c 代码；只读，不启动 MCP、不启动 Origin。
function py(cand, code, args = [], timeout = CHECK_TIMEOUT_MS) {
  const r = spawnSync(cand, ['-X', 'utf8', '-c', code, ...args], {
    timeout,
    killSignal: 'SIGKILL',
    stdio: ['ignore', 'pipe', 'pipe'],
    encoding: 'utf8',
    windowsHide: true,
    env: childEnv,
  });
  const bad = exitReason(r);
  return { ok: !bad, out: (r.stdout || '').trim(), err: (r.stderr || '').trim(), reason: bad };
}

const READ_SERVER_CODE = "import sys; sys.stdout.write(str(len(open(sys.argv[1], 'rb').read())))";
const NUMPY_CODE = 'import sys; import numpy as n; sys.stdout.write(n.__version__)';
// originpro 没有 __version__，优先用发行元数据；取不到就只报“已导入”。
const ORIGINPRO_CODE = [
  'import sys, importlib.metadata as m',
  'import originpro',
  'try:',
  "    sys.stdout.write(m.version('originpro'))",
  'except Exception:',
  "    sys.stdout.write('unknown')",
].join('\n');

async function runCheck(chosen) {
  const out = [];
  let code = 0;
  const fail = (title, detail, hint) => {
    code = 1;
    out.push(`[FAIL] ${title}：${detail}`);
    if (hint) out.push(`       修法：${hint}`);
  };

  out.push('origin-bridge --check（诊断；不启动 MCP，也不启动 Origin）');
  out.push('判定：解释器可用 + server.py 可读 + import numpy 成功 → 退出码 0；');
  out.push('      import originpro 缺失只告警（离线照常注册 28 个工具，调用时报 missing_originpro）。');
  out.push('');
  out.push(`[OK]   解释器      ${chosen.path}`);
  out.push(`       来源        ${chosen.label}`);
  out.push(`       实测版本    Python ${chosen.version || '（能启动，但没回打版本号）'}`);

  const rd = py(chosen.path, READ_SERVER_CODE, [server]);
  if (rd.ok) out.push(`[OK]   server.py   ${server}（${rd.out} 字节，该解释器可读）`);
  else fail('server.py 不可读', `${server} —— ${rd.reason}`, '重新安装本包（server.py 必须随包发布）');

  const np = py(chosen.path, NUMPY_CODE);
  if (np.ok) out.push(`[OK]   import numpy    ${np.out}`);
  else fail('import numpy 失败', oneLine(np), `${chosen.path} -m pip install numpy`);

  const op = py(chosen.path, ORIGINPRO_CODE);
  if (op.ok) out.push(`[OK]   import originpro  本机可用（版本 ${op.out === 'unknown' ? '未知' : op.out}）`);
  else {
    out.push(`[WARN] import originpro  不可用：${oneLine(op)}`);
    out.push('       已知行为（不是本启动器的故障）：28 个工具照常注册，每次实际调用返回');
    out.push('       missing_originpro 错误并附安装指引；装上 Origin + originpro 即可恢复。');
  }

  out.push('');
  out.push(code === 0
    ? '退出码 0：解释器与 server.py 就绪，可以接 MCP 客户端了。'
    : '退出码 1：上面标 [FAIL] 的项目必须先修好，否则 server.py 跑不起来。');
  // 先等 stdout 真的写完再退出：process.exit() 在 stdout 是管道时会截断还没刷出去的
  // 写入，而 --check 的输出正是要被 CI 日志/远端终端完整拿走的。
  await new Promise((resolve) => process.stdout.write(out.join('\n') + '\n', resolve));
  process.exit(code);
}

const { chosen, tried } = select();
if (!chosen) fatal(tried);

if (!existsSync(server)) {
  console.error(`[origin-bridge] 解释器可用（${chosen.path}），但找不到 server.py：${server}`);
  console.error('  这个启动器必须与 server.py 一起发布；npm 包的 files 里漏掉了它。');
  process.exit(1);
}

if (CHECK) await runCheck(chosen);

if (DEBUG) console.error(`[origin-bridge] 使用解释器 ${chosen.path}（${chosen.label}）`);

const child = spawn(chosen.path, ['-u', '-X', 'utf8', server], {
  stdio: 'inherit',
  cwd: root,
  env: childEnv,
});

// A child killed by a signal is a crash, not a clean exit: reporting 0 here would
// make an MCP client treat a dead server as "finished normally". The 'error'
// event (interpreter not found, no execute permission) is otherwise an unhandled
// Node stack trace instead of a message the MCP client can show.
child.on('error', (err) => {
  console.error(`[origin-bridge] 无法启动 python（${chosen.path}）：${err.message}`);
  console.error('  候选选择已经实测过，仍失败通常是启动其间被删除/被杀软隔离；重新运行 --check。');
  process.exit(1);
});
child.on('exit', (code, signal) => {
  if (signal) {
    console.error(`[origin-bridge] python 被信号 ${signal} 终止（${chosen.path}）`);
    process.exit(1);
  }
  process.exit(code ?? 1);
});