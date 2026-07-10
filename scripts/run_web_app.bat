@echo off
setlocal

call conda deactivate 2>nul

set "PROJECT_ROOT=%~dp0.."
cd /d "%PROJECT_ROOT%"

"%PROJECT_ROOT%\labagent\Scripts\python.exe" -m streamlit run "%PROJECT_ROOT%\lab_agent\web\app.py"
