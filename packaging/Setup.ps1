param([switch]$Preview, [switch]$ValidateOnly, [string]$ValidationScreenshot = '', [ValidateSet('ready','success','warning','failed')][string]$ValidationState = 'ready', [double]$ValidationScale = 1, [switch]$SelfTest)
& (Join-Path $PSScriptRoot 'SetupWindow.ps1') @PSBoundParameters
