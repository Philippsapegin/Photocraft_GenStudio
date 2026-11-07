param([Parameter(Mandatory=$true)][string]$Data)
$ErrorActionPreference='Stop'
$request=Get-Content -LiteralPath $Data -Raw -Encoding UTF8 | ConvertFrom-Json
if(-not $request.host -and $request.photocraftDir){$request | Add-Member -MemberType NoteProperty -Name host -Value $request.photocraftDir}
$result=@{}
if($request.files){
    foreach($file in $request.files){
        $signature=Get-AuthenticodeSignature -LiteralPath $file
        if($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate){throw ('The official file has no valid signature: '+$file)}
        $publisher=$signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName,$false)
        if($publisher -ne $request.publisher){throw ('The official file has an unexpected publisher: '+$file)}
    }
    $result.verified=$true
}
if($request.msi){
    $installer=New-Object -ComObject WindowsInstaller.Installer
    $db=$installer.GetType().InvokeMember('OpenDatabase','InvokeMethod',$null,$installer,@($request.msi,0))
    $view=$db.GetType().InvokeMember('OpenView','InvokeMethod',$null,$db,@('SELECT `Property`, `Value` FROM `Property`'))
    $view.GetType().InvokeMember('Execute','InvokeMethod',$null,$view,$null)|Out-Null
    $properties=@{}
    try{
        while($row=$view.GetType().InvokeMember('Fetch','InvokeMethod',$null,$view,$null)){
            try{
                $key=$row.GetType().InvokeMember('StringData','GetProperty',$null,$row,1)
                $value=$row.GetType().InvokeMember('StringData','GetProperty',$null,$row,2)
                $properties[$key]=$value
            }finally{[void][Runtime.InteropServices.Marshal]::ReleaseComObject($row)}
        }
    }finally{
        $view.GetType().InvokeMember('Close','InvokeMethod',$null,$view,$null)|Out-Null
        foreach($com in @($view,$db,$installer)){[void][Runtime.InteropServices.Marshal]::ReleaseComObject($com)}
    }
    if($properties.ProductName -ne 'PhotoCraft' -or $properties.ProductVersion -ne $request.version -or $properties.UpgradeCode -ne $request.upgradeCode){throw 'The MSI is not the expected PhotoCraft release'}
    $result.msi=@{version=$properties.ProductVersion;upgradeCode=$properties.UpgradeCode;productCode=$properties.ProductCode}
}
if($request.host){
    $target=[IO.Path]::GetFullPath($request.host).TrimEnd('\')
    $found=@()
    foreach($scope in @('CurrentUser','LocalMachine')){
        foreach($view in @('Registry64','Registry32')){
            $hive=[Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::$scope,[Microsoft.Win32.RegistryView]::$view)
            try{
                $app=$hive.OpenSubKey('Software\PhotoCraft')
                $known=if($app){try{[string]$app.GetValue('InstallDir')}finally{$app.Dispose()}}else{''}
                $uninstall=$hive.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Uninstall')
                if(-not $uninstall){continue}
                try{
                    foreach($code in $uninstall.GetSubKeyNames()){
                        $key=$uninstall.OpenSubKey($code)
                        if(-not $key){continue}
                        try{
                            if($key.GetValue('DisplayName') -ne 'PhotoCraft' -or $key.GetValue('Publisher') -notin @('Learning Machines LLC','Learning Machines Inc') -or $key.GetValue('WindowsInstaller') -ne 1){continue}
                            $location=[string]$key.GetValue('InstallLocation');if(-not $location){$location=$known}
                            if($location -and [IO.Path]::GetFullPath($location).TrimEnd('\') -eq $target){$found+=@{kind='msi';scope=if($scope -eq 'CurrentUser'){'user'}else{'machine'};productCode=$code}}
                        }finally{$key.Dispose()}
                    }
                }finally{$uninstall.Dispose()}
            }finally{$hive.Dispose()}
        }
    }
    $unique=@($found|Group-Object productCode,scope|ForEach-Object {$_.Group[0]})
    if($unique.Count -gt 1){throw 'Multiple MSI registrations refer to this PhotoCraft folder. Repair it with its official installer first.'}
    $result.installation=if($unique.Count){$unique[0]}else{@{kind='portable';scope='user'}}
}
$result | ConvertTo-Json -Depth 8 -Compress
