@echo off
REM Double-click launcher for the Materials Master app (Windows).
REM
REM Two roles, told apart by whether config.local.toml exists:
REM
REM   Operator (has config + service account key): installs the Google Sheets
REM   extras, and refreshes both sheets into the database on every launch.
REM
REM   Viewer (no config): app dependencies only, and NEVER rebuilds the
REM   database. A viewer's db\materials.db is a real-data snapshot handed to
REM   them by the operator; regenerating synthetic data over it would
REM   silently destroy the only copy they have.
REM
REM Safe to hand to a coworker: every setup step is checked, and a failed
REM setup cleans up after itself so the next double-click retries instead of
REM skipping past a half-built environment.

setlocal
cd /d "%~dp0"

REM --- Python present, and new enough ------------------------------------
where python >nul 2>nul
if errorlevel 1 goto :no_python

REM config.py needs stdlib tomllib (3.11+). This also catches the Microsoft
REM Store's python.exe alias stub, which `where` finds but which is not a
REM real interpreter.
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto :bad_python

REM --- Virtual environment ------------------------------------------------
REM Gate on streamlit.exe rather than on the venv folder. The folder appears
REM the moment `python -m venv` runs, so gating on it meant a failed pip
REM install left a broken environment that every later run skipped past --
REM permanently, until someone knew to delete venv\ by hand.
if exist "venv\Scripts\streamlit.exe" goto :activate

echo.
echo First-time setup: creating a virtual environment and installing
echo dependencies. This takes a few minutes and needs an internet connection.
echo.

REM Clear any half-built environment from a previous failed attempt.
if exist venv rmdir /s /q venv

python -m venv venv
if errorlevel 1 goto :setup_failed

call venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if errorlevel 1 goto :setup_failed
goto :sheets_extras

:activate
call venv\Scripts\activate.bat

:sheets_extras
REM Only an operator needs gspread/google-auth, and only they have anything
REM for it to connect to. Checked on every run rather than only at first
REM setup, so adding config.local.toml later installs them without needing
REM the environment rebuilt.
if not exist config.local.toml goto :ensure_db
python -c "import gspread" >nul 2>nul
if not errorlevel 1 goto :ensure_db

echo Local config found - installing Google Sheets support...
python -m pip install -r requirements-sheets.txt
if errorlevel 1 goto :setup_failed

:ensure_db
if exist config.local.toml goto :refresh

REM Viewer: leave an existing database strictly alone.
if exist "db\materials.db" goto :launch

echo.
echo No database and no local config found. Building a starter database from
echo synthetic sample data. (Real data comes from your colleague as a
echo db\materials.db file - see the README.)
echo.
python scripts\generate_synthetic_sheet.py
if errorlevel 1 goto :db_failed
python -m dtf_materials.etl
if errorlevel 1 goto :db_failed
goto :launch

:refresh
REM Operator: refresh by re-running the loaders, never by deleting the
REM database. The ETL is idempotent and atomic by design; deleting the file
REM would throw away the stable material_ids it works to preserve.
if /i "%~1"=="--no-refresh" goto :launch

echo.
echo Refreshing from Google Sheets... (skip this with: run_app.bat --no-refresh)
echo.
python -m dtf_materials.etl --source sheets
if errorlevel 1 goto :refresh_failed
python -m dtf_materials.lab_samples
if errorlevel 1 goto :lab_refresh_failed
goto :launch

:launch
streamlit run app.py
goto :end

REM --- Failure paths ------------------------------------------------------

:no_python
echo.
echo Python was not found on this computer.
echo Install Python 3.11 or newer from python.org, then run this file again.
echo Be sure to check "Add python.exe to PATH" during its setup.
echo.
pause
exit /b 1

:bad_python
echo.
echo Python was found, but it is not usable for this app.
echo.
echo This needs Python 3.11 or newer. Your version:
python --version
echo.
echo If that printed nothing, or opened the Microsoft Store, the "python" on
echo this machine is the Store's placeholder rather than a real install.
echo Either way: install Python 3.11+ from python.org and check
echo "Add python.exe to PATH" during setup, then run this file again.
echo.
pause
exit /b 1

:setup_failed
echo.
echo Setup failed - the dependencies could not be installed.
echo Most often this is no internet connection, or a company network
echo blocking pip. The half-built environment has been left in place and
echo will be rebuilt automatically next time; just run this file again once
echo you are connected.
echo.
pause
exit /b 1

:db_failed
echo.
echo Could not build the starter database. This is a bug rather than a
echo setup problem - the messages above say what failed.
echo.
pause
exit /b 1

:refresh_failed
echo.
echo   WARNING: could not refresh the inventory from Google Sheets.
echo   Common causes: no internet connection, the sheet is not shared with
echo   the service account, or config.local.toml points at the wrong sheet.
echo.
if not exist "db\materials.db" goto :db_failed
echo   Launching with the data already in the database instead.
echo.
goto :launch

:lab_refresh_failed
echo.
echo   NOTE: the lab sample catalog did not refresh. If config.local.toml has
echo   no [lab_sheet] section, that is expected - see config.example.toml.
echo   The inventory refresh above succeeded either way.
echo.
goto :launch

:end
pause
