@echo off
rem Double-click to open the clusterleg deck. Needs Python 3.9+ on PATH.
cd /d "%~dp0"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"

where pythonw >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH. Install Python 3 from python.org and tick "Add to PATH".
    pause
    exit /b 1
)

python -c "import udsoncan, serial" >nul 2>nul
if errorlevel 1 (
    echo First run: installing udsoncan, pyserial and esptool...
    python -m pip install --user "udsoncan>=1.15" "pyserial>=3.5" "esptool>=4.5"
    if errorlevel 1 (
        echo Install failed - see the message above.
        pause
        exit /b 1
    )
)

start "" pythonw -m clusterleg.deck
