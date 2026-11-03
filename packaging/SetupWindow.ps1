param([switch]$ValidateOnly, [string]$ValidationScreenshot = '', [ValidateSet('ready','success','warning','failed')][string]$ValidationState = 'ready', [double]$ValidationScale = 1, [switch]$SelfTest)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -Path (Join-Path $PSScriptRoot 'SetupUi.cs') -ReferencedAssemblies System.Windows.Forms,System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$bundle = $PSScriptRoot
$python = Join-Path $bundle 'runtime\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python) -and -not $ValidateOnly) {
    [System.Windows.Forms.MessageBox]::Show('Extract the complete GenGate add-on ZIP, then run Setup.cmd. Install PhotoCraft separately first.', 'GenGate') | Out-Null
    exit 1
}
$form = New-Object GenGateSetup.ChromeForm
$form.Preview = [bool]$ValidateOnly
$form.Text = 'GenGate 0.9.7 - Setup'
$form.Font = New-Object System.Drawing.Font('Segoe UI',9.5)
$form.ClientSize = New-Object System.Drawing.Size(820,([Math]::Min(870,[System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea.Height-60)))
$form.MinimumSize = New-Object System.Drawing.Size(760,640)
$form.StartPosition = 'CenterScreen'
$form.KeyPreview = $true
$script:child = $null; $script:finished = $false; $script:cancelled = $false
$script:requestFile = $null; $script:logFile = $null; $script:result = $null
$script:editControls = New-Object System.Collections.Generic.List[System.Windows.Forms.Control]
function Label([string]$text,[int]$x,[int]$y,[int]$width,[int]$height=24) {
    $c=New-Object System.Windows.Forms.Label; $c.Text=$text; $c.SetBounds($x,$y,$width,$height); $c.ForeColor=[GenGateSetup.Palette]::Text; return $c
}
function Button([string]$text,[int]$x,[int]$y,[int]$width=94) {
    $c=New-Object GenGateSetup.FlatButton; $c.Text=$text; $c.SetBounds($x,$y,$width,34); return $c
}
$header=New-Object System.Windows.Forms.Panel; $header.Width=$form.ClientSize.Width; $header.Dock='Top'; $header.Height=58; $header.BackColor=[GenGateSetup.Palette]::Surface
$brand=Label 'GG' 23 15 35 29; $brand.BackColor=[GenGateSetup.Palette]::Accent; $brand.ForeColor=[GenGateSetup.Palette]::Background; $brand.TextAlign='MiddleCenter'
$title=Label 'GenGate' 70 12 180 24; $title.Font=New-Object System.Drawing.Font('Segoe UI',12,[System.Drawing.FontStyle]::Bold)
$version=Label 'ADD-ON SETUP  /  0.9.7' 70 36 250 18; $version.Font=New-Object System.Drawing.Font('Segoe UI',8); $version.ForeColor=[GenGateSetup.Palette]::Muted
$minimize=Button '_' 724 12 32; $minimize.Anchor='Top,Right'; $minimize.Add_Click({$form.WindowState='Minimized'})
$titleClose=Button 'x' 764 12 32; $titleClose.Anchor='Top,Right'
$header.Controls.AddRange(@($brand,$title,$version,$minimize,$titleClose)); $header.Add_MouseDown({if($_.Button -eq 'Left'){$form.DragWindow()}}); $title.Add_MouseDown({if($_.Button -eq 'Left'){$form.DragWindow()}})
$footer=New-Object System.Windows.Forms.Panel; $footer.Width=$form.ClientSize.Width; $footer.Dock='Bottom'; $footer.Height=96
$progress=New-Object GenGateSetup.Progress; $progress.Dock='Top'; $progress.Value=0
$status=Label 'Ready to install' 24 13 520 25
$detail=Label 'Close PhotoCraft before installing. Existing settings and models are kept.' 24 42 470 40; $detail.ForeColor=[GenGateSetup.Palette]::Muted; $detail.Font=New-Object System.Drawing.Font('Segoe UI',8.5)
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
$intro.Controls.Add($lead); $hint=Label 'PhotoCraft 0.20.1  |  Windows x64  |  No editor or model weights bundled' 0 29 730 22; $hint.ForeColor=[GenGateSetup.Palette]::Muted; $hint.Font=New-Object System.Drawing.Font('Segoe UI',8.5); $intro.Controls.Add($hint)
$hostCandidates=@((Join-Path $env:LOCALAPPDATA 'Programs\PhotoCraft'),(Join-Path $env:ProgramFiles 'PhotoCraft'))
foreach($drive in (Get-PSDrive -PSProvider FileSystem)){$hostCandidates+=Join-Path $drive.Root 'Programs\PhotoCraft'}
$hostDefault='';foreach($candidate in $hostCandidates){if((Test-Path -LiteralPath (Join-Path $candidate 'photocraft.exe')) -and (Test-Path -LiteralPath (Join-Path $candidate 'photocraft-cli.exe'))){$hostDefault=$candidate;break}}
$hostRow=FieldRow 'PhotoCraft folder' $hostDefault; $photocraftDir=$hostRow.Field; $hostRow.Browse.Add_Click({[void](Browse $photocraftDir)})
$installRow=FieldRow 'GenGate install folder' (Join-Path $env:LOCALAPPDATA 'Programs\GenGate'); $installDir=$installRow.Field; $installRow.Browse.Add_Click({[void](Browse $installDir)})
$modelsRow=FieldRow 'Shared models folder' ''; $modelsDir=$modelsRow.Field
$engineRow=AddRow '' 73
$engineRow.Controls.Add((Label 'Engine' 0 0 330 22));$engineRow.Controls.Add((Label 'Direct runtime' 380 0 330 22))
$engine=New-Object GenGateSetup.FlatCombo; $engine.SetBounds(0,28,355,34); [void]$engine.Items.Add('Direct');[void]$engine.Items.Add('ComfyUI');$engine.SelectedIndex=0
$runtime=New-Object GenGateSetup.FlatCombo; $runtime.SetBounds(380,28,368,34);$runtime.Anchor='Top,Right';[void]$runtime.Items.Add('Install a separate CUDA runtime');[void]$runtime.Items.Add('Use existing CUDA Python');$runtime.SelectedIndex=0
$engineRow.Controls.AddRange(@($engine,$runtime));$script:editControls.Add($engine);$script:editControls.Add($runtime)
$engine.AccessibleName='Engine';$runtime.AccessibleName='Direct runtime'
$noteRow=AddRow '' 42; $runtimeNote=Label '' 0 0 730 40; $runtimeNote.ForeColor=[GenGateSetup.Palette]::Muted;$runtimeNote.Font=New-Object System.Drawing.Font('Segoe UI',8.5);$noteRow.Controls.Add($runtimeNote)
$pythonRow=FieldRow 'Engine Python (python.exe)';$enginePython=$pythonRow.Field;$pythonRow.Browse.Add_Click({[void](Browse $enginePython $false)})
$comfyRow=FieldRow 'ComfyUI folder (contains main.py)';$comfyDir=$comfyRow.Field;$comfyRow.Browse.Add_Click({[void](Browse $comfyDir)})
$optionRow=AddRow '' 57
$lanpaint=New-Object GenGateSetup.AccentCheck;$lanpaint.Text='Install missing LanPaint in this ComfyUI';$lanpaint.Checked=$true;$lanpaint.SetBounds(0,0,730,25)
$desktop=New-Object GenGateSetup.AccentCheck;$desktop.Text='Create a desktop shortcut';$desktop.Checked=$true;$desktop.SetBounds(0,29,730,25)
$optionRow.Controls.AddRange(@($lanpaint,$desktop));$script:editControls.Add($lanpaint);$script:editControls.Add($desktop)
$logRow=AddRow 'Installation details' 131
$log=New-Object System.Windows.Forms.TextBox;$log.SetBounds(0,28,$logRow.ClientSize.Width,100);$log.Anchor='Top,Left,Right';$log.Multiline=$true;$log.ReadOnly=$true;$log.BorderStyle='None';$log.ScrollBars='None';$log.BackColor=[GenGateSetup.Palette]::Surface;$log.ForeColor=[GenGateSetup.Palette]::Muted;$log.Font=New-Object System.Drawing.Font('Consolas',8.5);$log.Text='Ready. Your existing PhotoCraft settings and model files are kept.';$logRow.Controls.Add($log)
function UpdateFields {
    $comfy=$engine.SelectedIndex -eq 1; $reuse=$comfy -or $runtime.SelectedIndex -eq 1
    $runtime.Enabled=(-not $comfy) -and (-not $script:child);$pythonRow.Row.Visible=$reuse;$comfyRow.Row.Visible=$comfy;$lanpaint.Visible=$comfy
    if($comfy){$desktop.Top=29;$optionRow.Height=57}else{$desktop.Top=0;$optionRow.Height=28}
    if($comfy){$runtimeNote.Text='Uses your ComfyUI and shared model files. Studio runs the active browser graph.'}
    elseif($reuse){$runtimeNote.Text='Uses installed dependencies in place. Existing Python packages are not changed.'}
    else{$runtimeNote.Text='Downloads a separate CUDA runtime (several GB). NVIDIA CUDA 13 driver required. Model files are separate.'}
}
$engine.Add_SelectedIndexChanged({UpdateFields});$runtime.Add_SelectedIndexChanged({UpdateFields})
$modelsRow.Browse.Add_Click({
    if(Browse $modelsDir){
        $candidate=Split-Path $modelsDir.Text -Parent
        if($candidate -and (Test-Path -LiteralPath (Join-Path $candidate 'main.py'))){
            $comfyDir.Text=$candidate
            foreach($path in @((Join-Path (Split-Path $candidate -Parent) 'python_embeded\python.exe'),(Join-Path $candidate '.venv\Scripts\python.exe'))){if(Test-Path -LiteralPath $path){$enginePython.Text=$path;$runtime.SelectedIndex=1;break}}
        }
    }
})
if($hostDefault){
    try {
        $config=Get-Content -LiteralPath (Join-Path $hostDefault 'runtime\config.photocraft.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($config.modelsDir){$modelsDir.Text=$config.modelsDir};if($config.comfyDir){$comfyDir.Text=$config.comfyDir}
        if($config.engine -eq 'comfy'){$engine.SelectedIndex=1;$enginePython.Text=$config.comfyPython}
        elseif($config.directPython){$enginePython.Text=$config.directPython;$runtime.SelectedIndex=1}
        $state=Get-Content -LiteralPath (Join-Path $hostDefault '.gengate\state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if($state.package){$installDir.Text=Split-Path (Split-Path $state.package -Parent) -Parent}
    } catch { } # First installs have no previous configuration.
}
UpdateFields
function DrainOutput {
    foreach($line in $script:child.Drain()){
        if($line.StartsWith('GENGATE_SETUP_RESULT ')){try{$script:result=$line.Substring(21) | ConvertFrom-Json}catch{}}
        else{$log.AppendText([Environment]::NewLine+$line)}
    }
    if($log.TextLength -gt 18000){$log.Text=$log.Text.Substring($log.TextLength-16000)};$log.SelectionStart=$log.TextLength;$log.ScrollToCaret()
}
function CompleteSetup([int]$code) {
    $script:finished=$false; $install.Text='Install'
    foreach($control in $script:editControls){$control.Enabled=$true};UpdateFields
    $close.Text='Close';$install.Enabled=$true
    if($script:cancelled){SetStatus 'warning' 'Installation cancelled' 'Run Setup again to finish or repair the installation.';$progress.Value=0}
    elseif($code -eq 0){
        $script:finished=$true;$install.Text='Open PhotoCraft';$progress.Value=100
        if($script:result -and $script:result.status -eq 'installedWithWarnings'){SetStatus 'warning' 'Installed with notes' 'GenGate is installed. Review the installation details for optional setup items.'}
        else{SetStatus 'success' 'Installed successfully' 'GenGate is ready. Open PhotoCraft to continue.'}
        $log.AppendText([Environment]::NewLine+$status.Text)
    }else{SetStatus 'failed' 'Installation did not complete' ('Correct the setting and retry. Log: '+$script:logFile);$progress.Value=0;$log.AppendText([Environment]::NewLine+'Setup failed (exit '+$code+').')}
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
        if($script:requestFile){Remove-Item -LiteralPath $script:requestFile -Force -ErrorAction SilentlyContinue}
    }
})
$install.Add_Click({
    if($script:finished){try{& (Join-Path $installDir.Text 'Launch.ps1');$form.Close()}catch{SetStatus 'warning' 'Installed; PhotoCraft could not open' $_.Exception.Message};return}
    if(-not $photocraftDir.Text.Trim() -or -not $modelsDir.Text.Trim()){SetStatus 'warning' 'Choose the installation folders' 'Select PhotoCraft and a shared models folder. An empty models folder is allowed.';return}
    if(($engine.SelectedIndex -eq 1 -or $runtime.SelectedIndex -eq 1) -and -not $enginePython.Text.Trim()){SetStatus 'warning' 'Choose engine Python' 'Select an existing python.exe, or install a separate CUDA runtime.';return}
    $taskId=[Guid]::NewGuid().ToString('N');$script:requestFile=Join-Path ([IO.Path]::GetTempPath()) ('genstudio-setup-'+$taskId+'.json');$script:logFile=[IO.Path]::ChangeExtension($script:requestFile,'.log')
    $request=@{installDir=$installDir.Text;photocraftDir=$photocraftDir.Text;modelsDir=$modelsDir.Text;engine=@('direct','comfy')[$engine.SelectedIndex];python=$enginePython.Text;comfyDir=$comfyDir.Text;newRuntime=($runtime.SelectedIndex -eq 0);installLanPaint=$lanpaint.Checked;desktopShortcut=$desktop.Checked}
    try {
        $request | ConvertTo-Json | Set-Content -LiteralPath $script:requestFile -Encoding UTF8
        $arguments='-u -X utf8 '+[GenGateSetup.Runner]::Quote((Join-Path $bundle 'runtime\packaging\installer.py'))+' --bundle '+[GenGateSetup.Runner]::Quote($bundle)+' --request '+[GenGateSetup.Runner]::Quote($script:requestFile)
        $script:result=$null;$script:cancelled=$false;$script:child=New-Object GenGateSetup.Runner($python,$arguments,$script:logFile)
        foreach($control in $script:editControls){$control.Enabled=$false};$install.Enabled=$false;$close.Text='Cancel';$progress.Value=-1
        SetStatus 'ready' 'Installing GenGate' 'Preparing the local runtime and the verified PhotoCraft patch.';$log.Clear();$timer.Start()
    }catch{SetStatus 'failed' 'Setup could not start' $_.Exception.GetBaseException().Message;foreach($control in $script:editControls){$control.Enabled=$true};$install.Enabled=$true;UpdateFields}
})
function CloseSetup {
    if($script:child -and -not $script:child.HasExited){$script:cancelled=$true;$script:child.Cancel();return};$form.Close()
}
$close.Add_Click({CloseSetup});$titleClose.Add_Click({CloseSetup});$form.Add_KeyDown({if($_.KeyCode -eq 'Escape'){CloseSetup}})
$form.Add_FormClosing({if($script:child -and -not $script:child.HasExited){$_.Cancel=$true;CloseSetup}})
if($ValidateOnly){
    $form.StartPosition='Manual';$form.Location=New-Object System.Drawing.Point(-30000,-30000);$form.Show();[System.Windows.Forms.Application]::DoEvents()
    if($SelfTest){
        $engineBefore=$engine.SelectedIndex;$runtimeBefore=$runtime.SelectedIndex
        $engine.SelectedIndex=1
        if(-not $pythonRow.Row.Visible -or -not $comfyRow.Row.Visible -or -not $lanpaint.Visible -or $runtime.Enabled){throw 'ComfyUI fields are inconsistent'}
        if($ValidationScreenshot){
            [System.Windows.Forms.Application]::DoEvents()
            $scroll.ScrollTo([int]::MaxValue)
            if($body.Height -gt $scroll.Height -and $scroll.Offset -le 0){throw 'Setup options did not scroll'}
            $scroll.ScrollTo(0)
            $preview=New-Object System.Drawing.Bitmap($form.Width,$form.Height)
            try{$form.DrawToBitmap($preview,(New-Object System.Drawing.Rectangle(0,0,$form.Width,$form.Height)));$preview.Save([IO.Path]::ChangeExtension($ValidationScreenshot,'comfy.png'),[System.Drawing.Imaging.ImageFormat]::Png)}finally{$preview.Dispose()}
        }
        $engine.SelectedIndex=0;$runtime.SelectedIndex=0
        if($pythonRow.Row.Visible -or $comfyRow.Row.Visible -or $lanpaint.Visible -or -not $runtime.Enabled){throw 'Separate Direct runtime fields are inconsistent'}
        $runtime.SelectedIndex=1
        if(-not $pythonRow.Row.Visible -or $comfyRow.Row.Visible){throw 'Existing Direct runtime fields are inconsistent'}
        $engine.SelectedIndex=$engineBefore;$runtime.SelectedIndex=$runtimeBefore
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
        }finally{Remove-Item -LiteralPath $testLog -Force -ErrorAction SilentlyContinue;$script:logFile=$null}
        $photocraftDir.Text=$before
        $script:finished=$false;$install.Text='Install';$log.Text='Ready. Your existing PhotoCraft settings and model files are kept.'
    }
    switch($ValidationState){'success'{CompleteSetup 0} 'warning'{$script:result=[pscustomobject]@{status='installedWithWarnings'};CompleteSetup 0} 'failed'{CompleteSetup 1} default{SetStatus 'ready' 'Ready to install' 'Close PhotoCraft before installing. Existing settings and models are kept.'}}
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
    $form.Dispose();$timer.Dispose();Write-Output 'Setup form validated';exit 0
}
try{[void]$form.ShowDialog()}finally{$timer.Dispose();$form.Dispose();if($script:child){$script:child.Dispose()}}
