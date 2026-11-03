using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace GenGateSetup {
    public static class Palette {
        public static readonly Color Background = Color.FromArgb(27, 27, 29);
        public static readonly Color Surface = Color.FromArgb(36, 36, 39);
        public static readonly Color Edge = Color.FromArgb(60, 60, 65);
        public static readonly Color Text = Color.FromArgb(234, 234, 238);
        public static readonly Color Muted = Color.FromArgb(158, 158, 168);
        public static readonly Color Accent = Color.FromArgb(151, 132, 239);
        public static GraphicsPath Round(Rectangle r, int radius) {
            var p = new GraphicsPath(); int d = radius * 2;
            p.AddArc(r.X, r.Y, d, d, 180, 90); p.AddArc(r.Right-d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right-d, r.Bottom-d, d, d, 0, 90); p.AddArc(r.X, r.Bottom-d, d, d, 90, 90); p.CloseFigure(); return p;
        }
    }
    public class ChromeForm : Form {
        public bool Preview;
        protected override bool ShowWithoutActivation { get { return Preview; } }
        [DllImport("user32.dll")] static extern bool ReleaseCapture();
        [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, int m, IntPtr w, IntPtr l);
        public ChromeForm() { FormBorderStyle=FormBorderStyle.None; DoubleBuffered=true; BackColor=Palette.Background; ForeColor=Palette.Text; AutoScaleMode=AutoScaleMode.Dpi; AutoScaleDimensions=new SizeF(96,96); }
        public void DragWindow() { ReleaseCapture(); SendMessage(Handle,0xA1,new IntPtr(2),IntPtr.Zero); }
        protected override void OnPaint(PaintEventArgs e) { base.OnPaint(e); using(var p=new Pen(Palette.Edge))e.Graphics.DrawRectangle(p,0,0,ClientSize.Width-1,ClientSize.Height-1); }
    }
    public class Surface : Panel {
        public Surface() { DoubleBuffered=true; BackColor=Palette.Background; Padding=new Padding(9,5,9,5); }
        protected override void OnPaintBackground(PaintEventArgs e) {
            e.Graphics.Clear(BackColor); e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;
            using(var path=Palette.Round(new Rectangle(0,0,Width-1,Height-1),5))
            using(var fill=new SolidBrush(Palette.Surface))using(var edge=new Pen(Palette.Edge)){e.Graphics.FillPath(fill,path);e.Graphics.DrawPath(edge,path);}
        }
    }
    public class FlatButton : Button {
        public bool Primary;
        bool hover;
        public FlatButton() { FlatStyle=FlatStyle.Flat; FlatAppearance.BorderSize=0; Cursor=Cursors.Hand; UseVisualStyleBackColor=false; TabStop=true; }
        protected override void OnMouseEnter(EventArgs e){hover=true;Invalidate();base.OnMouseEnter(e);}
        protected override void OnMouseLeave(EventArgs e){hover=false;Invalidate();base.OnMouseLeave(e);}
        protected override void OnPaint(PaintEventArgs e){
            e.Graphics.Clear(BackColor);e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;
            Color bg=Primary?Palette.Accent:Color.FromArgb(46,46,50); if(hover&&Enabled)bg=ControlPaint.Light(bg,.12f);
            using(var path=Palette.Round(new Rectangle(0,0,Width-1,Height-1),5))using(var brush=new SolidBrush(bg))using(var pen=new Pen(Primary?bg:Palette.Edge)){e.Graphics.FillPath(brush,path);e.Graphics.DrawPath(pen,path);}
            TextRenderer.DrawText(e.Graphics,Text,Font,ClientRectangle,Enabled?(Primary?Color.FromArgb(27,23,40):Palette.Text):Palette.Muted,TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);
            if(Focused&&ShowFocusCues){var r=ClientRectangle;r.Inflate(-4,-4);ControlPaint.DrawFocusRectangle(e.Graphics,r,Palette.Text,bg);}
        }
    }
    public class FlatCombo : Control {
        public System.Collections.Generic.List<string> Items {get;private set;}
        int selected=-1;
        public event EventHandler SelectedIndexChanged;
        public int SelectedIndex {get{return selected;}set{if(value<0||value>=Items.Count)throw new ArgumentOutOfRangeException("value");if(selected==value)return;selected=value;Invalidate();if(SelectedIndexChanged!=null)SelectedIndexChanged(this,EventArgs.Empty);}}
        public FlatCombo(){Items=new System.Collections.Generic.List<string>();DoubleBuffered=true;TabStop=true;Cursor=Cursors.Hand;BackColor=Palette.Background;ForeColor=Palette.Text;AccessibleRole=AccessibleRole.ComboBox;}
        void OpenMenu(){if(!Enabled)return;Focus();var menu=new ContextMenuStrip();menu.ShowImageMargin=false;menu.BackColor=Palette.Surface;menu.ForeColor=Palette.Text;menu.Renderer=new ToolStripProfessionalRenderer(new MenuColors());for(int i=0;i<Items.Count;i++){int index=i;var item=new ToolStripMenuItem(Items[i]);item.Checked=i==selected;item.Click+=delegate{SelectedIndex=index;};menu.Items.Add(item);}menu.Closed+=delegate{menu.Dispose();};menu.Show(this,0,Height);}
        protected override void OnMouseDown(MouseEventArgs e){base.OnMouseDown(e);if(e.Button==MouseButtons.Left)OpenMenu();}
        protected override bool IsInputKey(Keys key){return key==Keys.Up||key==Keys.Down||base.IsInputKey(key);}
        protected override void OnKeyDown(KeyEventArgs e){base.OnKeyDown(e);if(e.KeyCode==Keys.Space||e.KeyCode==Keys.Enter){OpenMenu();e.Handled=true;}else if(e.KeyCode==Keys.Down&&selected<Items.Count-1){SelectedIndex=selected+1;e.Handled=true;}else if(e.KeyCode==Keys.Up&&selected>0){SelectedIndex=selected-1;e.Handled=true;}}
        protected override void OnPaint(PaintEventArgs e){e.Graphics.Clear(BackColor);e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;using(var p=Palette.Round(new Rectangle(0,0,Width-1,Height-1),5))using(var b=new SolidBrush(Palette.Surface))using(var pen=new Pen(Focused?Palette.Accent:Palette.Edge)){e.Graphics.FillPath(b,p);e.Graphics.DrawPath(pen,p);}if(selected>=0)TextRenderer.DrawText(e.Graphics,Items[selected],Font,new Rectangle(10,0,Width-38,Height),Enabled?Palette.Text:Palette.Muted,TextFormatFlags.Left|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);using(var pen=new Pen(Palette.Muted,1.5f)){e.Graphics.DrawLines(pen,new Point[]{new Point(Width-22,Height/2-2),new Point(Width-17,Height/2+3),new Point(Width-12,Height/2-2)});}}
    }
    public class MenuColors : ProfessionalColorTable {
        public override Color ToolStripDropDownBackground {get{return Palette.Surface;}}
        public override Color MenuBorder {get{return Palette.Edge;}}
        public override Color MenuItemSelected {get{return Color.FromArgb(70,61,101);}}
        public override Color MenuItemBorder {get{return Palette.Accent;}}
        public override Color MenuItemSelectedGradientBegin {get{return MenuItemSelected;}}
        public override Color MenuItemSelectedGradientEnd {get{return MenuItemSelected;}}
    }
    public class AccentCheck : CheckBox {
        public AccentCheck(){SetStyle(ControlStyles.UserPaint|ControlStyles.OptimizedDoubleBuffer,true);Cursor=Cursors.Hand;}
        protected override void OnPaint(PaintEventArgs e){
            e.Graphics.Clear(BackColor);e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;
            int size=Math.Max(13,Math.Min(Height-8,Font.Height)),top=(Height-size)/2,left=2;
            using(var path=Palette.Round(new Rectangle(left,top,size,size),3))using(var fill=new SolidBrush(Checked?Palette.Accent:Palette.Surface))using(var pen=new Pen(Palette.Edge)){e.Graphics.FillPath(fill,path);e.Graphics.DrawPath(pen,path);}
            if(Checked)using(var pen=new Pen(Palette.Background,Math.Max(1.7f,size*.11f)))e.Graphics.DrawLines(pen,new PointF[]{new PointF(left+size*.2f,top+size*.48f),new PointF(left+size*.42f,top+size*.7f),new PointF(left+size*.8f,top+size*.28f)});
            int textLeft=left+size+9;TextRenderer.DrawText(e.Graphics,Text,Font,new Rectangle(textLeft,0,Math.Max(0,Width-textLeft-1),Height),Enabled?Palette.Text:Palette.Muted,TextFormatFlags.Left|TextFormatFlags.VerticalCenter);
            if(Focused&&ShowFocusCues){var r=ClientRectangle;r.Inflate(-2,-2);ControlPaint.DrawFocusRectangle(e.Graphics,r);}
        }
    }
    public class Progress : Control {
        public int Value=-1;
        int phase;
        public Progress(){DoubleBuffered=true;Height=4;}
        public void Advance(){phase=(phase+9)%Math.Max(1,Width+100);Invalidate();}
        protected override void OnPaint(PaintEventArgs e){e.Graphics.Clear(Palette.Surface);using(var b=new SolidBrush(Palette.Accent)){if(Value<0)e.Graphics.FillRectangle(b,phase-100,0,100,Height);else e.Graphics.FillRectangle(b,0,0,Width*Math.Max(0,Math.Min(100,Value))/100,Height);}}
    }
    public class Body : FlowLayoutPanel {
        public Body(){FlowDirection=FlowDirection.TopDown;WrapContents=false;AutoSize=true;AutoSizeMode=AutoSizeMode.GrowAndShrink;Dock=DockStyle.Top;Padding=new Padding(24,14,24,14);}
        protected override void OnSizeChanged(EventArgs e){base.OnSizeChanged(e);foreach(Control c in Controls)c.Width=Math.Max(200,ClientSize.Width-Padding.Horizontal-c.Margin.Horizontal);}
        protected override void OnControlAdded(ControlEventArgs e){base.OnControlAdded(e);e.Control.Width=Math.Max(200,ClientSize.Width-Padding.Horizontal-e.Control.Margin.Horizontal);}
    }
    public class ScrollArea : Panel {
        Body content;int offset,grab;bool layout,drag;
        public ScrollArea(){DoubleBuffered=true;BackColor=Palette.Background;AccessibleName="Setup options";AccessibleRole=AccessibleRole.Pane;}
        int Limit {get{return content==null?0:Math.Max(0,content.Height-ClientSize.Height);}}
        public int Offset {get{return offset;}}
        Rectangle Thumb {
            get{int track=Math.Max(1,ClientSize.Height-12);int height=Math.Min(track,Math.Max(28,content==null?track:(int)((long)track*ClientSize.Height/Math.Max(1,content.Height))));
                return new Rectangle(ClientSize.Width-10,6+(Limit==0?0:(int)((long)offset*(track-height)/Limit)),6,height);}
        }
        public void Attach(Body body){content=body;content.Dock=DockStyle.None;Controls.Add(content);Hook(content);content.SizeChanged+=delegate{Arrange();};Arrange();}
        void Hook(Control c){
            c.ControlAdded+=delegate(object sender,ControlEventArgs e){Hook(e.Control);};
            c.MouseWheel+=Wheel;
            c.Enter+=delegate{if(content==null)return;Point p=content.PointToClient(c.PointToScreen(Point.Empty));if(p.Y<offset)ScrollTo(p.Y);else if(p.Y+c.Height>offset+ClientSize.Height)ScrollTo(p.Y+c.Height-ClientSize.Height);};
            foreach(Control child in c.Controls)Hook(child);
        }
        void Wheel(object sender,MouseEventArgs e){
            var handled=e as HandledMouseEventArgs;if(handled!=null&&handled.Handled)return;
            var text=sender as TextBox;if(text!=null&&text.Multiline)return;
            ScrollTo(offset-Math.Sign(e.Delta)*Math.Max(40,Font.Height*3));if(handled!=null)handled.Handled=true;
        }
        public void ScrollTo(int position){offset=Math.Max(0,Math.Min(Limit,position));if(content!=null)content.Top=-offset;Invalidate();}
        void Arrange(){
            if(layout||content==null)return;layout=true;
            try{int width=Math.Max(200,ClientSize.Width-(Limit>0?14:0));content.MinimumSize=new Size(width,0);content.MaximumSize=new Size(width,0);content.Width=width;ScrollTo(offset);}finally{layout=false;}
        }
        protected override void OnSizeChanged(EventArgs e){base.OnSizeChanged(e);Arrange();}
        protected override void OnMouseWheel(MouseEventArgs e){Wheel(this,e);base.OnMouseWheel(e);}
        protected override void OnPaint(PaintEventArgs e){base.OnPaint(e);if(Limit==0)return;e.Graphics.SmoothingMode=SmoothingMode.AntiAlias;using(var p=Palette.Round(Thumb,3))using(var b=new SolidBrush(drag?Palette.Accent:Palette.Edge))e.Graphics.FillPath(b,p);}
        protected override void OnMouseDown(MouseEventArgs e){base.OnMouseDown(e);if(e.Button!=MouseButtons.Left||Limit==0||e.X<ClientSize.Width-14)return;var thumb=Thumb;grab=thumb.Contains(e.Location)?e.Y-thumb.Y:thumb.Height/2;drag=true;Capture=true;DragTo(e.Y);}
        void DragTo(int y){int travel=Math.Max(1,ClientSize.Height-12-Thumb.Height);ScrollTo((int)Math.Max(0,Math.Min(Limit,(long)(y-6-grab)*Limit/travel)));}
        protected override void OnMouseMove(MouseEventArgs e){base.OnMouseMove(e);if(drag)DragTo(e.Y);}
        protected override void OnMouseUp(MouseEventArgs e){base.OnMouseUp(e);drag=false;Capture=false;Invalidate();}
    }
    public static class Picker {
        // A dedicated STA protects dialogs even when Setup.ps1 was opened from
        // an MTA host. The owner keeps the Explorer window above the installer.
        public static Func<IWin32Window,string,bool,string> Override;
        sealed class Owner : IWin32Window { public IntPtr Handle {get;private set;} public Owner(IntPtr h){Handle=h;} }
        // IFileDialog, documented by the Windows Common Item Dialog API. Only
        // methods through GetResult are needed; order follows shobjidl_core.h.
        [ComImport,Guid("42f85136-db7e-439c-85f1-e4075d135fc8"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        public interface IFileDialog {
            [PreserveSig] int Show(IntPtr owner);
            void SetFileTypes(uint count,IntPtr filters);void SetFileTypeIndex(uint index);void GetFileTypeIndex(out uint index);
            void Advise(IFileDialogEvents sink,out uint cookie);void Unadvise(uint cookie);
            void SetOptions(uint flags);void GetOptions(out uint flags);
            void SetDefaultFolder(IShellItem item);void SetFolder(IShellItem item);void GetFolder(out IShellItem item);void GetCurrentSelection(out IShellItem item);
            void SetFileName([MarshalAs(UnmanagedType.LPWStr)]string name);void GetFileName(out IntPtr name);
            void SetTitle([MarshalAs(UnmanagedType.LPWStr)]string title);void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)]string label);void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)]string label);
            void GetResult(out IShellItem item);
            void AddPlace(IShellItem item,uint alignment);void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)]string extension);void Close(int result);
        }
        [ComImport,Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        public interface IShellItem {
            void BindToHandler(IntPtr context,ref Guid handler,ref Guid iid,out IntPtr result);
            void GetParent(out IShellItem parent);void GetDisplayName(uint kind,out IntPtr name);
        }
        [ComVisible(true),Guid("973510db-7d7f-452b-8975-74a85828d354"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        public interface IFileDialogEvents {
            [PreserveSig]int OnFileOk(IFileDialog dialog);
            [PreserveSig]int OnFolderChanging(IFileDialog dialog,IShellItem folder);
            [PreserveSig]int OnFolderChange(IFileDialog dialog);
            [PreserveSig]int OnSelectionChange(IFileDialog dialog);
            [PreserveSig]int OnShareViolation(IFileDialog dialog,IShellItem item,out uint response);
            [PreserveSig]int OnTypeChange(IFileDialog dialog);
            [PreserveSig]int OnOverwrite(IFileDialog dialog,IShellItem item,out uint response);
        }
        [ComVisible(true),ClassInterface(ClassInterfaceType.None)]
        public sealed class CancelDialogProbe : IFileDialogEvents {
            public int OnFileOk(IFileDialog d){return 0;}public int OnFolderChanging(IFileDialog d,IShellItem f){return 0;}
            public int OnFolderChange(IFileDialog d){d.Close(unchecked((int)0x800704C7));return 0;}
            public int OnSelectionChange(IFileDialog d){return 0;}
            public int OnShareViolation(IFileDialog d,IShellItem i,out uint r){r=0;return 0;}
            public int OnTypeChange(IFileDialog d){return 0;}public int OnOverwrite(IFileDialog d,IShellItem i,out uint r){r=0;return 0;}
        }
        [DllImport("shell32.dll",CharSet=CharSet.Unicode,PreserveSig=true)]
        static extern int SHCreateItemFromParsingName(string path,IntPtr context,ref Guid iid,out IShellItem item);
        static string ModernFolder(IntPtr owner,string initial,bool show,bool cancelForTest=false){
            object instance=Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")));
            IShellItem start=null,result=null;
            try {
                var dialog=(IFileDialog)instance;uint options;dialog.GetOptions(out options);
                dialog.SetOptions(options|0x20u|0x40u|0x800u|0x8u|0x02000000u);dialog.SetTitle("Choose folder");
                if(Directory.Exists(initial)){
                    Guid iid=new Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe");
                    Marshal.ThrowExceptionForHR(SHCreateItemFromParsingName(initial,IntPtr.Zero,ref iid,out start));dialog.SetFolder(start);
                }
                if(!show)return initial;
                int hr;
                uint cookie=0;var cancel=new CancelDialogProbe();
                try{
                    if(cancelForTest)dialog.Advise(cancel,out cookie);
                    hr=dialog.Show(owner);
                }finally{if(cancelForTest)dialog.Unadvise(cookie);GC.KeepAlive(cancel);}
                if(hr==unchecked((int)0x800704C7))return null;Marshal.ThrowExceptionForHR(hr);
                dialog.GetResult(out result);IntPtr path;result.GetDisplayName(0x80058000,out path);
                try{return Marshal.PtrToStringUni(path);}finally{Marshal.FreeCoTaskMem(path);}
            }finally{if(result!=null)Marshal.ReleaseComObject(result);if(start!=null)Marshal.ReleaseComObject(start);Marshal.ReleaseComObject(instance);}
        }
        static void OnSta(ThreadStart action){
            if(Thread.CurrentThread.GetApartmentState()==ApartmentState.STA){action();return;}
            Exception failure=null;var thread=new Thread(delegate(){try{action();}catch(Exception error){failure=error;}});
            thread.SetApartmentState(ApartmentState.STA);thread.Start();while(!thread.Join(15))Application.DoEvents();
            if(failure!=null)throw new InvalidOperationException("The Explorer dialog could not initialize.",failure);
        }
        public static void ProbeModern(string directory){OnSta(delegate{ModernFolder(IntPtr.Zero,directory,false);});}
        public static void ProbeOpenAndCancel(string directory){OnSta(delegate{if(ModernFolder(IntPtr.Zero,directory,true,true)!=null)throw new InvalidOperationException("The Explorer test did not cancel.");});}
        public static ApartmentState ProbeApartment(){ApartmentState result=ApartmentState.Unknown;OnSta(delegate{result=Thread.CurrentThread.GetApartmentState();});return result;}
        public static string Choose(IWin32Window owner,string initial,bool folder) {
            if(Override!=null)return Override(owner,initial,folder);
            string selected=null;Exception failure=null;IntPtr handle=owner.Handle;
            ThreadStart action=delegate {
                try {
                    if(folder){
                        try{selected=ModernFolder(handle,initial,true);return;}
                        catch(COMException){ } // Keep the older shell picker as a compatibility fallback.
                        using(var dialog=new FolderBrowserDialog()){
                            dialog.Description="Choose folder";dialog.ShowNewFolderButton=true;
                            if(Directory.Exists(initial))dialog.SelectedPath=initial;
                            if(dialog.ShowDialog(new Owner(handle))==DialogResult.OK)selected=dialog.SelectedPath;
                        }
                    } else {
                        using(var dialog=new OpenFileDialog()){
                            dialog.Title="Choose engine Python";dialog.Filter="Python interpreter (python.exe)|python.exe";dialog.CheckFileExists=true;
                            if(File.Exists(initial)){dialog.FileName=initial;dialog.InitialDirectory=Path.GetDirectoryName(initial);}
                            if(dialog.ShowDialog(new Owner(handle))==DialogResult.OK)selected=dialog.FileName;
                        }
                    }
                }catch(Exception error){failure=error;}
            };
            OnSta(action);
            if(failure!=null)throw new InvalidOperationException("The folder/file picker could not open. You can paste the path into the field. "+failure.Message,failure);
            return selected;
        }
    }
    public sealed class Runner : IDisposable {
        public static string Quote(string value){
            var quoted=new StringBuilder("\"");int slashes=0;
            foreach(char c in value){if(c=='\\'){slashes++;continue;}quoted.Append('\\',c=='"'?slashes*2+1:slashes);quoted.Append(c);slashes=0;}
            quoted.Append('\\',slashes*2);return quoted.Append('"').ToString();
        }
        readonly ConcurrentQueue<string> pending=new ConcurrentQueue<string>();
        readonly object gate=new object();
        readonly StreamWriter log;
        readonly Process process;
        public Runner(string python,string arguments,string path) {
            log=new StreamWriter(path,false,new UTF8Encoding(false));log.AutoFlush=true;
            process=new Process();process.StartInfo=new ProcessStartInfo(python,arguments){UseShellExecute=false,CreateNoWindow=true,RedirectStandardOutput=true,RedirectStandardError=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
            process.OutputDataReceived+=Receive;process.ErrorDataReceived+=Receive;
            try{process.Start();process.BeginOutputReadLine();process.BeginErrorReadLine();}
            catch{process.Dispose();log.Dispose();throw;}
        }
        void Receive(object sender,DataReceivedEventArgs e){if(e.Data==null)return;lock(gate){try{log.WriteLine(e.Data);}catch(IOException){pending.Enqueue("NOTE: The setup log could not be written.");}pending.Enqueue(e.Data);}}
        public string[] Drain(){var lines=new System.Collections.Generic.List<string>();string line;while(pending.TryDequeue(out line))lines.Add(line);return lines.ToArray();}
        public bool HasExited {get{return process.HasExited;}}
        public int Finish(){process.WaitForExit();return process.ExitCode;}
        public void Cancel(){if(process.HasExited)return;var info=new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),"taskkill.exe"),"/PID "+process.Id+" /T /F"){UseShellExecute=false,CreateNoWindow=true};using(var killer=Process.Start(info)){killer.WaitForExit(5000);}}
        public void Dispose(){if(process.HasExited)process.WaitForExit();process.Dispose();lock(gate)log.Dispose();}
    }
}
