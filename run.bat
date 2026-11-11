@echo off
REM Start the Aniimo draft bot (Windows). First run creates a virtual environment.
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\pip install -e .
)
if not exist config.yaml (
  .venv\Scripts\aniimo-pvp init
  echo Fill in config.yaml and .env, then run run.bat again.
  pause
  exit /b 1
)
if "%~1"=="" (
  .venv\Scripts\aniimo-pvp run
) else (
  .venv\Scripts\aniimo-pvp %*
)
pause
