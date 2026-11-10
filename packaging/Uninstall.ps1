param([string]$Metadata='', [string]$HostFolder='', [switch]$ElevatedRestore)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
try {
    $bundlePython=Join-Path $PSScriptRoot 'runtime\.venv\Scripts\python.exe'
    if(-not $Metadata -and -not $HostFolder){$Metadata=Join-Path $PSScriptRoot 'genstudio-install.json'}
    if(-not $HostFolder -and (-not $Metadata -or -not(Test-Path -LiteralPath $Metadata)) -and (Test-Path -LiteralPath $bundlePython)){
        $saved=(& $bundlePython -X utf8 (Join-Path $PSScriptRoot 'runtime\genstudio\setup_state.py')) | ConvertFrom-Json
        if($saved.found -and $saved.installDir){$Metadata=Join-Path $saved.installDir 'genstudio-install.json'}
    }
    if($Metadata -and (Test-Path -LiteralPath $Metadata)){
        $install=Get-Content -LiteralPath $Metadata -Raw -Encoding UTF8 | ConvertFrom-Json
        $HostFolder=$install.host
        $python=Join-Path $install.runtime '.venv\Scripts\python.exe'
        $manager=Join-Path $install.runtime 'packaging\manage.py'
        if(-not(Test-Path -LiteralPath $python)){$python=$bundlePython;$manager=Join-Path $PSScriptRoot 'runtime\packaging\manage.py'}
        $managerArgs=@('--metadata',$Metadata,'--restore')
    }else{
        if(-not $HostFolder){
            $picker=New-Object System.Windows.Forms.FolderBrowserDialog
            $picker.Description='Select the PhotoCraft folder to remove GenGate from an older installation.'
            try{if($picker.ShowDialog() -ne 'OK'){return};$HostFolder=$picker.SelectedPath}finally{$picker.Dispose()}
        }
        $python=$bundlePython;$manager=Join-Path $PSScriptRoot 'runtime\packaging\manage.py'
        $managerArgs=@('--host',$HostFolder,'--restore')
    }
    if(-not(Test-Path -LiteralPath $python) -or -not(Test-Path -LiteralPath $manager)){throw 'Extract the complete current release ZIP and run its Uninstall.cmd, or run Uninstall.cmd from the installed GenGate folder.'}
    # Elevate only to restore a legacy shared host, never to install another user's runtime.
    $needsAdmin=$false
    try{$probe=[IO.File]::Open((Join-Path $HostFolder 'photocraft.exe'),'Open','Write','ReadWrite');$probe.Dispose()}
    catch [UnauthorizedAccessException]{$needsAdmin=$true}
    if($needsAdmin){
        if($ElevatedRestore){throw 'The PhotoCraft folder is still not writable.'}
        $quote={param($value) '"'+$value.Replace('"','\"')+'"'}
        $arguments='-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File '+(& $quote $PSCommandPath)+' -HostFolder '+(& $quote $HostFolder)+' -ElevatedRestore'
        if($Metadata -and (Test-Path -LiteralPath $Metadata)){$arguments+=' -Metadata '+(& $quote $Metadata)}
        Start-Process -FilePath powershell.exe -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait
        return
    }
    $cleanup=[System.Windows.Forms.MessageBox]::Show('Also remove GenGate recovery backups after PhotoCraft is restored? Documents and shared models are kept. Choose No to keep rollback backups.','Remove GenGate recovery data','YesNoCancel','Question')
    if($cleanup -eq 'Cancel'){return}
    if($cleanup -eq 'Yes'){$managerArgs+='--cleanup-recovery'}
    $output=& $python -X utf8 $manager @managerArgs 2>&1
    if($LASTEXITCODE){throw ($output -join "`n")}
    $recoveryText=if($cleanup -eq 'Yes'){'Recovery backups were removed after verifying the restored originals.'}else{'Recovery backups were kept for rollback.'}
    [System.Windows.Forms.MessageBox]::Show("GenGate was removed. PhotoCraft, documents, unrelated settings and shared models were kept.`n`n"+$recoveryText,'GenGate') | Out-Null
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.GetBaseException().Message,'GenGate') | Out-Null
}
