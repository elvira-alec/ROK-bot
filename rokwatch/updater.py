"""Updates: the panel checks GitHub for a newer numbered release (1.0, 1.1, 1.2, ...).
To install one, a small separate updater (a PowerShell window with a progress bar)
is started; RoKWatcher closes, the updater downloads the new version, puts the new
program files where the old ones were (your settings, logs and snapshots stay) and
starts RoKWatcher again."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

log = logging.getLogger("rokwatch")
REPO = "elvira-alec/ROK-bot"
ASSET = "RoKWatcher-windows.zip"
VERSION_FILE = Path(__file__).with_name("version.txt")


def current_version() -> str:
    try:
        return VERSION_FILE.read_text().strip() or "dev"
    except OSError:
        return "dev"


def parse(v) -> tuple:
    """'v1.12' -> (1, 12); anything else -> ()"""
    try:
        return tuple(int(p) for p in str(v).strip().lstrip("vV").split("."))
    except ValueError:
        return ()


def newer(latest, current) -> bool:
    return bool(parse(latest)) and (not parse(current) or parse(latest) > parse(current))


API = f"https://api.github.com/repos/{REPO}/releases/latest"


def _fetch_urllib(timeout):
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "RoKWatcher-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _fetch_powershell(timeout):
    """Same request through Windows' own networking (system proxy and certificates,
    like the browser) - for when Python's own connection is blocked."""
    cmd = ("[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
           f"(Invoke-WebRequest -UseBasicParsing -TimeoutSec {int(timeout)} -Uri '{API}' "
           "-Headers @{'User-Agent'='RoKWatcher-updater'}).Content")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                       capture_output=True, text=True, timeout=timeout + 15,
                       creationflags=0x08000000 if sys.platform == "win32" else 0)
    if r.returncode != 0 or not r.stdout.strip():
        raise OSError((r.stderr or "PowerShell request failed").strip().splitlines()[0][:200])
    return json.loads(r.stdout)


WEB_LATEST = f"https://github.com/{REPO}/releases/latest"


def _latest_from_web(timeout):
    """The newest release from GitHub's web page (it redirects to /releases/tag/vX.Y).
    Unlike the API this has no 60-checks-an-hour limit."""
    req = urllib.request.Request(WEB_LATEST, headers={"User-Agent": "RoKWatcher-updater"}, method="HEAD")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        final = r.geturl()
    tag = final.rstrip("/").rsplit("/", 1)[-1]
    if "/releases/tag/" not in final or not parse(tag):
        return None
    v = tag.lstrip("vV")
    return {"version": v, "url": f"https://github.com/{REPO}/releases/download/{tag}/{ASSET}",
            "notes": "", "page": final}


def latest_release(timeout=10, verbose=False):
    """{'version', 'url', 'notes', 'page'} of the newest numbered release, or None."""
    try:
        return _latest_from_web(timeout)
    except Exception as e:
        if verbose:
            print("Web check failed:", e, "- trying the GitHub API...")
        log.info("Update check: web page failed (%s); trying the API", e)
    try:
        data = _fetch_urllib(timeout)
    except Exception as e:
        if sys.platform != "win32":
            raise
        if verbose:
            print("Direct check failed:", e, "- trying through Windows (PowerShell)...")
        log.info("Update check: direct request failed (%s); trying through PowerShell", e)
        try:
            data = _fetch_powershell(timeout)
        except Exception as e2:
            raise OSError(f"{e} / through Windows: {e2}") from e2
    tag = data.get("tag_name", "")
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET), None)
    if not parse(tag) or not asset:
        return None
    return {"version": tag.lstrip("vV"), "url": asset["browser_download_url"],
            "notes": (data.get("body") or "")[:500], "page": data.get("html_url")}


def can_self_update() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    return Path(sys.executable).resolve().parent


