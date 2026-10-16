$ErrorActionPreference = 'Stop'
try {
    $install = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'genstudio-install.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not (Test-Path -LiteralPath $install.executable)) { throw 'The editor is missing. Run Setup.cmd again.' }
    $env:PHOTOCRAFT_CONFIG_DIR = $install.profile
    Start-Process -FilePath $install.executable -WorkingDirectory (Split-Path $install.executable -Parent)
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'PhotoCraft Gen Studio') | Out-Null
}
