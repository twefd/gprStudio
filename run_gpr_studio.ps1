# Launch GPR Concrete Studio (Streamlit) using the gprMax conda env's Python.
# Usage:  ./run_gpr_studio.ps1
$ErrorActionPreference = "Stop"
$envPython = Join-Path $env:USERPROFILE "miniconda3\envs\gprMax\python.exe"
if (-not (Test-Path $envPython)) {
    Write-Error "gprMax env Python not found at $envPython. Edit this path if your env lives elsewhere."
    exit 1
}
$app = Join-Path $PSScriptRoot "gpr_studio\app.py"
& $envPython -m streamlit run $app
