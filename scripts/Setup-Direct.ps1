param([string]$Python = '', [string]$ComfySource = '', [switch]$InstallDependencies)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$configPath = Join-Path $projectRoot 'config.local.json'
$config = @{}
if (Test-Path -LiteralPath $configPath) { $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json }
if (-not $ComfySource) { $ComfySource = $config.comfy_dir }
if (-not $Python) { $Python = $config.comfy_python }
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
    throw 'Pass -Python with a CUDA-compatible Python executable. No ComfyUI installation is required.'
}
if ($ComfySource) {
    & $projectPython (Join-Path $PSScriptRoot 'prepare_qwen_core.py') --source $ComfySource
} else {
    & $projectPython (Join-Path $PSScriptRoot 'prepare_qwen_core.py')
}
if ($LASTEXITCODE) { throw 'Could not prepare the direct model library.' }
if ($InstallDependencies) {
    # Explicit opt-in; reuse an existing GPU Python without duplicating its packages.
    $uv = Get-Command uv -ErrorAction Stop
    & $uv.Source pip install --python $Python -r (Join-Path $projectRoot 'photocraft\direct-requirements.txt')
    if ($LASTEXITCODE) { throw 'Could not install direct inference dependencies.' }
}
Write-Host "Direct runtime Python: $Python"
Write-Host 'Select your existing Models folder in Gen Studio. No model weights were downloaded or loaded.'
