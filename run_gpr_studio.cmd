@echo off
REM Launch gprStudio (Streamlit) using the gprMax conda env's Python.
REM Batch files are NOT affected by PowerShell's script execution policy, so
REM this works even when run_gpr_studio.ps1 is blocked.
REM Usage:  double-click this file, or run  run_gpr_studio.cmd
setlocal
set "ENVPY=%USERPROFILE%\miniconda3\envs\gprMax\python.exe"
if not exist "%ENVPY%" (
  echo [ERROR] gprMax env Python not found at "%ENVPY%".
  echo Edit ENVPY in this file if your conda env lives elsewhere.
  pause
  exit /b 1
)
"%ENVPY%" -m streamlit run "%~dp0gpr_studio\app.py"
