param([switch]$SkipClone, [switch]$SkipBuild, [string]$Python = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$source = Join-Path $projectRoot '.runtime\photocraft-source'
if (-not $Python) {
    $Python = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python)) { $Python = (Get-Command python -ErrorAction Stop).Source }
}
if (-not (Test-Path -LiteralPath (Join-Path $source 'Cargo.toml'))) {
    if ($SkipClone) { throw 'PhotoCraft source is missing.' }
    git clone --depth 1 --branch v0.8.2 https://github.com/storytold/photocraft.git $source
    if ($LASTEXITCODE) { throw 'PhotoCraft source download failed.' }
}
$revision = git -C $source rev-parse HEAD
if ($revision -ne 'd43db3e153c76aefce9b32cef9099e458eaf17a8') { throw 'Expected PhotoCraft v0.8.2 at the pinned commit.' }
& $Python (Join-Path $PSScriptRoot 'prepare_photocraft.py') $source
if ($LASTEXITCODE) { throw 'Could not apply Gen Studio integration.' }
if (-not $SkipBuild) {
    Push-Location $source
    try {
        cargo fmt --all
        if ($LASTEXITCODE) { throw 'Could not format the integration.' }
        # Keep peak compiler memory bounded on the same machine that runs the image editor.
        $env:CARGO_PROFILE_RELEASE_LTO = 'false'
        $env:CARGO_PROFILE_RELEASE_CODEGEN_UNITS = '8'
        cargo build --release --locked -p photocraft -p photocraft-cli --features heif -j 2
        if ($LASTEXITCODE) { throw 'PhotoCraft build failed.' }
    } finally { Pop-Location }
}
Write-Host (Join-Path $source 'target\release\photocraft.exe')
