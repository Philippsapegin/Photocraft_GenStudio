param([switch]$SkipClone, [switch]$SkipBuild, [string]$Python = '',
      [ValidateSet('0.8.2','0.12.0','0.15.1')][string]$HostVersion = '0.15.1', [string]$TargetDir = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$sourceName = if ($HostVersion -eq '0.8.2') { 'photocraft-source' } else { 'photocraft-' + $HostVersion }
$source = Join-Path $projectRoot ('.runtime\' + $sourceName)
$bases = Get-Content -LiteralPath (Join-Path $projectRoot 'photocraft\editor-bases.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$expected = $bases.$HostVersion.upstream
if (-not $Python) {
    $Python = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python)) { $Python = (Get-Command python -ErrorAction Stop).Source }
}
if (-not (Test-Path -LiteralPath (Join-Path $source 'Cargo.toml'))) {
    if ($SkipClone) { throw 'PhotoCraft source is missing.' }
    git clone --depth 1 --branch ('v' + $HostVersion) https://github.com/storytold/photocraft.git $source
    if ($LASTEXITCODE) { throw 'PhotoCraft source download failed.' }
}
$revision = git -C $source rev-parse HEAD
if ($revision -ne $expected) { throw ('Expected PhotoCraft ' + $HostVersion + ' at the pinned commit.') }
& $Python (Join-Path $PSScriptRoot 'prepare_photocraft.py') $source
if ($LASTEXITCODE) { throw 'Could not apply Gen Studio integration.' }
if (-not $SkipBuild) {
    if ($HostVersion -ne '0.8.2') {
        $fontRootJson = & $Python -X utf8 (Join-Path $PSScriptRoot 'prepare_craft_fonts.py') --json
        if ($LASTEXITCODE) { throw 'Could not verify the official PhotoCraft release fonts.' }
        $fontRoot = $fontRootJson | ConvertFrom-Json
        $env:CRAFT_FONTS_DIR = [string]$fontRoot
        $env:CRAFT_FONTS_REQUIRED = '1'
    }
    Push-Location $source
    try {
        # Formatting the whole growing workspace can exceed Windows' command-line
        # limit. These are the only crates modified by the integration.
        cargo fmt -p photocraft-engine -p photocraft-ui-egui -p photocraft -p photocraft-cli
        if ($LASTEXITCODE) { throw 'Could not format the integration.' }
        # Resolve hooks anchored in the newly formatted generated sections. A
        # fresh upstream checkout and an already prepared checkout must converge.
        & $Python (Join-Path $PSScriptRoot 'prepare_photocraft.py') $source
        if ($LASTEXITCODE) { throw 'Could not finish the native integration.' }
        cargo fmt -p photocraft-engine -p photocraft-ui-egui -p photocraft -p photocraft-cli
        if ($LASTEXITCODE) { throw 'Could not format the completed integration.' }
        # Keep peak compiler memory bounded on the same machine that runs the image editor.
        $env:CARGO_PROFILE_RELEASE_LTO = 'false'
        $env:CARGO_PROFILE_RELEASE_CODEGEN_UNITS = '8'
        if ($TargetDir) { $env:CARGO_TARGET_DIR = $TargetDir }
        cargo build --release --locked -p photocraft -p photocraft-cli --features heif -j 2
        if ($LASTEXITCODE) { throw 'PhotoCraft build failed.' }
    } finally { Pop-Location }
}
$outputTarget = if ($TargetDir) { $TargetDir } elseif ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $source 'target' }
Write-Host (Join-Path $outputTarget 'release\photocraft.exe')
