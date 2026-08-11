# Launch gprStudio (Streamlit) using the gprMax conda env's Python.
# Also sets up the MSVC toolchain (so nvcc can compile CUDA kernels) and adds
# the CUDA Toolkit to PATH, which makes the in-app "Run on GPU" option work.
# If Visual C++ / CUDA are absent the app still runs on CPU.
# Usage:  ./run_gpr_studio.ps1
$ErrorActionPreference = "Stop"
$envPython = Join-Path $env:USERPROFILE "miniconda3\envs\gprMax\python.exe"
if (-not (Test-Path $envPython)) {
    Write-Error "gprMax env Python not found at $envPython. Edit this path if your env lives elsewhere."
    exit 1
}

# --- MSVC toolchain (needed so nvcc can build CUDA kernels for -gpu runs) ---
$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
    $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    $devShell = Join-Path $vsPath "Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
    if ($vsPath -and (Test-Path $devShell)) {
        Import-Module $devShell
        Enter-VsDevShell -VsInstallPath $vsPath -SkipAutomaticLocation -DevCmdArguments '-arch=x64' | Out-Null
    } else {
        Write-Warning "Visual C++ build tools not found - GPU runs need them; CPU runs are unaffected."
    }
}

# --- CUDA Toolkit on PATH (nvcc + runtime DLLs), if installed ---
$cudaRoot = Join-Path $env:ProgramFiles "NVIDIA GPU Computing Toolkit\CUDA"
if (Test-Path $cudaRoot) {
    $cudaBin = Get-ChildItem "$cudaRoot\v*\bin" -ErrorAction SilentlyContinue | Sort-Object FullName | Select-Object -Last 1
    if ($cudaBin) { $env:Path = "$($cudaBin.FullName);$env:Path" }
}

$app = Join-Path $PSScriptRoot "gpr_studio\app.py"
& $envPython -m streamlit run $app
