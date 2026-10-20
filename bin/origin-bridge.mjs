#!/usr/bin/env node
// Standalone launcher for MCP clients that are not DeepSeek Harness
// (Claude Desktop / Cursor / Codex / any stdio MCP client):
//
//   npx dsh-origin-bridge
//
// Picks the interpreter from ORIGIN_BRIDGE_PYTHON, then the venv next to
// this package, then whatever `python` is on PATH.
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const server = join(root, 'server.py');

const candidates = [
  process.env.ORIGIN_BRIDGE_PYTHON,
  join(root, '.venv', 'Scripts', 'python.exe'),
  join(root, '.venv', 'bin', 'python'),
  'python',
].filter(Boolean);

// Same environment the dsh patch sets, so logs are UTF-8 instead of the console
// code page and the child flushes line by line.
const env = { ...process.env };
if (!env.PYTHONIOENCODING) env.PYTHONIOENCODING = 'utf-8';
if (!env.PYTHONUNBUFFERED) env.PYTHONUNBUFFERED = '1';

const child = spawn(
  candidates.find((c) => c === 'python' || existsSync(c)) || 'python',
  ['-u', '-X', 'utf8', server],
  { stdio: 'inherit', cwd: root, env },
);

// A child killed by a signal is a crash, not a clean exit: reporting 0 here would
// make an MCP client treat a dead server as "finished normally".
child.on('exit', (code, signal) => {
  if (signal) {
    console.error(`[origin-bridge] python exited on ${signal}`);
    process.exit(1);
  }
  process.exit(code ?? 1);
});
