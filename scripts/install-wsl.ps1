# =====================================================================
#  install-wsl.ps1  --  RUN THIS ONCE, AS ADMINISTRATOR
#
#  This is the ONLY step that needs administrator privileges.
#  Everything after this is automated by provision-wsl.sh.
#
#  How to run:
#    1. Press Win, type "PowerShell"
#    2. Right-click "Windows PowerShell" -> "Run as administrator"
#    3. Paste:
#         Set-ExecutionPolicy -Scope Process Bypass -Force
#         Set-Location <path to this repo>   # e.g. C:\src\iceberg-dv-anatomy
#         .\scripts\install-wsl.ps1
#
#  NOTE: written in English on purpose. Windows PowerShell 5.1 reads
#        BOM-less UTF-8 scripts as ANSI, which mangles non-ASCII text.
# =====================================================================
$ErrorActionPreference = 'Stop'

function Say([string]$m, [string]$c = 'White') { Write-Host $m -ForegroundColor $c }

# wsl.exe emits UTF-16LE. Without this, captured output becomes interleaved
# null bytes and every -match / -eq against it silently fails.
function WslOut {
  param([string[]]$WslArgs)
  $prev = [Console]::OutputEncoding
  try {
    [Console]::OutputEncoding = [System.Text.Encoding]::Unicode
    $raw = & wsl.exe @WslArgs 2>&1 | Out-String
  } finally {
    [Console]::OutputEncoding = $prev
  }
  return ($raw -replace "`0", "")
}
function Ok  ([string]$m) { Say "  [ok]   $m"   'Green'  }
function Warn([string]$m) { Say "  [warn] $m"   'Yellow' }
function Bad ([string]$m) { Say "  [FAIL] $m"   'Red'    }

Say ""
Say "=====================================================" 'Cyan'
Say " iceberg-dv-anatomy : WSL2 setup (admin step)"        'Cyan'
Say "=====================================================" 'Cyan'

# ---------- 0. admin check ----------
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
  ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
  Bad "Not running as administrator."
  Say ""
  Say "  Close this window. Open PowerShell via right-click ->" 'Yellow'
  Say "  'Run as administrator', then run this script again."   'Yellow'
  Say ""
  exit 1
}
Ok "Running as administrator"

$DISTRO = 'Ubuntu-24.04'
$rebootNeeded = $false

# ---------- 1. optional features ----------
Say ""
Say "[1/4] Enabling Windows optional features..."
foreach ($feat in 'VirtualMachinePlatform', 'Microsoft-Windows-Subsystem-Linux') {
  $state = (Get-WindowsOptionalFeature -Online -FeatureName $feat).State
  if ($state -eq 'Enabled') {
    Ok "$feat already enabled"
  } else {
    Say "  enabling $feat ..."
    $r = Enable-WindowsOptionalFeature -Online -FeatureName $feat -All -NoRestart
    Ok "$feat enabled"
    if ($r.RestartNeeded) { $rebootNeeded = $true }
  }
}

if ($rebootNeeded) {
  Say ""
  Say "=====================================================" 'Yellow'
  Say " REBOOT REQUIRED"                                      'Yellow'
  Say "=====================================================" 'Yellow'
  Say " A Windows feature was just enabled. Reboot, then run" 'Yellow'
  Say " this same script again (as administrator) to finish." 'Yellow'
  Say ""
  Say "   shutdown /r /t 0"                                   'White'
  Say ""
  exit 0
}

# ---------- 2. WSL runtime ----------
Say ""
Say "[2/4] Installing the WSL runtime (Store package)..."
$wslStore = Test-Path "$env:ProgramFiles\WSL\wsl.exe"
if ($wslStore) {
  Ok "WSL runtime already present"
} else {
  # --disable-interactivity is REQUIRED here. Without it winget can block on a
  # prompt that is invisible when the window is unattended, and the script hangs
  # forever after printing this step's header. (Observed 2026-08-17.)
  # Do not pipe to Out-Null either - that hides the very output you need to
  # diagnose a hang.
  try {
    & winget install --id Microsoft.WSL --exact --silent --disable-interactivity `
        --accept-source-agreements --accept-package-agreements
    Ok "winget exit code: $LASTEXITCODE"
  } catch {
    Warn "winget path failed, falling back to 'wsl --install --no-distribution'"
  }
  if (-not (Test-Path "$env:ProgramFiles\WSL\wsl.exe")) {
    & wsl.exe --install --no-distribution
  }
  if (Test-Path "$env:ProgramFiles\WSL\wsl.exe") { Ok "WSL runtime installed" }
  else { Warn "Could not confirm WSL runtime path; continuing anyway" }
}

# The optional feature alone does not ship the WSL2 kernel. Without this,
# 'wsl --status' reports "kernel file not found" and every distro fails to boot.
Say "  updating WSL kernel..."
$upd = WslOut @('--update')
# Korean "error" (U+C624 U+B958) built from char codes, NOT typed literally:
# this file must stay pure ASCII (see NOTE at the top) or PS 5.1 mangles it
# reading BOM-less UTF-8 as ANSI -- which would silently break this match.
$errWords = 'error|failed|' + [char]0xC624 + [char]0xB958
if ($upd -match $errWords) { Warn "wsl --update reported: $($upd.Trim())" }
else { Ok "WSL kernel up to date" }

& wsl.exe --set-default-version 2 2>&1 | Out-Null
Ok "Default WSL version = 2"

# ---------- 3. Ubuntu ----------
Say ""
Say "[3/4] Installing $DISTRO ..."
$installed = WslOut @('--list', '--quiet')
if ($installed -match [regex]::Escape($DISTRO)) {
  Ok "$DISTRO already installed"
} else {
  # --no-launch avoids the interactive username/password prompt.
  & wsl.exe --install -d $DISTRO --no-launch
  Ok "$DISTRO registered"

  # Unattended first-run: default user = root.
  # Deliberate choice - it removes sudo password prompts so the rest of the
  # setup can be fully automated. This is a disposable dev environment.
  $launcher = Get-Command 'ubuntu2404.exe' -ErrorAction SilentlyContinue
  if ($launcher) {
    Say "  running unattended first-time setup (root user)..."
    & ubuntu2404.exe install --root
    Ok "First-time setup done (default user: root)"
  } else {
    Warn "ubuntu2404.exe launcher not found."
    Warn "Run '$DISTRO' once from the Start menu and create a user manually."
  }
}

# ---------- 4. verify ----------
Say ""
Say "[4/4] Verifying..."
Say (WslOut @('--list', '--verbose'))
$probe = WslOut @('-d', $DISTRO, '--', 'echo', 'WSL_OK')
if ($probe -match 'WSL_OK') {
  Ok "$DISTRO responds to commands"
} else {
  Bad "$DISTRO did not respond. Output: $probe"
  Say "  If a reboot is still pending, reboot and re-run this script." 'Yellow'
  exit 1
}

Say ""
Say "=====================================================" 'Green'
Say " DONE. WSL2 + $DISTRO are ready."                      'Green'
Say "=====================================================" 'Green'
Say ""
Say " Next: go back to the Claude Code session and say"      'White'
Say " it is done. The rest (JDK, Spark, async-profiler,"     'White'
Say " Iceberg jars) is installed automatically inside WSL."  'White'
Say ""
Say " Or run it yourself:"                                   'White'
$WslRepo = (wsl -d $DISTRO -- wslpath -a "$PSScriptRoot" 2>$null)
if (-not $WslRepo) { $WslRepo = "<REPO>/scripts" }
Say "   wsl -d $DISTRO -- bash $WslRepo/provision-wsl.sh" 'DarkGray'
Say ""
