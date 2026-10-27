param([switch]$ValidateOnly, [string]$ValidationScreenshot = '')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$bundle = $PSScriptRoot
$python = Join-Path $bundle 'runtime\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python) -and -not $ValidateOnly) {
    [System.Windows.Forms.MessageBox]::Show('Extract the complete GenGate add-on ZIP, then run Setup.cmd. Install PhotoCraft separately first.', 'GenGate') | Out-Null
    exit 1
}
if ($ValidateOnly) {
    Add-Type -ReferencedAssemblies System.Windows.Forms -TypeDefinition 'public class GenStudioSetupPreviewForm : System.Windows.Forms.Form { protected override bool ShowWithoutActivation { get { return true; } } }'
    $form = New-Object GenStudioSetupPreviewForm
} else { $form = New-Object System.Windows.Forms.Form }
$form.Text = 'GenGate 0.9.4 - Add-on Setup'
$form.ClientSize = New-Object System.Drawing.Size(700, 655)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$form.BackColor = [System.Drawing.Color]::FromArgb(39,39,39)
$form.ForeColor = [System.Drawing.Color]::Gainsboro

function Label([string]$text, [int]$x, [int]$y, [int]$width = 655) {
    $c = New-Object System.Windows.Forms.Label
    $c.Text = $text; $c.SetBounds($x,$y,$width,25); $form.Controls.Add($c)
    return $c
}
function Field([int]$y, [string]$initial = '') {
    $c = New-Object System.Windows.Forms.TextBox
    $c.SetBounds(22,$y,568,28); $c.Text = $initial
    $c.BackColor = [System.Drawing.Color]::FromArgb(55,55,55); $c.ForeColor = [System.Drawing.Color]::White
    $form.Controls.Add($c); return $c
}
function Button([string]$text, [int]$x, [int]$y, [int]$width = 82) {
    $c = New-Object System.Windows.Forms.Button
    $c.Text = $text; $c.SetBounds($x,$y,$width,29); $c.FlatStyle = 'Flat'
    $form.Controls.Add($c); return $c
}
function BrowseFolder($field) {
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Choose folder'
    if (Test-Path -LiteralPath $field.Text) { $dialog.SelectedPath = $field.Text }
    if ($dialog.ShowDialog() -eq 'OK') { $field.Text = $dialog.SelectedPath }
    $dialog.Dispose()
}
[void](Label 'Local AI tools inside PhotoCraft' 22 16)
[void](Label 'For an existing PhotoCraft. No bundled editor or model weights.' 22 43)
[void](Label 'Install folder' 22 78)
$installDir = Field 104 (Join-Path $env:LOCALAPPDATA 'Programs\GenGate')
$browseInstall = Button 'Browse...' 600 103
$browseInstall.Add_Click({ BrowseFolder $installDir })
[void](Label 'Models folder (ComfyUI\models or a separate folder)' 22 143)
$modelsDir = Field 169
$browseModels = Button 'Browse...' 600 168
$browseModels.Add_Click({
    BrowseFolder $modelsDir
    $candidate = Split-Path $modelsDir.Text -Parent
    if (Test-Path -LiteralPath (Join-Path $candidate 'main.py')) {
        $comfyDir.Text = $candidate
        $embedded = Join-Path (Split-Path $candidate -Parent) 'python_embeded\python.exe'
        $venv = Join-Path $candidate '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $embedded) { $enginePython.Text = $embedded; $runtime.SelectedIndex = 1 }
        elseif (Test-Path -LiteralPath $venv) { $enginePython.Text = $venv; $runtime.SelectedIndex = 1 }
    }
})
[void](Label 'Engine' 22 208 280)
[void](Label 'Direct runtime' 350 208 325)
$engine = New-Object System.Windows.Forms.ComboBox
$engine.SetBounds(22,234,305,28); $engine.DropDownStyle = 'DropDownList'
[void]$engine.Items.Add('Direct (no ComfyUI server)'); [void]$engine.Items.Add('ComfyUI')
$engine.SelectedIndex = 0; $form.Controls.Add($engine)
$runtime = New-Object System.Windows.Forms.ComboBox
$runtime.SetBounds(350,234,330,28); $runtime.DropDownStyle = 'DropDownList'
[void]$runtime.Items.Add('Install a separate CUDA runtime'); [void]$runtime.Items.Add('Use an existing CUDA Python')
$runtime.SelectedIndex = 0; $form.Controls.Add($runtime)
$runtimeNote = Label 'A separate runtime downloads several GB of software. Models are always separate.' 22 271
$pythonLabel = Label 'Engine Python (python.exe)' 22 303
$enginePython = Field 329
$browsePython = Button 'Browse...' 600 328
$browsePython.Add_Click({
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Filter = 'Python interpreter (python.exe)|python.exe'
    if ($dialog.ShowDialog() -eq 'OK') { $enginePython.Text = $dialog.FileName }
    $dialog.Dispose()
})
$comfyLabel = Label 'ComfyUI folder (contains main.py)' 22 367
$comfyDir = Field 393
$browseComfy = Button 'Browse...' 600 392
$browseComfy.Add_Click({ BrowseFolder $comfyDir })
$lanpaint = New-Object System.Windows.Forms.CheckBox
$lanpaint.Text = 'Install missing LanPaint into this ComfyUI (existing files are kept)'
$lanpaint.SetBounds(22,430,655,25); $lanpaint.Checked = $true; $form.Controls.Add($lanpaint)
$desktop = New-Object System.Windows.Forms.CheckBox
$desktop.Text = 'Create desktop shortcut'; $desktop.Checked = $true
$desktop.SetBounds(22,462,655,25); $form.Controls.Add($desktop)
$log = New-Object System.Windows.Forms.TextBox
$log.SetBounds(22,493,658,97); $log.Multiline = $true; $log.ReadOnly = $true; $log.ScrollBars = 'Vertical'
$log.BackColor = [System.Drawing.Color]::FromArgb(28,28,28); $log.ForeColor = [System.Drawing.Color]::Gainsboro
$log.Text = 'Save and close the selected PhotoCraft before installing. Originals are kept for restore.'
$form.Controls.Add($log)
$progress = New-Object System.Windows.Forms.ProgressBar
$progress.SetBounds(22,607,398,8); $form.Controls.Add($progress)
$close = Button 'Close' 453 598 105
$close.Add_Click({
    if ($script:child -and -not $script:child.HasExited) {
        # Terminate only the installer process we own and its pip children.
        & "$env:SystemRoot\System32\taskkill.exe" /PID $script:child.Id /T /F | Out-Null
        return
    }
    $form.Close()
})
$install = Button 'Install' 575 598 105
$script:child = $null; $script:finished = $false
$script:requestFile = $null; $script:logFile = $null; $script:errFile = $null
function UpdateFields {
    $comfy = $engine.SelectedIndex -eq 1
    $runtime.Enabled = -not $comfy
    $reuse = $comfy -or $runtime.SelectedIndex -eq 1
    $enginePython.Enabled = $reuse; $browsePython.Enabled = $reuse
    $comfyDir.Enabled = $comfy; $browseComfy.Enabled = $comfy; $lanpaint.Enabled = $comfy
    if ($comfy) { $runtimeNote.Text = 'Uses your ComfyUI Python and shared models. FLUX 3 needs ComfyUI 0.41.3+; Qwen inpainting uses LanPaint.' }
    elseif ($reuse) { $runtimeNote.Text = 'Uses installed dependencies in place; does not change your existing Python.' }
    else { $runtimeNote.Text = 'A separate runtime downloads several GB of software. NVIDIA CUDA 13 driver required.' }
}
$engine.Add_SelectedIndexChanged({ UpdateFields }); $runtime.Add_SelectedIndexChanged({ UpdateFields })
UpdateFields
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 300
$timer.Add_Tick({
    if ($script:logFile -and (Test-Path -LiteralPath $script:logFile)) {
        $lines = Get-Content -LiteralPath $script:logFile -Tail 65 -Encoding UTF8 -ErrorAction SilentlyContinue
        $log.Text = $lines -join [Environment]::NewLine; $log.SelectionStart = $log.Text.Length; $log.ScrollToCaret()
    }
    if ($script:child -and $script:child.HasExited) {
        $timer.Stop(); $script:child.Refresh()
        $progress.Style = 'Blocks'; $close.Enabled = $true; $close.Text = 'Close'
        $install.Enabled = $true
        if ($script:child.ExitCode -eq 0) {
            $script:finished = $true; $progress.Value = 100; $install.Text = 'Open'
        } else {
            foreach ($c in $form.Controls) { $c.Enabled = $true }; UpdateFields
            $log.AppendText([Environment]::NewLine + 'Setup failed. Correct the setting above and retry. Log: ' + $script:logFile)
            if (Test-Path -LiteralPath $script:errFile) {
                $log.AppendText([Environment]::NewLine + (Get-Content -LiteralPath $script:errFile -Raw -Encoding UTF8))
            }
        }
        Remove-Item -LiteralPath $script:requestFile -Force -ErrorAction SilentlyContinue
        $script:child.Dispose(); $script:child = $null
    }
})
$install.Add_Click({
    if ($script:finished) {
        & (Join-Path $installDir.Text 'Launch.ps1'); $form.Close(); return
    }
    if (-not $modelsDir.Text.Trim()) { $log.Text = 'Choose a Models folder. You may select an empty folder and add models later.'; return }
    if (-not $photocraftDir.Text.Trim()) { $log.Text = 'Choose the existing PhotoCraft installation folder.'; return }
    if (($engine.SelectedIndex -eq 1 -or $runtime.SelectedIndex -eq 1) -and -not $enginePython.Text.Trim()) {
        $log.Text = 'Choose the engine Python interpreter.'; return
    }
    $taskId = [Guid]::NewGuid().ToString('N')
    $script:requestFile = Join-Path ([IO.Path]::GetTempPath()) ('genstudio-setup-' + $taskId + '.json')
    $script:logFile = [IO.Path]::ChangeExtension($script:requestFile, '.log')
    $script:errFile = [IO.Path]::ChangeExtension($script:requestFile, '.errors.log')
    $request = @{ installDir=$installDir.Text; photocraftDir=$photocraftDir.Text; modelsDir=$modelsDir.Text; engine=@('direct','comfy')[$engine.SelectedIndex];
        python=$enginePython.Text; comfyDir=$comfyDir.Text; newRuntime=($runtime.SelectedIndex -eq 0);
        installLanPaint=$lanpaint.Checked; desktopShortcut=$desktop.Checked }
    $request | ConvertTo-Json | Set-Content -LiteralPath $script:requestFile -Encoding UTF8
    try {
        $installerPath = Join-Path $bundle 'runtime\packaging\installer.py'
        $arguments = '-u -X utf8 "' + $installerPath + '" --bundle "' + $bundle + '" --request "' + $script:requestFile + '"'
        $script:child = Start-Process -FilePath $python -ArgumentList $arguments -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $script:logFile -RedirectStandardError $script:errFile
        foreach ($c in $form.Controls) { $c.Enabled = $false }
        $log.Enabled = $true; $close.Enabled = $true; $close.Text = 'Cancel'
        $progress.Enabled = $true; $progress.Style = 'Marquee'; $timer.Start()
    } catch { $log.Text = $_.Exception.Message }
})
$form.Add_FormClosing({
    if ($script:child -and -not $script:child.HasExited) { $_.Cancel = $true }
})
# Add the host row above the existing settings without changing their spacing.
foreach ($control in $form.Controls) { if ($control.Top -ge 78) { $control.Top += 64 } }
$form.ClientSize = New-Object System.Drawing.Size(700, 719)
[void](Label 'Existing PhotoCraft folder' 22 78)
$hostCandidates = @((Join-Path $env:LOCALAPPDATA 'Programs\PhotoCraft'), (Join-Path $env:ProgramFiles 'PhotoCraft'))
foreach ($drive in (Get-PSDrive -PSProvider FileSystem)) { $hostCandidates += Join-Path $drive.Root 'Programs\PhotoCraft' }
$hostDefault = ''
foreach ($candidate in $hostCandidates) {
    if ((Test-Path -LiteralPath (Join-Path $candidate 'photocraft.exe')) -and (Test-Path -LiteralPath (Join-Path $candidate 'photocraft-cli.exe'))) { $hostDefault = $candidate; break }
}
$photocraftDir = Field 104 $hostDefault
$browseHost = Button 'Browse...' 600 103
$browseHost.Add_Click({ BrowseFolder $photocraftDir })
if ($ValidateOnly) {
    if ($ValidationScreenshot) {
        $form.StartPosition = 'Manual'; $form.Location = New-Object System.Drawing.Point(-30000,-30000)
        $form.Show(); [System.Windows.Forms.Application]::DoEvents()
        $bitmap = New-Object System.Drawing.Bitmap($form.Width,$form.Height)
        $form.DrawToBitmap($bitmap, (New-Object System.Drawing.Rectangle(0,0,$form.Width,$form.Height)))
        $bitmap.Save($ValidationScreenshot, [System.Drawing.Imaging.ImageFormat]::Png)
        $bitmap.Dispose()
        $form.Hide()
    }
    $form.Dispose(); $timer.Dispose(); Write-Output 'Setup form validated'; exit 0
}
[void]$form.ShowDialog()
$timer.Dispose(); $form.Dispose()
