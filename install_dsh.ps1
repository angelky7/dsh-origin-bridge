# Install Origin Bridge into DeepSeek Harness.
#
#   powershell -ExecutionPolicy Bypass -File .\install_dsh.ps1
#   powershell -ExecutionPolicy Bypass -File .\install_dsh.ps1 -Profile web
#
# ASCII only on purpose: Windows PowerShell 5.1 reads .ps1 as ANSI unless a BOM
# is present, so non-ASCII text can decode into stray quote characters and break
# parsing.
#
# Why an `insert:` block and not a bare `- id:` entry:
#   a top-level `- id: X` in the profile layer OVERRIDES an entry X that a lower
#   (bundle) layer already created. For a brand-new id it silently does nothing,
#   which looks exactly like "I edited the config, restarted, and nothing
#   happened". `- insert:` creates the entry.
#
# Why we do not copy into node_modules:
#   a bundle is only applied when it is BOTH a dependency and listed in
#   dsh.profile.bundles in the profile package.json. Registering there means
#   running pnpm against a live profile (it prunes/rewrites node_modules). The
#   profile patch layer is applied unconditionally, so an insert here needs no
#   package-manager surgery.

param(
    [string]$Profile = 'desktop',
    [string]$PythonExe = '',
    [string]$DshHome = '',
    [switch]$RemoveOld
)

$ErrorActionPreference = 'Stop'
$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$entryId = 'mcp-origin-bridge'
$oldEntryId = 'mcp-originlab-bridge'
$serverPy = Join-Path $src 'server.py'

if (-not (Test-Path $serverPy)) { throw "server.py not next to this script: $serverPy" }

if (-not $DshHome) { $DshHome = Join-Path $env:USERPROFILE '.dsh' }
$profileDir = Join-Path $DshHome "profiles\$Profile"
if (-not (Test-Path $profileDir)) {
    throw "dsh profile dir not found: $profileDir (pass -Profile web or desktop)"
}

if (-not $PythonExe) {
    $candidate = Join-Path $src '.venv\Scripts\python.exe'
    if (Test-Path $candidate) { $PythonExe = $candidate }
    else { $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source }
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "python interpreter not found. Create it first: python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt"
}

# A python that cannot RUN must not be written into the config. On a Windows where
# the Store alias comes first on PATH, `Get-Command python` resolves to a 0-byte
# app-execution alias that exits 9009 with no stderr; the old code wrote its path into
# the YAML, warned "no originpro, the server will start but cannot connect" (which is
# the wrong diagnosis: it never starts at all), and exited 0. Measured 2026-11-26.
$prevEap2 = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
# Load the scripted exception into a variable instead of letting PowerShell print
# "Program 'x' failed to run" twice before our own message.
$runsAtAll = $false
$runWhy = ''
try {
    $probeOut = & $PythonExe -c "import sys" 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) { $runsAtAll = $true }
    else { $runWhy = "exit code $LASTEXITCODE" }
} catch {
    $runWhy = $_.Exception.Message
}
$ErrorActionPreference = $prevEap2
if (-not $runsAtAll) {
    throw ("this python cannot run at all (" + $runWhy + "): " + $PythonExe + "`n" +
           "On PATH, the first 'python' is often the Microsoft Store app-execution alias " +
           "(0 bytes, exits 9009). Nothing was written. Pass a real one: " +
           "-PythonExe 'C:\...\python.exe', or set ORIGIN_BRIDGE_PYTHON.")
}

$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $PythonExe -c "import originpro" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "$PythonExe has no originpro; the server starts and registers all tools, but every call answers missing_originpro until you pip install originpro"
}

# YAML single-quoted scalars escape a quote by doubling it. Writing a path with an
# apostrophe (C:\Users\O'Brien\...) unescaped produced an unparseable patch: measured
# 2026-11-26, the file was left corrupt and the user was told to restore it by hand.
function Quote-Yaml([string]$Value) {
    "'" + ($Value -replace "'", "''") + "'"
}

$patch = Join-Path $profileDir 'cordis.patch.yml'
$block = ""
$block += "`r`n# --- origin-bridge (installed $(Get-Date -Format 'yyyy-MM-dd HH:mm')) ---`r`n"
$block += "# insert: creates the loader entry in the profile layer. A second top-level`r`n"
$block += "# '- id: ${entryId}' would OVERRIDE this one instead of adding another (`r`n"
$block += "# a duplicate insert is tolerated by dsh but the later layer wins silently).`r`n"
$block += "# failOnStartupError keeps dsh usable when Origin is missing.`r`n"
$block += "- insert:`r`n"
$block += "    - id: $entryId`r`n"
$block += "      name: '@deepseek-ai/dsh-mcp-client'`r`n"
$block += "      config:`r`n"
$block += "        serverName: origin_bridge`r`n"
$block += "        transport: stdio`r`n"
$block += "        command: $(Quote-Yaml $PythonExe)`r`n"
$block += "        args:`r`n"
$block += "          - '-u'`r`n"
$block += "          - '-X'`r`n"
$block += "          - utf8`r`n"
$block += "          - $(Quote-Yaml $serverPy)`r`n"
$block += "        env:`r`n"
$block += "          PYTHONIOENCODING: utf-8`r`n"
$block += "          PYTHONUNBUFFERED: '1'`r`n"
$block += "        failOnStartupError: false`r`n"
$block += "        toolCallTimeoutMs: 180000`r`n"

