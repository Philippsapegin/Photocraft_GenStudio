$ErrorActionPreference = 'Stop'
try {
    $install = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'genstudio-install.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($install.installMode -ne 'overlay') { throw 'This launcher requires a GenGate add-on installation.' }
    if (-not (Test-Path -LiteralPath $install.executable)) { throw 'The installed PhotoCraft has moved. Run GenGate Setup again and select its new folder.' }
    $python = Join-Path $install.runtime '.venv\Scripts\python.exe'
    $manager = Join-Path $install.runtime 'packaging\manage.py'
    $check = & $python -X utf8 $manager --metadata (Join-Path $PSScriptRoot 'genstudio-install.json') 2>&1
    $checkExit = $LASTEXITCODE
    Start-Process -FilePath $install.executable -WorkingDirectory (Split-Path $install.executable -Parent)
    if ($checkExit) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show("PhotoCraft opened. GenGate needs attention:`n`n" + ($check -join "`n"), 'GenGate compatibility') | Out-Null
    }
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'PhotoCraft GenGate') | Out-Null
}
