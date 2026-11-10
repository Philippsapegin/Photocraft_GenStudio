$ErrorActionPreference='Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class GenGateSessionAccount {
    [DllImport("wtsapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool WTSQuerySessionInformation(IntPtr server,int session,int field,out IntPtr data,out int bytes);
    [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr data);
    static string Read(int session,int field){IntPtr p;int size;if(!WTSQuerySessionInformation(IntPtr.Zero,session,field,out p,out size))throw new System.ComponentModel.Win32Exception();try{return Marshal.PtrToStringUni(p)??"";}finally{WTSFreeMemory(p);}}
    public static string Sid(){int session=System.Diagnostics.Process.GetCurrentProcess().SessionId;string name=Read(session,5),domain=Read(session,7);if(name.Length==0)throw new Exception("Run setup in your signed-in Windows session.");return new System.Security.Principal.NTAccount(domain,name).Translate(typeof(System.Security.Principal.SecurityIdentifier)).Value;}
}
'@
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
@{currentSid=$identity.User.Value;sessionSid=[GenGateSessionAccount]::Sid()} | ConvertTo-Json -Compress
