# Use the Unicode Shell API: WScript.Shell rejects targets outside the system
# ANSI code page, even though JSON and the filesystem preserve their names.
if (-not ('GenGate.ShellLinks' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

namespace GenGate {
    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int size, IntPtr findData, uint flags);
        void GetIDList(out IntPtr list);
        void SetIDList(IntPtr list);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text, int size);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string text);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int size);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string path);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text, int size);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string text);
        void GetHotkey(out short key);
        void SetHotkey(short key);
        void GetShowCmd(out int command);
        void SetShowCmd(int command);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int size, out int index);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string path, int index);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string path, uint reserved);
        void Resolve(IntPtr window, uint flags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string path);
    }

    public sealed class ShellLinkData {
        public string TargetPath { get; set; }
        public string Arguments { get; set; }
    }

    public static class ShellLinks {
        static object NewLink() {
            return Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046")));
        }

        public static ShellLinkData Read(string path) {
            object value = NewLink();
            try {
                ((IPersistFile)value).Load(path, 0);
                var link = (IShellLinkW)value;
                var target = new StringBuilder(32768);
                var arguments = new StringBuilder(32768);
                link.GetPath(target, target.Capacity, IntPtr.Zero, 4);
                link.GetArguments(arguments, arguments.Capacity);
                return new ShellLinkData { TargetPath = target.ToString(), Arguments = arguments.ToString() };
            } finally { Marshal.FinalReleaseComObject(value); }
        }

        public static void Write(string path, string target, string arguments, string working, string icon, string description) {
            object value = NewLink();
            try {
                var link = (IShellLinkW)value;
                link.SetPath(target);
                link.SetArguments(arguments ?? "");
                link.SetWorkingDirectory(working);
                link.SetIconLocation(icon, 0);
                link.SetDescription(description);
                ((IPersistFile)value).Save(path, true);
            } finally { Marshal.FinalReleaseComObject(value); }
        }
    }
}
'@
}
