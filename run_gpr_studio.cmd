@echo off
REM Launch gprStudio (Streamlit) using the gprMax conda env's Python.
REM Batch files are NOT affected by PowerShell's script execution policy, so
REM this works even when run_gpr_studio.ps1 is blocked.
REM
REM This also sets up the MSVC toolchain (so nvcc can compile CUDA kernels) and
REM adds the CUDA Toolkit to PATH, which is what makes the in-app "Run on GPU"
REM option work. If Visual C++ / CUDA are absent the app still runs on CPU.
REM Usage:  double-click this file, or run  run_gpr_studio.cmd
setlocal
set "ENVPY=%USERPROFILE%\miniconda3\envs\gprMax\python.exe"
if not exist "%ENVPY%" (
  echo [ERROR] gprMax env Python not found at "%ENVPY%".
  echo Edit ENVPY in this file if your conda env lives elsewhere.
  pause
  exit /b 1
)

REM --- MSVC toolchain (needed so nvcc can build CUDA kernels for -gpu runs) ---
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
set "VSPATH="
if exist "%VSWHERE%" (
  for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSPATH=%%i"
)
if defined VSPATH (
  if exist "%VSPATH%\VC\Auxiliary\Build\vcvarsall.bat" (
    call "%VSPATH%\VC\Auxiliary\Build\vcvarsall.bat" x64 >nul
  )
)
if not defined VSPATH echo [note] Visual C++ build tools not found - GPU runs need them; CPU runs are unaffected.

REM --- CUDA Toolkit on PATH (nvcc + runtime DLLs), if installed ---
set "CUDA_DIR="
for /d %%d in ("%ProgramFiles%\NVIDIA GPU Computing Toolkit\CUDA\v*") do set "CUDA_DIR=%%d"
if defined CUDA_DIR set "PATH=%CUDA_DIR%\bin;%PATH%"

"%ENVPY%" -m streamlit run "%~dp0gpr_studio\app.py"