# Self-mount: this is what makes the plugin show up as its own card in dsh's plugin
# settings (measured 2026-11-26 on dsh 0.2.2-rc.5: removing the row still registers
# every tool and still reports installed/enabled, so it is cosmetic — the tools come
# from the insert above). We stage a registration-only package (no python code) so
# there is never a second copy of the server to drift out of sync with $src.
$pkgName = 'dsh-origin-bridge'
$pkgDir = Join-Path $profileDir "node_modules\$pkgName"
# Read the real version instead of hard-coding it: the card in dsh shows this
# value, and a stale 0.4.0 makes the installed plugin look ancient.
$pkgVersion = '0.0.0'
$manifest = Join-Path $src 'package.json'
if (Test-Path $manifest) {
    try { $pkgVersion = (Get-Content $manifest -Raw -Encoding UTF8 | ConvertFrom-Json).version } catch { }
}
if (-not (Test-Path (Join-Path $pkgDir 'index.js'))) {
    New-Item -ItemType Directory -Force -Path $pkgDir | Out-Null
    @'
// Registration-only bundle. The Origin tools come from server.py in this
// package's own folder, registered by the mcp-origin-bridge insert in the
// profile's cordis.patch.yml. This module exists so dsh's plugin UI can show
// origin-bridge as its own card; keep apply() side-effect free because it
// runs on every dsh startup.
export const name = 'dsh-origin-bridge';
export function activate() {}
export function apply() {}
export default { name, activate, apply };
'@ | Set-Content -Path (Join-Path $pkgDir 'index.js') -Encoding ASCII
    Write-Host "staged registration-only package -> $pkgDir"
}
# (Re)write package.json on every run: the 0.4.9-and-earlier text embedded raw
# quotes in the description (unparseable JSON, so dsh could not read the card),
# and a one-time write also left a stale version behind on re-install.
@"
{
  "name": "dsh-origin-bridge",
  "version": "$pkgVersion",
  "description": "Origin Bridge: natural language -> local Origin/OriginPro -> editable .opju + publication images",
  "main": "./index.js",
  "type": "module",
  "license": "MIT"
}
"@ | Set-Content -Path (Join-Path $pkgDir 'package.json') -Encoding ASCII

$block += "`r`n# self-mount: makes $pkgName appear as its own card in dsh's plugin UI.`r`n"
$block += "# apply() is a no-op; the tools come from the $entryId insert above.`r`n"
$block += "# NOTE: a future 'pnpm install' in this profile may prune this folder, which`r`n"
$block += "# only removes the card -- the origin_* tools keep working.`r`n"
$block += "- insert:`r`n"
$block += "    - id: $pkgName`r`n"
$block += "      name: $pkgName`r`n"

$existing = if (Test-Path $patch) { Get-Content $patch -Raw -Encoding UTF8 } else { '' }

# A fresh dsh profile ships `cordis.patch.yml` containing just `[]` (plus comments).
# Appending a `- insert:` entry after that produces TWO YAML documents and dsh
# refuses to parse the overlay — the installer used to break a brand-new profile.
# Treat a comments-only / empty-array file as empty so the block becomes the array.
$meaningful = ($existing -split "`r?`n" | Where-Object { $_ -notmatch '^\s*#' -and $_.Trim() -ne '' }) -join "`n"
if ($meaningful.Trim() -eq '[]' -or $meaningful.Trim() -eq '') { $existing = '' }

# -RemoveOld: drop the previous plugin's insert block. Both entries pointed at
# C:\...\originlab-bridge, and a dead stdio server still shows up as a tool
# provider, so leaving it in place would mean two overlapping tool sets.
$removed = $false
if ($RemoveOld -and $existing -match [regex]::Escape($oldEntryId)) {
    $lines = @($existing -split "`r?`n")
    $from = -1; $to = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($from -lt 0 -and $lines[$i] -match '^# --- originlab-bridge') { $from = $i; continue }
        if ($from -ge 0 -and $lines[$i] -match '^# --- ') { $to = $i; break }
    }
    if ($from -ge 0) {
        # $lines[0..-1] is a REVERSE range in PowerShell: taking the block out when it
        # started at line 0 used to keep the first and last lines, so the old plugin's
        # tombstone comment survived the "removal". Measured 2026-11-26.
        $head = if ($from -gt 0) { @($lines[0..($from - 1)]) } else { @() }
        $tailLines = if ($to -le $lines.Count - 1) { @($lines[$to..($lines.Count - 1)]) } else { @() }
        $existing = (($head + $tailLines) -join "`r`n")
        $removed = $true
        Write-Host "removed the old $oldEntryId block (lines $from..$to)"
    } else {
        Write-Warning "$oldEntryId is referenced but its '# --- originlab-bridge' marker was not found; leaving it alone"
    }
}

