param([switch]$Preview, [switch]$ValidateOnly, [string]$ValidationScreenshot = '', [ValidateSet('ready','success','warning','failed')][string]$ValidationState = 'ready', [double]$ValidationScale = 1, [switch]$SelfTest, [string]$BundleRoot='', [string]$EditorFolder='', [string]$TargetVersion='', [switch]$UpdateEditor, [string]$ResumeRequest='')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -Path (Join-Path $PSScriptRoot 'SetupUi.cs') -ReferencedAssemblies System.Windows.Forms,System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$bundle = if($BundleRoot){$BundleRoot}else{$PSScriptRoot}
$resume=$null
if($ResumeRequest){$resume=Get-Content -LiteralPath $ResumeRequest -Raw -Encoding UTF8 | ConvertFrom-Json;if($resume._profile){$env:PHOTOCRAFT_CONFIG_DIR=$resume._profile}}
$python = Join-Path $bundle 'runtime\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python) -and -not ($ValidateOnly -or $Preview)) {
    [System.Windows.Forms.MessageBox]::Show('Extract the complete GenGate add-on ZIP, then run Setup.cmd. Install PhotoCraft separately first.', 'GenGate') | Out-Null
    exit 1
}
$form = New-Object GenGateSetup.ChromeForm
$form.Preview = [bool]$ValidateOnly
$form.Text = 'GenGate 0.9.9 - Setup'
$form.Font = New-Object System.Drawing.Font('Segoe UI',9.5)
$form.ClientSize = New-Object System.Drawing.Size(820,([Math]::Min(870,[System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea.Height-60)))
$form.MinimumSize = New-Object System.Drawing.Size(760,640)
$form.StartPosition = 'CenterScreen'
$form.KeyPreview = $true
$script:child = $null; $script:finished = $false; $script:cancelled = $false
$script:requestFile = $null; $script:logFile = $null; $script:result = $null
$script:loadingSettings=$false; $script:loadedHost=''; $script:reuseDirect=$false
$script:compatibility=$null; $script:compatResult=$null; $script:operation='install'; $script:waitingUpdate=$false; $script:closeAfterUpdater=$false;$script:cancelFile=$null
$script:checkedFingerprint=''; $script:compatTimer=$null
$setupInspector=Join-Path $bundle 'runtime\genstudio\setup_state.py'; $setupPython=$python
if(-not (Test-Path -LiteralPath $setupInspector)){
    $setupInspector=Join-Path $bundle '..\genstudio\setup_state.py'; $setupPython=Join-Path $bundle '..\.venv\Scripts\python.exe'
}
$script:editControls = New-Object System.Collections.Generic.List[System.Windows.Forms.Control]
function Label([string]$text,[int]$x,[int]$y,[int]$width,[int]$height=24) {
    $c=New-Object System.Windows.Forms.Label; $c.Text=$text; $c.SetBounds($x,$y,$width,$height); $c.ForeColor=[GenGateSetup.Palette]::Text; return $c
}
function Button([string]$text,[int]$x,[int]$y,[int]$width=94) {
    $c=New-Object GenGateSetup.FlatButton; $c.Text=$text; $c.SetBounds($x,$y,$width,34); return $c
}
$header=New-Object System.Windows.Forms.Panel; $header.Width=$form.ClientSize.Width; $header.Dock='Top'; $header.Height=58; $header.BackColor=[GenGateSetup.Palette]::Surface
$title=Label 'GenGate' 24 10 180 24; $title.Font=New-Object System.Drawing.Font('Segoe UI',12,[System.Drawing.FontStyle]::Bold)
$version=Label 'ADD-ON SETUP  /  0.9.9' 24 35 250 18; $version.Font=New-Object System.Drawing.Font('Segoe UI',8); $version.ForeColor=[GenGateSetup.Palette]::Muted
$minimize=New-Object GenGateSetup.CaptionButton; $minimize.SetBounds(712,10,46,38); $minimize.AccessibleName='Minimize'; $minimize.Anchor='Top,Right'; $minimize.Add_Click({$form.WindowState='Minimized'})
$titleClose=New-Object GenGateSetup.CaptionButton; $titleClose.SetBounds(758,10,46,38); $titleClose.CloseButton=$true; $titleClose.AccessibleName='Close'; $titleClose.Anchor='Top,Right'
$header.Controls.AddRange(@($title,$version,$minimize,$titleClose)); $header.Add_MouseDown({if($_.Button -eq 'Left'){$form.DragWindow()}}); $title.Add_MouseDown({if($_.Button -eq 'Left'){$form.DragWindow()}})
$footer=New-Object System.Windows.Forms.Panel; $footer.Width=$form.ClientSize.Width; $footer.Dock='Bottom'; $footer.Height=96
$progress=New-Object GenGateSetup.Progress; $progress.Dock='Top'; $progress.Value=0
$status=Label 'Ready to install' 24 13 520 25
$detail=Label 'Close PhotoCraft before installing. Existing settings and models are kept.' 24 42 470 50; $detail.ForeColor=[GenGateSetup.Palette]::Muted; $detail.Font=New-Object System.Drawing.Font('Segoe UI',8.5)
$close=Button 'Close' 570 41 96; $close.Anchor='Bottom,Right'
$install=Button 'Install' 678 41 118; $install.Primary=$true; $install.Anchor='Bottom,Right'
$footer.Controls.AddRange(@($status,$detail,$close,$install,$progress))
$scroll=New-Object GenGateSetup.ScrollArea; $scroll.Dock='Fill'
$body=New-Object GenGateSetup.Body; $scroll.Attach($body)
$form.Controls.Add($scroll);$form.Controls.Add($footer);$form.Controls.Add($header)
function AddRow([string]$heading,[int]$height=66) {
    $row=New-Object System.Windows.Forms.Panel; $row.Height=$height; $row.Margin=New-Object System.Windows.Forms.Padding(0,0,0,8)
    if($heading){$row.Controls.Add((Label $heading 0 0 730 24))}; $body.Controls.Add($row); return $row
}
function FieldRow([string]$heading,[string]$initial='') {
    $row=AddRow $heading
    $surface=New-Object GenGateSetup.Surface; $surface.SetBounds(0,28,626,34); $surface.Anchor='Top,Left,Right'
    $field=New-Object System.Windows.Forms.TextBox; $field.AccessibleName=$heading; $field.BorderStyle='None'; $field.BackColor=[GenGateSetup.Palette]::Surface; $field.ForeColor=[GenGateSetup.Palette]::Text; $field.Text=$initial; $field.Dock='Fill'; $surface.Controls.Add($field)
    $browse=Button 'Browse' 638 28 110; $browse.Anchor='Top,Right'; $row.Controls.AddRange(@($surface,$browse))
    $script:editControls.Add($field);$script:editControls.Add($browse)
    return [pscustomobject]@{Row=$row;Field=$field;Browse=$browse}
}
function SetStatus([string]$state,[string]$text,[string]$extra='') {
    $status.Text=$text; $detail.Text=$extra
    $status.ForeColor=switch($state){'failed'{[System.Drawing.Color]::FromArgb(243,135,145)} 'warning'{[System.Drawing.Color]::FromArgb(238,194,106)} 'success'{[System.Drawing.Color]::FromArgb(153,214,171)} default{[GenGateSetup.Palette]::Text}}
}
function Browse($field,[bool]$folder=$true) {
    try { $path=[GenGateSetup.Picker]::Choose($form,$field.Text,$folder); if($path){$field.Text=$path;SetStatus 'ready' 'Ready to install' 'Close PhotoCraft before installing. Existing settings and models are kept.'}; return [bool]$path }
    catch { SetStatus 'warning' 'The browser could not open' 'Paste the path into the field, or try Browse again.'; $log.AppendText([Environment]::NewLine+$_.Exception.GetBaseException().Message); return $false }
}
$intro=AddRow '' 51
$lead=Label 'Local AI tools, inside PhotoCraft.' 0 0 720 25; $lead.Font=New-Object System.Drawing.Font('Segoe UI',12)
$intro.Controls.Add($lead); $hint=Label 'PhotoCraft 0.21.0  |  Windows x64  |  No editor or model weights bundled' 0 29 730 22; $hint.ForeColor=[GenGateSetup.Palette]::Muted; $hint.Font=New-Object System.Drawing.Font('Segoe UI',8.5); $intro.Controls.Add($hint)
$hostCandidates=@((Join-Path $env:LOCALAPPDATA 'Programs\ArtCraft\PhotoCraft'),(Join-Path $env:ProgramFiles 'ArtCraft\PhotoCraft'),(Join-Path $env:LOCALAPPDATA 'Programs\PhotoCraft'),(Join-Path $env:ProgramFiles 'PhotoCraft'))
foreach($drive in (Get-PSDrive -PSProvider FileSystem)){$hostCandidates+=Join-Path $drive.Root 'Programs\PhotoCraft'}
$hostDefault='';foreach($candidate in $hostCandidates){if((Test-Path -LiteralPath (Join-Path $candidate 'photocraft.exe')) -and (Test-Path -LiteralPath (Join-Path $candidate 'photocraft-cli.exe'))){$hostDefault=$candidate;break}}
if($EditorFolder){$hostDefault=$EditorFolder}
$hostRow=FieldRow 'PhotoCraft folder' $hostDefault; $photocraftDir=$hostRow.Field; $hostRow.Browse.Add_Click({[void](Browse $photocraftDir)})
$installRow=FieldRow 'GenGate install folder' (Join-Path $env:LOCALAPPDATA 'Programs\GenGate'); $installDir=$installRow.Field; $installRow.Browse.Add_Click({[void](Browse $installDir)})
$modelsRow=FieldRow 'Shared models folder' ''; $modelsDir=$modelsRow.Field
$directRow=AddRow '' 89
$direct=New-Object GenGateSetup.AccentCheck; $direct.Text='Install Direct engine'; $direct.AccessibleName=$direct.Text; $direct.Checked=$true; $direct.SetBounds(0,0,730,25)
$directNote=Label "NVIDIA GPU / CUDA 13 driver required. Downloads several GB on first install.`nDisable for advanced users: ComfyUI will be the only engine." 0 31 730 48
$directNote.Font=New-Object System.Drawing.Font('Segoe UI',8.5)
$directRow.Controls.AddRange(@($direct,$directNote)); $script:editControls.Add($direct)
$comfyNoteRow=AddRow '' 36; $comfyNote=Label '' 0 0 730 32; $comfyNote.Font=New-Object System.Drawing.Font('Segoe UI',8.5);$comfyNote.ForeColor=[GenGateSetup.Palette]::Muted;$comfyNoteRow.Controls.Add($comfyNote)
$pythonRow=FieldRow 'ComfyUI Python (python.exe)';$comfyPython=$pythonRow.Field;$pythonRow.Browse.Add_Click({[void](Browse $comfyPython $false)})
$comfyRow=FieldRow 'ComfyUI folder (contains main.py)';$comfyDir=$comfyRow.Field;$comfyRow.Browse.Add_Click({[void](Browse $comfyDir)})
$logRow=AddRow 'Installation details' 131
$log=New-Object System.Windows.Forms.TextBox;$log.SetBounds(0,28,$logRow.ClientSize.Width,100);$log.Anchor='Top,Left,Right';$log.Multiline=$true;$log.ReadOnly=$true;$log.BorderStyle='None';$log.ScrollBars='None';$log.BackColor=[GenGateSetup.Palette]::Surface;$log.ForeColor=[GenGateSetup.Palette]::Muted;$log.Font=New-Object System.Drawing.Font('Consolas',8.5);$log.Text='Ready. Your existing PhotoCraft settings and model files are kept.';$logRow.Controls.Add($log)
function UpdateFields {
    $directNote.ForeColor=if($direct.Checked){[GenGateSetup.Palette]::Muted}else{[System.Drawing.Color]::FromArgb(238,194,106)}
    $directNote.Text=if($direct.Checked){"NVIDIA GPU / CUDA 13 driver required. Downloads several GB on first install.`nDisable for advanced users: ComfyUI will be the only engine."}else{"Direct will not be installed.`nComfyUI will be the only engine (advanced users)."}
    if($direct.Checked -and $script:reuseDirect -and $photocraftDir.Text -eq $script:loadedHost){$directNote.Text="Existing Direct runtime found. Setup will check compatibility and reuse it.`nDisable for advanced users: ComfyUI will be the only engine."}
    $comfyNote.Text=if($direct.Checked){'ComfyUI (optional). Configure it now, or later in PhotoCraft preferences.'}else{'ComfyUI is required. Select its Python and application folder below.'}
}
$direct.Add_CheckedChanged({UpdateFields})
function SetupRequest {
    return @{installDir=$installDir.Text;photocraftDir=$photocraftDir.Text;modelsDir=$modelsDir.Text;installDirect=$direct.Checked;comfyPython=$comfyPython.Text;comfyDir=$comfyDir.Text;installLanPaint=$true}
}
$modelsRow.Browse.Add_Click({
    if(Browse $modelsDir){
        $candidate=Split-Path $modelsDir.Text -Parent
        if($candidate -and (Test-Path -LiteralPath (Join-Path $candidate 'main.py'))){
            $comfyDir.Text=$candidate
            foreach($path in @((Join-Path (Split-Path $candidate -Parent) 'python_embeded\python.exe'),(Join-Path $candidate '.venv\Scripts\python.exe'))){if(Test-Path -LiteralPath $path){$comfyPython.Text=$path;break}}
        }
    }
})
function ReadSetupState([string[]]$stateArguments) {
    if(-not (Test-Path -LiteralPath $setupPython) -or -not (Test-Path -LiteralPath $setupInspector)){return $null}
    $output=& $setupPython -X utf8 $setupInspector @stateArguments
    $value=($output -join [Environment]::NewLine) | ConvertFrom-Json
    if($LASTEXITCODE -ne 0){throw $value.error};return $value
}
function LoadSetupState([string]$hostPath='',[string]$installPath='') {
    if($script:loadingSettings -or $script:child){return}
    $script:loadingSettings=$true
    try {
        $stateArguments=@();if($hostPath){$stateArguments+=@('--host',$hostPath)}elseif($installPath){$stateArguments+=@('--install',$installPath)}
        $saved=ReadSetupState $stateArguments
        if($saved -and $saved.found){
            $photocraftDir.Text=$saved.host
            if($saved.installDir){$installDir.Text=$saved.installDir}
            $modelsDir.Text=[string]$saved.settings.modelsDir;$comfyDir.Text=[string]$saved.settings.comfyDir;$comfyPython.Text=[string]$saved.settings.comfyPython
            $direct.Checked=[bool]$saved.installDirect;$script:loadedHost=$saved.host;$script:reuseDirect=[bool]$saved.directReusable
            SetStatus 'ready' 'Existing installation found' 'Saved folders and engine settings have been loaded.'
        }else{$script:reuseDirect=$false}
    }catch{SetStatus 'warning' 'Saved settings need attention' $_.Exception.GetBaseException().Message}
    finally{$script:loadingSettings=$false;UpdateFields;if($script:compatTimer){$script:compatTimer.Start()}}
}
$settingsTimer=New-Object System.Windows.Forms.Timer;$settingsTimer.Interval=400
$script:settingsSource='host'
$settingsTimer.Add_Tick({$settingsTimer.Stop();if($script:settingsSource -eq 'install'){LoadSetupState '' $installDir.Text}else{LoadSetupState $photocraftDir.Text}})
if(-not $ValidateOnly -or $env:GENGATE_SETUP_VALIDATION_HOST){
    $photocraftDir.Add_TextChanged({if(-not $script:loadingSettings){$script:settingsSource='host';$settingsTimer.Stop();$settingsTimer.Start()}})
    $installDir.Add_TextChanged({if(-not $script:loadingSettings){$script:settingsSource='install';$settingsTimer.Stop();$settingsTimer.Start()}})
    if(-not $ValidateOnly){if($EditorFolder){LoadSetupState $EditorFolder}else{LoadSetupState}}
}
if($resume){
    $script:loadingSettings=$true
    $photocraftDir.Text=$resume.photocraftDir;$installDir.Text=$resume.installDir;$modelsDir.Text=$resume.modelsDir;$comfyDir.Text=$resume.comfyDir;$comfyPython.Text=$resume.comfyPython;$direct.Checked=[bool]$resume.installDirect
    $script:loadingSettings=$false
}
if($UpdateEditor){
    $form.Text='PhotoCraft update';$lead.Text='Update PhotoCraft with GenGate';$hint.Text='Your engine, model folders and generation settings will be kept.'
    foreach($row in @($installRow.Row,$modelsRow.Row,$directRow,$comfyNoteRow,$pythonRow.Row,$comfyRow.Row)){$row.Visible=$false}
    $hostRow.Browse.Enabled=$false;$photocraftDir.ReadOnly=$true;$logRow.Height=251;$log.Height=220
    $form.MinimumSize=New-Object System.Drawing.Size(760,460);$form.ClientSize=New-Object System.Drawing.Size(820,540)
}
UpdateFields
function DrainOutput {
    foreach($line in $script:child.Drain()){
        if($line.StartsWith('GENGATE_SETUP_RESULT ')){try{$script:result=$line.Substring(21) | ConvertFrom-Json}catch{}}
        elseif($line.StartsWith('GENGATE_SETUP_COMPAT ')){try{$script:compatResult=$line.Substring(21) | ConvertFrom-Json}catch{}}
        elseif($line.StartsWith('GENGATE_SETUP_PHASE ')){try{$phase=$line.Substring(20) | ConvertFrom-Json;SetStatus 'ready' 'Updating PhotoCraft and GenGate' $phase.message}catch{}}
        else{$log.AppendText([Environment]::NewLine+$line)}
    }
    if($log.TextLength -gt 18000){$log.Text=$log.Text.Substring($log.TextLength-16000)};$log.SelectionStart=$log.TextLength;$log.ScrollToCaret()
}
function CompleteSetup([int]$code) {
    $script:finished=$false; $install.Text='Install'
    foreach($control in $script:editControls){$control.Enabled=$true};UpdateFields
    $close.Text='Close';$install.Enabled=$true
    if($code -eq 2 -and $script:result -and $script:result.status -eq 'needsPhotoCraft'){
        $script:compatibility=$script:result.compatibility;ShowCompatibility;$progress.Value=0
    }
    elseif($code -eq 3 -and $script:result.status -eq 'restartRequired'){SetStatus 'warning' 'Restart Windows to continue' $script:result.message;$progress.Value=0}
    elseif($script:cancelled -or ($script:result -and $script:result.status -eq 'cancelled')){SetStatus 'warning' 'Installation cancelled' 'Run Setup again to finish or repair the installation.';$progress.Value=0}
    elseif($code -eq 0){
        $script:finished=$true;$install.Text='Open PhotoCraft';$progress.Value=100
        if($script:result -and $script:result.status -eq 'installedWithWarnings'){SetStatus 'warning' 'Installed with notes' 'GenGate is installed. Review the installation details for optional setup items.'}
        else{SetStatus 'success' 'Installed successfully' 'GenGate is ready. Open PhotoCraft to continue.'}
        $log.AppendText([Environment]::NewLine+$status.Text)
        if(-not ($ValidateOnly -or $Preview)){
            try{[void](ReadSetupState -stateArguments @('--remember','--host',$photocraftDir.Text,'--install',$installDir.Text))}
            catch{$log.AppendText([Environment]::NewLine+'NOTE: The setup location could not be remembered.')}
        }
    }else{
        ShowSetupFields
        $failure=if($script:result -and $script:result.message){$script:result.message}else{'Correct the setting and retry. Log: '+$script:logFile}
        SetStatus 'failed' 'Installation did not complete' $failure;$progress.Value=0;$log.AppendText([Environment]::NewLine+$failure+[Environment]::NewLine+'Setup failed (exit '+$code+').')
    }
    if($script:logFile){
        try{[IO.File]::AppendAllText($script:logFile,($status.Text+[Environment]::NewLine),(New-Object System.Text.UTF8Encoding($false)))}
        catch{$log.AppendText([Environment]::NewLine+'NOTE: The final status could not be saved to the log.')}
    }
    $progress.Invalidate()
}
$timer=New-Object System.Windows.Forms.Timer;$timer.Interval=180
$timer.Add_Tick({
    if(-not $script:child){return}
    $progress.Advance();DrainOutput
    if($script:child.HasExited){
        $timer.Stop();$code=$script:child.Finish();DrainOutput;$script:child.Dispose();$script:child=$null
        CompleteSetup $code
        if($script:operation -eq 'editorUpdate'){
            if($code -eq 0 -and $UpdateEditor -and -not $script:closeAfterUpdater -and -not ($ValidateOnly -or $Preview)){
                try{& (Join-Path $installDir.Text 'Launch.ps1');$form.Close()}catch{SetStatus 'warning' 'Updated; PhotoCraft could not open' $_.Exception.Message}
            }elseif($script:closeAfterUpdater){$form.Close()}
        }
        if($script:requestFile){Remove-Item -LiteralPath $script:requestFile -Force -ErrorAction SilentlyContinue}
        if($script:cancelFile){Remove-Item -LiteralPath $script:cancelFile -Force -ErrorAction SilentlyContinue}
    }
})
function EditorFingerprint {
    return ((@('photocraft.exe','photocraft-cli.exe') | ForEach-Object {
        $file=Get-Item -LiteralPath (Join-Path $photocraftDir.Text $_) -ErrorAction SilentlyContinue
        if($file){$file.FullName+':'+$file.Length+':'+$file.LastWriteTimeUtc.Ticks}else{'missing:'+$_}
    }) -join '|')
}
function ShowCompatibility {
    $state=$script:compatibility
    if(-not $state){return}
    $install.Text=switch($state.state){'updatePhotoCraft'{'Update PhotoCraft'} 'updateGenGate'{'Update GenGate'} 'getPhotoCraft'{'Get PhotoCraft'} default{if($UpdateEditor){'Update'}else{'Install'}}}
    $install.Width=138;$install.Left=$footer.ClientSize.Width-162;$close.Left=$install.Left-108
    if($state.compatible){SetStatus 'ready' 'PhotoCraft is ready' $state.message;$script:waitingUpdate=$false}
    else{SetStatus 'warning' 'PhotoCraft compatibility' $state.message}
}
function CheckCompatibility {
    if($ValidateOnly -and $env:GENGATE_SETUP_COMPAT_FIXTURE){$script:compatibility=$env:GENGATE_SETUP_COMPAT_FIXTURE | ConvertFrom-Json;ShowCompatibility;return [bool]$script:compatibility.compatible}
    if(-not $photocraftDir.Text.Trim()){return $false}
    $helper=Join-Path (Split-Path $setupInspector -Parent) 'setup_compatibility.py'
    $catalogBundle=$bundle
    if($Preview -and -not (Test-Path -LiteralPath (Join-Path $catalogBundle 'editor-patches.json'))){$catalogBundle=Join-Path $bundle '..\.runtime\release099-patches'}
    try{
        $targetArguments=if($TargetVersion){@('--target',$TargetVersion)}else{@()}
        $output=& $setupPython -X utf8 $helper --host $photocraftDir.Text --bundle $catalogBundle @targetArguments
        $line=$output | Where-Object {$_.StartsWith('GENGATE_SETUP_COMPAT ')} | Select-Object -Last 1
        $script:compatibility=$line.Substring(21) | ConvertFrom-Json
        $script:checkedFingerprint=EditorFingerprint;ShowCompatibility
        return [bool]$script:compatibility.compatible
    }catch{SetStatus 'warning' 'Check the PhotoCraft folder' 'Choose the installed PhotoCraft folder and try again.';return $false}
}
function OpenPhotoCraftUpdater {
    $helper=Join-Path (Split-Path $setupInspector -Parent) 'editor_update.py'
    $path=Join-Path ([IO.Path]::GetTempPath()) ('gengate-updater-'+[guid]::NewGuid().ToString('N')+'.log')
    $script:logFile=$path
    $script:requestFile=[IO.Path]::ChangeExtension($path,'.json');$script:cancelFile=$path+'.cancel'
    $request=SetupRequest
    $request._profile=if($env:PHOTOCRAFT_CONFIG_DIR){$env:PHOTOCRAFT_CONFIG_DIR}elseif((Test-Path -LiteralPath (Join-Path $photocraftDir.Text 'portable.txt')) -or (Test-Path -LiteralPath (Join-Path $photocraftDir.Text 'PhotoCraft.portable'))){Join-Path $photocraftDir.Text 'PhotoCraftData'}else{Join-Path $env:APPDATA 'Photocraft'}
    $request._updateCache=Join-Path $env:LOCALAPPDATA 'GenGate\updates'
    $request._shortcutLocations=@{programs=[Environment]::GetFolderPath('Programs');desktopPath=[Environment]::GetFolderPath('Desktop');locations=@([Environment]::GetFolderPath('Programs'),[Environment]::GetFolderPath('CommonPrograms'),[Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('CommonDesktopDirectory'))}
    if($resume -and $resume._shortcutLocations){$request._shortcutLocations=$resume._shortcutLocations;$request._updateCache=$resume._updateCache}
    $request | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $script:requestFile -Encoding UTF8
    if($resume -and $resume._editorInstallation){$request._editorInstallation=$resume._editorInstallation}
    else{
        $platformHelper=Join-Path (Split-Path (Split-Path $setupInspector -Parent) -Parent) 'packaging\PhotoCraft-Windows.ps1'
        $registration=(& $platformHelper -Data $script:requestFile) | ConvertFrom-Json
        $request._editorInstallation=$registration.installation
    }
    $request | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $script:requestFile -Encoding UTF8
    if([GenGateSetup.Permissions]::RequiresElevation($photocraftDir.Text)){
        if($ResumeRequest){throw 'This PhotoCraft folder is still not writable. Check its permissions before retrying.'}
        $elevatedArgs='-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File '+[GenGateSetup.Runner]::Quote((Join-Path $PSScriptRoot 'Setup.ps1'))+' -BundleRoot '+[GenGateSetup.Runner]::Quote($bundle)+' -EditorFolder '+[GenGateSetup.Runner]::Quote($photocraftDir.Text)+' -ResumeRequest '+[GenGateSetup.Runner]::Quote($script:requestFile)
        if($TargetVersion){$elevatedArgs+=' -TargetVersion '+[GenGateSetup.Runner]::Quote($TargetVersion)};if($UpdateEditor){$elevatedArgs+=' -UpdateEditor'}
        Start-Process -FilePath powershell.exe -ArgumentList $elevatedArgs -Verb RunAs -WindowStyle Hidden
        $form.Close();return
    }
    $arguments='-u -X utf8 '+[GenGateSetup.Runner]::Quote($helper)+' --request '+[GenGateSetup.Runner]::Quote($script:requestFile)+' --bundle '+[GenGateSetup.Runner]::Quote($bundle)+' --cancel-file '+[GenGateSetup.Runner]::Quote($script:cancelFile)
    if($TargetVersion){$arguments+=' --target '+[GenGateSetup.Runner]::Quote($TargetVersion)}
    $script:operation='editorUpdate';$script:result=$null;$script:cancelled=$false;$script:closeAfterUpdater=$false;$script:child=New-Object GenGateSetup.Runner($setupPython,$arguments,$path)
    foreach($control in $script:editControls){$control.Enabled=$false};$install.Enabled=$false;$progress.Value=-1;$close.Text='Cancel'
    SetStatus 'ready' 'Updating PhotoCraft and GenGate' 'Official files are verified before the update. Save and close PhotoCraft when requested.';$timer.Start()
}
function ShowSetupFields {
    if(-not $UpdateEditor){return}
    foreach($row in @($installRow.Row,$modelsRow.Row,$directRow,$comfyNoteRow,$pythonRow.Row,$comfyRow.Row)){$row.Visible=$true}
    $form.MinimumSize=New-Object System.Drawing.Size(760,640)
    $form.ClientSize=New-Object System.Drawing.Size(820,([Math]::Min(870,[System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea.Height-60)))
}
function ValidSetupFields {
    if(-not $photocraftDir.Text.Trim() -or -not $modelsDir.Text.Trim()){ShowSetupFields;SetStatus 'warning' 'Choose the installation folders' 'Select PhotoCraft and a shared models folder. An empty models folder is allowed.';return $false}
    if((-not $direct.Checked -or $comfyDir.Text.Trim()) -and -not $comfyPython.Text.Trim()){ShowSetupFields;SetStatus 'warning' 'Choose ComfyUI Python' 'Select the python.exe used by your ComfyUI.';return $false}
    if((-not $direct.Checked -or $comfyPython.Text.Trim()) -and -not $comfyDir.Text.Trim()){ShowSetupFields;SetStatus 'warning' 'Choose the ComfyUI folder' 'Select the ComfyUI folder containing main.py.';return $false}
    return $true
}
$script:compatTimer=New-Object System.Windows.Forms.Timer;$script:compatTimer.Interval=1500
$script:compatTimer.Add_Tick({
    if($script:child -or $script:finished){return}
    if(-not $script:waitingUpdate){$script:compatTimer.Stop();[void](CheckCompatibility)}
    elseif((EditorFingerprint) -ne $script:checkedFingerprint){[void](CheckCompatibility)}
})
$install.Add_Click({
    if($Preview){return} # Dev-only inspection: never install or launch PhotoCraft.
    if($script:finished){try{& (Join-Path $installDir.Text 'Launch.ps1');$form.Close()}catch{SetStatus 'warning' 'Installed; PhotoCraft could not open' $_.Exception.Message};return}
    if($settingsTimer.Enabled){$settingsTimer.Stop();if($script:settingsSource -eq 'install'){LoadSetupState '' $installDir.Text}else{LoadSetupState $photocraftDir.Text}}
    if(-not (CheckCompatibility)){
        try{
            if($script:compatibility -and $script:compatibility.state -eq 'updatePhotoCraft'){if(ValidSetupFields){OpenPhotoCraftUpdater}}
            elseif($script:compatibility -and $script:compatibility.url){Start-Process -FilePath $script:compatibility.url -WindowStyle Hidden}
        }catch{SetStatus 'warning' 'The update could not open' $_.Exception.GetBaseException().Message}
        return
    }
    if($UpdateEditor){try{if(ValidSetupFields){OpenPhotoCraftUpdater}}catch{SetStatus 'warning' 'The update could not open' $_.Exception.GetBaseException().Message};return}
    if($script:compatibility.running){return}
    if(-not (ValidSetupFields)){return}
    $taskId=[Guid]::NewGuid().ToString('N');$script:requestFile=Join-Path ([IO.Path]::GetTempPath()) ('genstudio-setup-'+$taskId+'.json');$script:logFile=[IO.Path]::ChangeExtension($script:requestFile,'.log')
    $request=SetupRequest
    try {
        $request | ConvertTo-Json | Set-Content -LiteralPath $script:requestFile -Encoding UTF8
        $arguments='-u -X utf8 '+[GenGateSetup.Runner]::Quote((Join-Path $bundle 'runtime\packaging\installer.py'))+' --bundle '+[GenGateSetup.Runner]::Quote($bundle)+' --request '+[GenGateSetup.Runner]::Quote($script:requestFile)
        $script:operation='install';$script:result=$null;$script:cancelled=$false;$script:child=New-Object GenGateSetup.Runner($python,$arguments,$script:logFile)
        foreach($control in $script:editControls){$control.Enabled=$false};$install.Enabled=$false;$close.Text='Cancel';$progress.Value=-1
        SetStatus 'ready' 'Installing GenGate' 'Preparing the local runtime and the verified PhotoCraft patch.';$log.Clear();$timer.Start()
    }catch{SetStatus 'failed' 'Setup could not start' $_.Exception.GetBaseException().Message;foreach($control in $script:editControls){$control.Enabled=$true};$install.Enabled=$true;UpdateFields}
})
function CloseSetup {
    if($script:child -and $script:operation -eq 'editorUpdate' -and -not $script:child.HasExited){[IO.File]::WriteAllText($script:cancelFile,'cancel');$script:closeAfterUpdater=$true;SetStatus 'ready' 'Cancelling safely' 'Finishing or restoring the current update step.';return}
    if($script:child -and -not $script:child.HasExited){$script:cancelled=$true;$script:child.Cancel();return};$form.Close()
}
$close.Add_Click({CloseSetup});$titleClose.Add_Click({CloseSetup});$form.Add_KeyDown({if($_.KeyCode -eq 'Escape'){CloseSetup}})
$form.Add_FormClosing({if($script:child -and -not $script:child.HasExited){$_.Cancel=$true;CloseSetup}})
function SetValidationState {
    switch($ValidationState){'success'{CompleteSetup 0} 'warning'{$script:result=[pscustomobject]@{status='installedWithWarnings'};CompleteSetup 0} 'failed'{CompleteSetup 1} default{SetStatus 'ready' 'Ready to install' 'Close PhotoCraft before installing. Existing settings and models are kept.'}}
}
if($ValidateOnly){
    $form.StartPosition='Manual';$form.Location=New-Object System.Drawing.Point(-30000,-30000);$form.Show();[System.Windows.Forms.Application]::DoEvents()
    if($UpdateEditor -and $resume){
        $request=SetupRequest
        foreach($name in @('photocraftDir','installDir','modelsDir','comfyDir','comfyPython','installDirect')){if($request[$name] -ne $resume.$name){throw ('Update resume lost '+$name)}}
        if($form.ClientSize.Height -gt 600 -or $modelsRow.Row.Visible -or -not $photocraftDir.ReadOnly -or $hostRow.Browse.Enabled){throw 'Update mode did not keep the compact editor-specific view'}
        if($script:child -or $script:requestFile){throw 'Update validation started an installation'}
    }
    if($SelfTest){
        if($env:GENGATE_SETUP_VALIDATION_HOST){
            $photocraftDir.Text='';$photocraftDir.Text=$env:GENGATE_SETUP_VALIDATION_HOST
            $deadline=[DateTime]::UtcNow.AddSeconds(5)
            while($settingsTimer.Enabled){if([DateTime]::UtcNow -gt $deadline){throw 'Automatic settings load timed out'};[System.Windows.Forms.Application]::DoEvents();Start-Sleep -Milliseconds 20}
            $expected=ReadSetupState -stateArguments @('--host',$env:GENGATE_SETUP_VALIDATION_HOST)
            if(-not $expected.found -or $photocraftDir.Text -ne $expected.host -or $installDir.Text -ne $expected.installDir -or $modelsDir.Text -ne $expected.settings.modelsDir -or $comfyDir.Text -ne $expected.settings.comfyDir -or $comfyPython.Text -ne $expected.settings.comfyPython -or $direct.Checked -ne $expected.installDirect){throw 'Existing setup did not populate the form'}
            $request=SetupRequest
            if($request.modelsDir -ne $expected.settings.modelsDir -or $request.comfyDir -ne $expected.settings.comfyDir -or $request.comfyPython -ne $expected.settings.comfyPython){throw 'Existing settings did not reach the setup request'}
        }
        if($Preview){
            $script:loadingSettings=$true
            $saved=@($photocraftDir.Text,$installDir.Text,$modelsDir.Text,$comfyPython.Text)
            $probeRoot=Join-Path ([IO.Path]::GetTempPath()) ('gengate-preview-probe-'+[guid]::NewGuid().ToString('N'))
            $photocraftDir.Text=Join-Path $probeRoot 'PhotoCraft';$installDir.Text=Join-Path $probeRoot 'GenGate'
            $modelsDir.Text=Join-Path $probeRoot 'Models';$comfyPython.Text=Join-Path $probeRoot 'python.exe'
            try{
                $beforeStatus=$status.Text
                $install.PerformClick()
                if($script:child -or $script:requestFile -or $script:logFile -or $script:finished -or $status.Text -ne $beforeStatus){throw 'Preview attempted an installation'}
                $script:finished=$true;$install.Text='Open PhotoCraft'
                $install.PerformClick()
                if(-not $form.Visible -or $status.Text -ne $beforeStatus){throw 'Preview attempted to open PhotoCraft'}
            }finally{
                $photocraftDir.Text=$saved[0];$installDir.Text=$saved[1];$modelsDir.Text=$saved[2];$comfyPython.Text=$saved[3]
                $script:finished=$false;$install.Text='Install'
                $script:loadingSettings=$false;$settingsTimer.Stop()
            }
        }
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class SetupInputProbe {
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h,int m,IntPtr w,IntPtr l);
    public static void Click(IntPtr h){SendMessage(h,0x201,new IntPtr(1),new IntPtr(0x000C000C));SendMessage(h,0x202,IntPtr.Zero,new IntPtr(0x000C000C));}
    public static void Space(IntPtr h){SendMessage(h,0x100,new IntPtr(32),IntPtr.Zero);SendMessage(h,0x101,new IntPtr(32),IntPtr.Zero);}
}
'@
        function CheckSetupChoice([bool]$expected) {
            [System.Windows.Forms.Application]::DoEvents()
            if($direct.Checked -ne $expected -or (SetupRequest).installDirect -ne $expected){throw 'Direct checkbox request does not match the selected mode'}
            if(-not $pythonRow.Row.Visible -or -not $comfyRow.Row.Visible){throw 'ComfyUI fields must remain available in both modes'}
            if(-not $expected -and $comfyNote.Text -notmatch 'required'){throw 'ComfyUI-only warning is missing'}
            $request=SetupRequest
            foreach($removed in @('engine','newRuntime','desktopShortcut')){if($request.ContainsKey($removed)){throw ('Removed option in setup request: '+$removed)}}
        }
        $directBefore=$direct.Checked
        for($i=0;$i -lt 12;$i++){
            $beforeClick=$direct.Checked;[SetupInputProbe]::Click($direct.Handle);CheckSetupChoice (-not $beforeClick)
            $beforeSpace=$direct.Checked;[SetupInputProbe]::Space($direct.Handle);CheckSetupChoice (-not $beforeSpace)
        }
        $direct.Checked=$false;CheckSetupChoice $false
        if($ValidationScreenshot){
            [System.Windows.Forms.Application]::DoEvents()
            $scroll.ScrollTo([int]::MaxValue)
            if($body.Height -gt $scroll.Height -and $scroll.Offset -le 0){throw 'Setup options did not scroll'}
            $scroll.ScrollTo(0)
            $previewBitmap=New-Object System.Drawing.Bitmap($form.Width,$form.Height)
            try{$form.DrawToBitmap($previewBitmap,(New-Object System.Drawing.Rectangle(0,0,$form.Width,$form.Height)));$previewBitmap.Save([IO.Path]::ChangeExtension($ValidationScreenshot,'comfy.png'),[System.Drawing.Imaging.ImageFormat]::Png)}finally{$previewBitmap.Dispose()}
        }
        $direct.Checked=$true;CheckSetupChoice $true
        $direct.Checked=$directBefore
        [SetupInputProbe]::Click($minimize.Handle)
        if($form.WindowState -ne 'Minimized'){throw 'Caption minimize did not minimize the window'}
        $form.WindowState='Normal'
        $before=$photocraftDir.Text
        [GenGateSetup.Picker]::Override=[Func[System.Windows.Forms.IWin32Window,string,bool,string]]{param($owner,$initial,$folder) throw 'Synthetic picker failure'}
        $hostRow.Browse.PerformClick();if($photocraftDir.Text -ne $before -or $status.Text -ne 'The browser could not open'){throw 'Browse failure recovery failed'}
        [GenGateSetup.Picker]::Override=[Func[System.Windows.Forms.IWin32Window,string,bool,string]]{param($owner,$initial,$folder) 'C:\Synthetic folder'}
        $hostRow.Browse.PerformClick();if($photocraftDir.Text -ne 'C:\Synthetic folder'){throw 'Browse selection failed'}
        [GenGateSetup.Picker]::Override=$null
        $testLog=Join-Path ([IO.Path]::GetTempPath()) ('genstudio-setup-validation-'+[guid]::NewGuid().ToString('N')+'.log')
        $script:logFile=$testLog
        try{
            $script:finished=$false;CompleteSetup 0;if(-not $script:finished -or $status.Text -ne 'Installed successfully'){throw 'Successful exit incorrectly reported as failure'}
            if((Get-Content -LiteralPath $testLog -Tail 1) -ne 'Installed successfully'){throw 'Success log has no terminal result'}
            $script:finished=$false;CompleteSetup 1;if($status.Text -ne 'Installation did not complete'){throw 'Failed exit incorrectly reported as success'}
            if((Get-Content -LiteralPath $testLog -Tail 1) -ne 'Installation did not complete'){throw 'Failure log has no terminal result'}
            $script:result=[pscustomobject]@{status='needsPhotoCraft';compatibility=[pscustomobject]@{state='updatePhotoCraft';compatible=$false;message='Update PhotoCraft first.'}}
            CompleteSetup 2
            if($script:finished -or $install.Text -ne 'Update PhotoCraft' -or $status.Text -eq 'Installation did not complete'){throw 'Compatibility prerequisite was treated as an installation failure'}
            $script:result=$null;$script:compatibility=$null
        }finally{Remove-Item -LiteralPath $testLog -Force -ErrorAction SilentlyContinue;$script:logFile=$null}
        $photocraftDir.Text=$before
        $script:finished=$false;$install.Text='Install';$log.Text='Ready. Your existing PhotoCraft settings and model files are kept.'
        if($env:GENGATE_SETUP_COMPAT_FIXTURE){
            $savedPaths=@($photocraftDir.Text,$installDir.Text,$modelsDir.Text,$comfyDir.Text,$comfyPython.Text)
            $script:updaterProbeCount=0;$script:openedUrl=''
            function OpenPhotoCraftUpdater {$script:updaterProbeCount++}
            function ValidSetupFields {return $true}
            function Start-Process {param($FilePath,$WindowStyle) $script:openedUrl=$FilePath}
            [void](CheckCompatibility)
            if(-not $script:compatibility.compatible -and -not $Preview){
                $install.PerformClick()
                if($script:compatibility.state -eq 'updatePhotoCraft' -and $script:updaterProbeCount -ne 1){throw 'Update action did not open the external updater'}
                if($script:compatibility.url -and $script:openedUrl -ne $script:compatibility.url){throw 'Compatibility action opened the wrong download'}
                if($script:child -or $script:requestFile){throw 'Incompatible editor started the installer'}
            }
            $afterPaths=@($photocraftDir.Text,$installDir.Text,$modelsDir.Text,$comfyDir.Text,$comfyPython.Text)
            if(($savedPaths -join '|') -ne ($afterPaths -join '|')){throw 'Compatibility handoff lost setup settings'}
            $script:child=[pscustomobject]@{HasExited=$false}
            $script:child | Add-Member -MemberType ScriptMethod -Name Cancel -Value {throw 'Updater handoff must never kill PhotoCraft'}
            $script:cancelFile=Join-Path ([IO.Path]::GetTempPath()) ('gengate-cancel-test-'+[guid]::NewGuid().ToString('N'))
            $script:operation='editorUpdate';CloseSetup
            if(-not $script:closeAfterUpdater){throw 'Closing during updater handoff was not deferred'}
            if(-not (Test-Path -LiteralPath $script:cancelFile)){throw 'Safe cancellation was not requested'}
            Remove-Item -LiteralPath $script:cancelFile -Force;$script:cancelFile=$null
            $script:child=$null;$script:operation='install';$script:closeAfterUpdater=$false
        }
    }
    if($SelfTest -and $env:GENGATE_SETUP_VALIDATION_HOST){LoadSetupState $env:GENGATE_SETUP_VALIDATION_HOST}
    SetValidationState
    if($env:GENGATE_SETUP_COMPAT_FIXTURE){[void](CheckCompatibility)}
    if($ValidationScale -ne 1){
        $fonts=New-Object 'System.Collections.Generic.Dictionary[System.Windows.Forms.Control,System.Drawing.Font]'
        function RememberFonts($parent){$fonts[$parent]=$parent.Font;foreach($c in $parent.Controls){RememberFonts $c}}
        RememberFonts $form;$form.Scale((New-Object System.Drawing.SizeF($ValidationScale,$ValidationScale)))
        foreach($c in $fonts.Keys){$f=$fonts[$c];$c.Font=New-Object System.Drawing.Font($f.FontFamily,($f.Size*$ValidationScale),$f.Style)}
    }
    if($ValidationScreenshot){
        [System.Windows.Forms.Application]::DoEvents()
        $bitmap=New-Object System.Drawing.Bitmap($form.Width,$form.Height);$form.DrawToBitmap($bitmap,(New-Object System.Drawing.Rectangle(0,0,$form.Width,$form.Height)));$bitmap.Save($ValidationScreenshot,[System.Drawing.Imaging.ImageFormat]::Png);$bitmap.Dispose();$form.Hide()
    }
    if($SelfTest){$form.Show();[SetupInputProbe]::Click($titleClose.Handle);if(-not $form.IsDisposed){throw 'Caption close did not close the window'}}
    $form.Dispose();$timer.Dispose();$settingsTimer.Dispose();$script:compatTimer.Dispose();Write-Output 'Setup form validated';exit 0
}
if($Preview){SetValidationState}
if($ResumeRequest -and -not ($ValidateOnly -or $Preview)){$form.Add_Shown({$install.PerformClick()})}
if(-not $ValidateOnly){$script:compatTimer.Start()}
try{[void]$form.ShowDialog()}finally{$timer.Dispose();$settingsTimer.Dispose();$script:compatTimer.Dispose();$form.Dispose();if($script:child){$script:child.Dispose()}}
