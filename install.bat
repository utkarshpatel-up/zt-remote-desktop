@echo off
cd /d "%~dp0"
echo Installing ZT Remote Desktop (Windows)...
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/
  echo IMPORTANT: tick "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)
if not exist ".venv" python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Done. Run start.bat to launch the agent.
pause
