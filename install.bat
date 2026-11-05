@echo off
cd /d "%~dp0"
echo Installing what the watcher needs...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Python isn't installed or isn't on PATH. Install it from python.org
  echo and tick "Add Python to PATH", then run this again.
  pause
  exit /b 1
)
python -m playwright install chromium
if not exist config.yaml copy config.example.yaml config.yaml >nul
echo.
echo Done. Set game_url and ntfy_topic in config.yaml (opening it now),
echo save it, then double-click test_alert.bat and run.bat.
notepad config.yaml
pause
