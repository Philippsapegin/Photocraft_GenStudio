param([Parameter(Mandatory=$true)][string]$Data)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
. (Join-Path $PSScriptRoot 'Shell-Link.ps1')
$v = Get-Content -LiteralPath $Data -Raw -Encoding UTF8 | ConvertFrom-Json
if ($v.locations) {
    $locations = @($v.locations)
    $programs = $v.programs
    $desktop = $v.desktopPath
} else {
    $programs = [Environment]::GetFolderPath('Programs')
    $desktop = [Environment]::GetFolderPath('Desktop')
    $locations = @($programs, [Environment]::GetFolderPath('CommonPrograms'))
    if ($v.desktop) { $locations += @($desktop, [Environment]::GetFolderPath('CommonDesktopDirectory')) }
}
$canonical = @(Join-Path $programs 'PhotoCraft.lnk')
if ($v.desktop) { $canonical += Join-Path $desktop 'PhotoCraft.lnk' }
$existing = @()
foreach ($location in ($locations | Select-Object -Unique)) {
    if (-not $location -or -not (Test-Path -LiteralPath $location)) { continue }
    foreach ($file in (Get-ChildItem -LiteralPath $location -Filter '*.lnk' -Recurse -File)) {
        try { $link = [GenGate.ShellLinks]::Read($file.FullName) } catch { continue }
        $name = [IO.Path]::GetFileNameWithoutExtension($link.TargetPath)
        $native = $link.TargetPath -like '*.exe' -and $name -match '^photocraft(?:[-.]|$)'
        $wrapper = $file.Name -match '^PhotoCraft(?: (?:GenGate|GenStudio))?\.lnk$' -and $link.Arguments -match '(?i)(GenGate|GenStudio).*Launch\.ps1'
        if ($native -or $wrapper -or $canonical -contains $file.FullName) { $existing += $file.FullName }
    }
}
$backup = Join-Path $v.host ('.gengate\shortcut-backups\' + [Guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $backup -Force)
$snapshots = @()
foreach ($path in ($existing | Select-Object -Unique)) {
    $copy = Join-Path $backup ($snapshots.Count.ToString() + '.lnk')
    Copy-Item -LiteralPath $path -Destination $copy
    $snapshots += [PSCustomObject]@{ path=$path; backup=$copy }
}
$removed = @()
try {
    foreach ($path in $canonical) {
        [void](New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force)
        [GenGate.ShellLinks]::Write($path, $v.target, $v.arguments, $v.working, $v.icon, $v.description)
    }
    foreach ($path in $existing) {
        if ($canonical -notcontains $path) {
            Remove-Item -LiteralPath $path -Force
            $removed += $path
        }
    }
} catch {
    # Link repair is independent of editor patching; restore the link set on error.
    foreach ($path in $canonical) {
        if (-not ($snapshots | Where-Object path -eq $path)) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
    }
    foreach ($snapshot in $snapshots) { Copy-Item -LiteralPath $snapshot.backup -Destination $snapshot.path -Force }
    throw
}
$report = @{ canonical=$canonical; removed=$removed; backups=$snapshots; backupFolder=$backup }
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $backup 'report.json') -Encoding UTF8
$report | ConvertTo-Json -Depth 6 -Compress
