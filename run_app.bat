@echo off
REM Double-click launcher for the Materials Master app (Windows).
REM Safe to hand to a coworker: on first run it creates the virtual
REM environment, installs dependencies, and builds a starter database from
REM the synthetic sample data if one isn't there yet. Every run after that
REM just activates the environment and launches the app.

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on this computer.
    echo Install Python 3.11+ from python.org, then run this file again.
    echo Be sure to check "Add python.exe to PATH" during its setup.
    pause
    exit /b 1
)

if not exist venv (
    echo First-time setup: creating a virtual environment and installing dependencies...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

if not exist db\materials.db (
    echo No database found yet. Building a starter one from the sample data...
    python scripts\generate_synthetic_sheet.py
    python -m dtf_materials.etl
)

streamlit run app.py
pause
