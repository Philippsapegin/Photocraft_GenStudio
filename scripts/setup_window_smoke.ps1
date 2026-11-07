param([string]$Root = (Split-Path $PSScriptRoot -Parent), [string]$Bundle = '')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$uiRoot=if($Bundle){$Bundle}else{Join-Path $Root 'packaging'}
$python=if($Bundle){Join-Path $Bundle 'runtime\.venv\Scripts\python.exe'}else{Join-Path $Root '.venv\Scripts\python.exe'}
Add-Type -Path (Join-Path $uiRoot 'SetupUi.cs') -ReferencedAssemblies System.Windows.Forms,System.Drawing
$folder=Join-Path $Root ('.runtime\setup-window-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($folder)
[GenGateSetup.Picker]::ProbeModern($folder)
[GenGateSetup.Picker]::ProbeModern('')
Write-Output 'Modern Explorer initialized'
[GenGateSetup.Picker]::ProbeOpenAndCancel($folder)
Write-Output 'Modern Explorer opened and cancelled'
if([GenGateSetup.Picker]::ProbeApartment() -ne 'STA'){throw 'Dialog apartment is not STA'}
$fixture=Join-Path $folder 'child.py'
[IO.File]::WriteAllText($fixture,"import sys`nprint('ERROR: synthetic diagnostic on stderr',file=sys.stderr,flush=True)`nprint('GENGATE_SETUP_RESULT {}',flush=True)`nsys.exit(int(sys.argv[1]))`n")
foreach($exitCode in @(0,1)){
    $runner=New-Object GenGateSetup.Runner($python,([GenGateSetup.Runner]::Quote($fixture)+' '+$exitCode),(Join-Path $folder ('runner-'+$exitCode+'.log')))
    try{
        $deadline=[DateTime]::UtcNow.AddSeconds(15)
        while(-not $runner.HasExited){if([DateTime]::UtcNow -gt $deadline){throw 'Synthetic child did not exit'};Start-Sleep -Milliseconds 15}
        if($runner.Finish() -ne $exitCode){throw 'Runner lost the real exit code'}
        $lines=$runner.Drain()
        if(($lines -join '') -notmatch 'synthetic diagnostic'){throw 'stderr output was lost'}
    }finally{$runner.Dispose()}
}
$argFixture=Join-Path $folder 'args.py'
[IO.File]::WriteAllText($argFixture,"import json,sys`nprint(json.dumps(sys.argv[1:]))`n")
$runner=New-Object GenGateSetup.Runner($python,([GenGateSetup.Runner]::Quote($argFixture)+' '+[GenGateSetup.Runner]::Quote('C:\')+' '+[GenGateSetup.Runner]::Quote('D:\Folder with spaces\')),(Join-Path $folder 'arguments.log'))
try{
    if($runner.Finish() -ne 0){throw 'Argument fixture failed'}
    $arguments=($runner.Drain() -join '') | ConvertFrom-Json
    if($arguments[0] -ne 'C:\' -or $arguments[1] -ne 'D:\Folder with spaces\'){throw 'Windows argument quoting changed a path'}
}finally{$runner.Dispose()}
foreach($state in @('ready','success','warning','failed')){
    $image=Join-Path $folder ($state+'.png')
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File (Join-Path $uiRoot 'Setup.ps1') -ValidateOnly -SelfTest -ValidationState $state -ValidationScreenshot $image
    if($LASTEXITCODE){throw ('UI validation failed: '+$state)}
}
if(-not $Bundle){
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File (Join-Path $uiRoot 'Setup.ps1') -Preview -ValidateOnly -SelfTest -ValidationScreenshot (Join-Path $folder 'preview.png')
    if($LASTEXITCODE){throw 'Dev preview allowed an installation or editor launch'}
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File (Join-Path $uiRoot 'Setup.ps1') -ValidateOnly -ValidationScale 1.5 -ValidationScreenshot (Join-Path $folder 'ready-150.png')
if($LASTEXITCODE){throw 'UI validation failed at 150 percent'}
$resumeFile=Join-Path $folder 'update-resume.json'
@{photocraftDir=(Join-Path $folder 'PhotoCraft');installDir=(Join-Path $folder 'GenGate');modelsDir=(Join-Path $folder 'Models');comfyDir=(Join-Path $folder 'ComfyUI');comfyPython=(Join-Path $folder 'python.exe');installDirect=$false} | ConvertTo-Json | Set-Content -LiteralPath $resumeFile -Encoding UTF8
foreach($state in @('ready','failed')){
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File (Join-Path $uiRoot 'Setup.ps1') -ValidateOnly -UpdateEditor -ResumeRequest $resumeFile -ValidationState $state -ValidationScreenshot (Join-Path $folder ('update-'+$state+'.png'))
    if($LASTEXITCODE){throw ('Update form validation failed: '+$state)}
}
$report=@{passed=$true;folder=$folder;uiRoot=$uiRoot;modernExplorerProbe=$true;emptyInitialFolder=$true;modernExplorerOpenedAndCancelled=$true;checkboxMouseAndKeyboard=$true;captionButtons=$true;runnerExitCodes=@(0,1);states=@('ready','success','warning','failed');scales=@(1,1.5);updateResume=$true;realInference=$false}
$report | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root '.runtime\release097-setup-ui.json') -Encoding UTF8
$report | ConvertTo-Json
