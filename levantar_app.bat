@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
    echo Python launcher py is required. Install Python 3.13 or later with the Windows launcher. 1>&2
    exit /b 9009
)
set "PYTHONDONTWRITEBYTECODE=1"
py -B "%~dp0tools\java_local.py" %*
exit /b %ERRORLEVEL%
