@echo off
rem Investment Monitor for Windows - double-click this file. The first start installs what it needs
rem (about 2 minutes), then your browser opens http://localhost:8000. Keep this window open while you
rem use it; close it to stop. Arguments go to the server, e.g. start-windows.bat --port 8001
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist "monitor\bootstrap.py" (
  echo The monitor folder is missing next to this file. Unzip the download first:
  echo right-click the ZIP file, choose "Extract All", then start this file from the extracted folder.
  goto failed
)

rem Find Python 3.11+: the py launcher (python.org installs it), else python on the PATH. The Microsoft
rem Store placeholder "python" only prints a hint and exits with 9009 - it counts as no Python.
set "PY="
set "OLD="
call :probe py -3
if defined PY goto run
call :probe python
if defined PY goto run
echo.
if defined OLD (
  echo Investment Monitor needs Python 3.11 or newer; the Python found here is older.
) else (
  echo Investment Monitor needs Python 3.11 or newer, and none was found.
)
echo Get it from https://www.python.org/downloads/ - in the installer, tick "Add python.exe to PATH".
echo Then start this file again.
goto failed

:run
%PY% -m monitor.bootstrap %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Something went wrong (see above).
pause
exit /b 1

:probe
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 3)" >nul 2>nul
if %errorlevel% equ 0 set "PY=%*"
if %errorlevel% equ 3 set "OLD=1"
exit /b 0
