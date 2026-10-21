param([Parameter(Mandatory=$true)][string]$Data)
$ErrorActionPreference = 'Stop'
$v = Get-Content -LiteralPath $Data -Raw -Encoding UTF8 | ConvertFrom-Json
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($v.path)
$s.TargetPath = $v.target
$s.Arguments = $v.arguments
$s.WorkingDirectory = $v.working
$s.IconLocation = $v.icon + ',0'
$s.Description = 'PhotoCraft with GenGate'
$s.Save()
