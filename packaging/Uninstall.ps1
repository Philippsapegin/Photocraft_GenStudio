$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
try {
    $metadata = Join-Path $PSScriptRoot 'genstudio-install.json'
    $install = Get-Content -LiteralPath $metadata -Raw -Encoding UTF8 | ConvertFrom-Json
    $python = Join-Path $install.runtime '.venv\Scripts\python.exe'
    $manager = Join-Path $install.runtime 'packaging\manage.py'
    $output = & $python -X utf8 $manager --metadata $metadata --restore 2>&1
    if ($LASTEXITCODE) { throw ($output -join "`n") }
    [System.Windows.Forms.MessageBox]::Show("GenGate has been removed from PhotoCraft. Updated PhotoCraft files, documents and shared models were kept.`n`nThe GenGate software folder and recovery backups were retained for reinstalling or manual removal.", 'GenGate') | Out-Null
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'GenGate') | Out-Null
}