# The updater window. Runs on its own (PowerShell + Windows Forms) so it can replace
# RoKWatcher's files once RoKWatcher has closed.
UPDATER_PS1 = r'''
param([int]$WaitPid, [string]$Url, [string]$AppDir, [string]$Version)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, System.IO.Compression.FileSystem
[System.Windows.Forms.Application]::EnableVisualStyles()
$f = New-Object Windows.Forms.Form
$f.Text = "RoK Watcher update"; $f.Width = 460; $f.Height = 190; $f.StartPosition = "CenterScreen"
$f.FormBorderStyle = "FixedDialog"; $f.MaximizeBox = $false; $f.TopMost = $true
$t = New-Object Windows.Forms.Label
$t.Text = "Updating RoK Watcher to version $Version"; $t.Font = New-Object Drawing.Font("Segoe UI", 12, [Drawing.FontStyle]::Bold)
$t.AutoSize = $true; $t.Left = 20; $t.Top = 18; $f.Controls.Add($t)
$s = New-Object Windows.Forms.Label
$s.Text = "Starting..."; $s.Font = New-Object Drawing.Font("Segoe UI", 10); $s.Width = 410; $s.Left = 20; $s.Top = 55
$f.Controls.Add($s)
$p = New-Object Windows.Forms.ProgressBar
$p.Left = 20; $p.Top = 88; $p.Width = 405; $p.Height = 22; $p.Style = "Marquee"; $f.Controls.Add($p)
$n = New-Object Windows.Forms.Label
$n.Text = "Your settings, logs and snapshots are kept. RoK Watcher starts again by itself."
$n.Font = New-Object Drawing.Font("Segoe UI", 8); $n.ForeColor = "Gray"; $n.Width = 420; $n.Left = 20; $n.Top = 120
$f.Controls.Add($n)
$f.Show(); [Windows.Forms.Application]::DoEvents()
function Say($text) { $s.Text = $text; [Windows.Forms.Application]::DoEvents() }
$exe = Join-Path $AppDir "RoKWatcher.exe"
$work = Join-Path $env:TEMP ("rokwatch-update-" + [guid]::NewGuid().ToString("N"))
try {
    Say "Closing RoK Watcher..."
    $end = (Get-Date).AddSeconds(30)
    while ((Get-Process -Id $WaitPid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $end) {
        Start-Sleep -Milliseconds 200; [Windows.Forms.Application]::DoEvents()
    }
    Stop-Process -Id $WaitPid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800
    New-Item -ItemType Directory -Path $work | Out-Null
    $zip = Join-Path $work "update.zip"
    $p.Style = "Continuous"; $p.Value = 0
    $wc = New-Object Net.WebClient
    $wc.Headers.Add("User-Agent", "RoKWatcher-updater")
    $script:pct = 0
    Register-ObjectEvent $wc DownloadProgressChanged -Action { $script:pct = $EventArgs.ProgressPercentage } | Out-Null
    $task = $wc.DownloadFileTaskAsync($Url, $zip)
    while (-not $task.IsCompleted) {
        $p.Value = [Math]::Min(100, $script:pct); Say ("Downloading version $Version... " + $script:pct + "%")
        Start-Sleep -Milliseconds 150
    }
    if ($task.IsFaulted) { throw $task.Exception.InnerException }
    $p.Style = "Marquee"
    Say "Installing..."
    $out = Join-Path $work "unzipped"
    [IO.Compression.ZipFile]::ExtractToDirectory($zip, $out)
    $src = Join-Path $out "RoKWatcher"
    if (-not (Test-Path (Join-Path $src "RoKWatcher.exe"))) { $src = $out }
    # copy the new program over the old one; files only you have (settings, logs,
    # snapshots) aren't in the download, so they stay as they are
    $rc = Start-Process robocopy -ArgumentList @("`"$src`"", "`"$AppDir`"", "/E", "/R:5", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP") -NoNewWindow -Wait -PassThru
    if ($rc.ExitCode -ge 8) { throw "Copying the new files failed (robocopy code $($rc.ExitCode)). Is a file still open?" }
    Say "Starting RoK Watcher $Version..."
    Start-Process -FilePath $exe -ArgumentList "--updated" -WorkingDirectory $AppDir
    Start-Sleep -Seconds 2
} catch {
    [Windows.Forms.MessageBox]::Show("The update didn't work: $($_.Exception.Message)`n`nRoK Watcher will start again as it was. You can also download the new version by hand from GitHub.", "RoK Watcher update") | Out-Null
    if (Test-Path $exe) { Start-Process -FilePath $exe -ArgumentList "--updated" -WorkingDirectory $AppDir }
} finally {
    $f.Close()
    Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
}
'''


UPDATER_EXE = "RoKWatcherUpdater.exe"


def _spawn(cmd):
    """Start a program that keeps running after this one closes."""
    detached = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:                                        # also leave any job we're in (else it can die with us)
        return subprocess.Popen(cmd, creationflags=detached | 0x01000000, close_fds=True,
                                cwd=tempfile.gettempdir())
    except OSError:
        return subprocess.Popen(cmd, creationflags=detached, close_fds=True, cwd=tempfile.gettempdir())


def launch_updater(info: dict, appdir: Path | None = None) -> None:
    """Start the updater (a separate program, run from a temp copy so it can replace
    the files in the RoKWatcher folder) and return; the caller then closes RoKWatcher."""
    appdir = Path(appdir or app_dir())
    exe = appdir / UPDATER_EXE
    if exe.exists():
        tmp = Path(tempfile.gettempdir()) / f"RoKWatcherUpdater-{os.getpid()}.exe"
        shutil.copy2(exe, tmp)
        _spawn([str(tmp), "--pid", str(os.getpid()), "--url", info["url"],
                "--appdir", str(appdir), "--version", info["version"]])
        log.info("Updater started for version %s (%s)", info["version"], tmp)
        return
    # older downloads without the updater program: the PowerShell window
    script = Path(tempfile.gettempdir()) / "rokwatch_update.ps1"
    script.write_text(UPDATER_PS1, encoding="utf-8-sig")
    _spawn(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-WaitPid", str(os.getpid()), "-Url", info["url"], "-AppDir", str(appdir),
            "-Version", info["version"]])
    log.info("Updater (PowerShell) started for version %s", info["version"])