# Entry detection must look at real YAML entries, not at the whole file: the block
# this script writes carries the ids inside its own comments ("insert for
# mcp-origin-bridge: ..."), so a substring match declared "already installed" on a
# patch that only had those comments and appended nothing, exit 0. Measured 2026-11-26.
function Has-EntryId([string]$Text, [string]$Id) {
    if (-not $Text) { return $false }
    return [bool]([regex]::IsMatch($Text, "(?m)^\s*-\s*id:\s*" + [regex]::Escape($Id) + "\s*$"))
}

$hasTools = Has-EntryId $existing $entryId
$hasCard = Has-EntryId $existing $pkgName

if ($hasTools -and $hasCard -and -not $removed) {
    Write-Host "profile patch already has both entries; nothing appended"
    $ErrorActionPreference = $prevEap
    exit 0
}

if ($hasTools) {
    # Only the cosmetic card entry is missing.
    $piece = ($block -replace '(?s)^.*?(# self-mount)', '$1')
} elseif (-not $hasCard) {
    $piece = $block
} else {
    # Card row present but the tools insert is gone (e.g. a hand edit): add the tools
    # part only.
    $piece = ($block -replace '(?s)(# self-mount).*$', '')
}

# A newline before the appended text is not optional: appending to a file whose last
# line has no EOL produced `failOnStartupError: false# self-mount: ...` — one comment
# glued onto a value, and the whole overlay unparseable. Measured 2026-11-26.
if ($existing -and -not $existing.EndsWith("`n")) { $existing += "`r`n" }

# Back up only when something is really going to be written, and make the name
# unique: two runs inside the same second used to collide on -Force and silently
# overwrite the ORIGINAL pre-first-install backup.
$backupDir = ''
if ($piece) {
    $stamp = "$(Get-Date -Format 'yyyyMMdd-HHmmss')-$PID"
    $backupDir = Join-Path $DshHome "backups\origin-bridge-$stamp"
    $n = 1
    while (Test-Path $backupDir) {
        $n++
        $backupDir = Join-Path $DshHome ("backups\origin-bridge-$stamp-$n")
    }
    New-Item -ItemType Directory -Path $backupDir | Out-Null
    if (Test-Path $patch) {
        Copy-Item $patch (Join-Path $backupDir 'cordis.patch.yml.bak') -ErrorAction Stop
    }
    Set-Content -Path $patch -Value ($existing + $piece) -Encoding UTF8 -NoNewline
    if ($hasTools) {
        Write-Host "added the self-mount card entry only (tools insert was already present)"
    } else {
        Write-Host "appended insert block"
    }
    Write-Host "backup -> $backupDir"
}

$checkers = @($PythonExe, (Get-Command python -ErrorAction SilentlyContinue).Source) |
    Where-Object { $_ -and (Test-Path $_ -ErrorAction SilentlyContinue) }
$checked = $false
$code = "import yaml,io,sys;d=yaml.safe_load(io.open(sys.argv[1],encoding='utf-8'));assert isinstance(d,list);" + `
        "ins=[e for e in d if isinstance(e,dict) and 'insert' in e];ids=[x.get('id') for e in ins for x in e['insert']];" + `
        "print('patch parses OK:',len(d),'entries; inserted ids:',ids);" + `
        "assert 'mcp-origin-bridge' in ids, 'the tools insert for mcp-origin-bridge is MISSING'"
foreach ($c in $checkers) {
    & $c -c "import yaml" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & $c -c $code "$patch"
        if ($LASTEXITCODE -ne 0) {
            # Roll back instead of leaving a broken profile behind plus advice.
            $bak = if ($backupDir) { Join-Path $backupDir 'cordis.patch.yml.bak' } else { '' }
            if ($bak -and (Test-Path $bak)) {
                Copy-Item $bak $patch -Force
                $ErrorActionPreference = $prevEap
                throw "cordis.patch.yml did not validate (see the python error above); restored it from $bak"
            }
            $ErrorActionPreference = $prevEap
            throw "cordis.patch.yml did not validate and there was no backup to restore from"
        }
        $checked = $true
        break
    }
}
$ErrorActionPreference = $prevEap
if (-not $checked) {
    Write-Warning ("no interpreter with PyYAML found, so the patch was NOT validated. " +
                   "Run: pip install pyyaml -r requirements.txt, then re-run this script.")
}

Write-Host ""
Write-Host "DONE. Fully quit dsh (tray -> exit, not just close the window), reopen, then ask" -ForegroundColor Green
Write-Host "a new chat to list tools whose names contain origin_." -ForegroundColor Green
