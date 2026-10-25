param([Parameter(Mandatory=$true)][string]$Data)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Shell-Link.ps1')
$v = Get-Content -LiteralPath $Data -Raw -Encoding UTF8 | ConvertFrom-Json
[GenGate.ShellLinks]::Write($v.path, $v.target, $v.arguments, $v.working, $v.icon, 'PhotoCraft with GenGate')
