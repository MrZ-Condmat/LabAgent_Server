@echo off
setlocal

set "PROJECT_ROOT=%~dp0.."
cd /d "%PROJECT_ROOT%"

if not exist logs mkdir logs

echo ===== %date% %time% ===== >> logs\daily_report.log
"%PROJECT_ROOT%\labagent\Scripts\python.exe" "%PROJECT_ROOT%\scripts\generate_daily_report.py" --skip-weekends >> logs\daily_report.log 2>&1

exit /b %ERRORLEVEL%
