@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-local-llm.ps1" %*
set "FH_INSTALL_RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %FH_INSTALL_RESULT%
