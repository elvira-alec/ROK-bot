@echo off
rem Opens your own Chrome so the watcher can attach to it (connect_to_chrome).
cd /d "%~dp0"
set CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" (
  echo Couldn't find Google Chrome. Install it, or leave connect_to_chrome empty.
  pause
  exit /b 1
)
start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%~dp0chrome-profile" --window-size=1600,900
