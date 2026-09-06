// Native user-session tray host. Uses only the Windows inbox .NET Framework.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Security.Principal;
using System.Runtime.InteropServices;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;

sealed class AgentTray : ApplicationContext {
    readonly string data, program, state, runName;
    readonly NotifyIcon icon = new NotifyIcon();
    readonly Control dispatcher = new Control();
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 32768 };
    readonly ToolStripMenuItem startup = new ToolStripMenuItem("Start with Windows");
    readonly ToolStripMenuItem pause = new ToolStripMenuItem("Pause Agent");
    readonly string runKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    Process runtime;
    bool busy, exiting;
    string label = "Running";
    string iconState = "Running";
    Icon brand;
    [DllImport("user32.dll", SetLastError = true)] static extern bool DestroyIcon(IntPtr handle);
    Dictionary<string, object> last = new Dictionary<string, object>();

    static double Now() { return (DateTime.UtcNow - new DateTime(1970,1,1)).TotalSeconds; }
    static string Quote(string value) { return "\"" + value.TrimEnd('\\') + "\""; }
    static void NoLinks(string path) {
        for (string item = Path.GetFullPath(path); item != null; item = Path.GetDirectoryName(item)) {
            if ((File.Exists(item) || Directory.Exists(item)) && (File.GetAttributes(item) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Linked Agent storage is not supported.");
        }
    }
    void Write(string path, object value) {
        NoLinks(path);
        string temporary = Path.Combine(Path.GetDirectoryName(path), ".tray-" + Guid.NewGuid().ToString("N"));
        File.WriteAllText(temporary, json.Serialize(value));
        if (File.Exists(path)) File.Replace(temporary, path, null); else File.Move(temporary, path);
    }
    Dictionary<string, object> Read(string path) {
        NoLinks(path);
        if (new FileInfo(path).Length > 32768) throw new IOException("Invalid Agent state.");
        using (var stream = new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite | FileShare.Delete))
        using (var reader = new StreamReader(stream)) return json.Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
    }
    string Value(Dictionary<string, object> value, string key) { return value.ContainsKey(key) ? Convert.ToString(value[key]) : ""; }
    void Error() { MessageBox.Show("The Agent operation could not complete. Review the local logs and runtime settings.", "Blue Ash Reel", MessageBoxButtons.OK, MessageBoxIcon.Warning); }

    public AgentTray(string location) {
        data = Path.GetFullPath(location); NoLinks(data);
        var settings = Read(Path.Combine(data, @"configuration\installation.json"));
        if (Value(settings, "runtime_mode") != "per_user") throw new IOException("Run the user-mode migration before launching the tray.");
        program = Path.GetFullPath(Value(settings, "program_dir")); NoLinks(program);
        if (!String.Equals(program, AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
            throw new IOException("The Agent installation identity does not match.");
        state = Path.Combine(data, "state"); runName = Value(settings,"service_prefix") + "Tray";
        var menu = new ContextMenuStrip();
        menu.Items.Add("Open Blue Ash Reel", null, delegate { Open(false); });
        menu.Items.Add("Open Local Agent Status", null, delegate { Browser(Local("status")); });
        menu.Items.Add("Pair Agent", null, delegate { Browser(Local("pair")); });
        menu.Items.Add("Reconnect", null, delegate { Command("reconnect"); });
        pause.Click += delegate { Command(label == "Paused" ? "restart" : "pause"); }; menu.Items.Add(pause);
        menu.Items.Add("Restart Agent", null, delegate { Command("restart"); });
        menu.Items.Add("View Status", null, delegate { ShowStatus(); });
        menu.Items.Add("Open Logs", null, delegate {
            string logs = Path.Combine(data,"logs");
            if (settings.ContainsKey("storage")) logs = Value((Dictionary<string,object>)settings["storage"],"logs");
            Process.Start(new ProcessStartInfo(logs) { UseShellExecute = true });
        });
        menu.Items.Add("Settings", null, delegate { Settings(settings); });
        startup.Checked = IsStartup(); startup.Click += delegate { SetStartup(!IsStartup()); }; menu.Items.Add(startup);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Exit Blue Ash Reel", null, delegate {
            if (MessageBox.Show("Exit Blue Ash Reel? Active playback and scans will stop and this Agent will appear offline.",
                "Blue Ash Reel", MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes) ExitAgent();
        });
        brand = MakeIcon(Color.FromArgb(48,121,221));
        icon.Icon = brand; icon.Text = "Blue Ash Reel — Running"; icon.ContextMenuStrip = menu; icon.Visible = true;
        icon.DoubleClick += delegate { Open(false); };
        IntPtr dispatcherHandle = dispatcher.Handle;
        SystemEvents.SessionEnding += OnSessionEnding;
        StartRuntime();
        timer.Interval = 750; timer.Tick += Tick; timer.Start();
    }
    Icon MakeIcon(Color color) {
        using (var bitmap = new Bitmap(32,32)) using (var graphics = Graphics.FromImage(bitmap)) {
            graphics.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            using (var brush = new SolidBrush(color)) graphics.FillEllipse(brush,1,1,30,30);
            using (var font = new Font("Segoe UI",18,FontStyle.Bold)) graphics.DrawString("B",font,Brushes.White,3,-1);
            IntPtr handle = bitmap.GetHicon();
            try { using (Icon temporary = Icon.FromHandle(handle)) return (Icon)temporary.Clone(); }
            finally { DestroyIcon(handle); }
        }
    }
    string Local(string purpose) {
        var settings = Read(Path.Combine(data,@"configuration\installation.json"));
        return "http://127.0.0.1:" + Value(settings,"port") + "/portal/start?purpose=" + purpose;
    }
    void Browser(string url) { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
    void ShowStatus() {
        MessageBox.Show("Agent: " + label + "\n" + (last.ContainsKey("fingerprint") ? "Fingerprint: " + Value(last,"fingerprint") : "") +
            (label == "Update available" ? "\nPublished update: " + Value(last,"update_version") : "") +
            "\n\nSign in to the Portal to browse or manage media.", "Blue Ash Reel status", MessageBoxButtons.OK, MessageBoxIcon.Information);
    }
    void Open(bool pair) { Browser(pair || label == "Unpaired" ? Local("pair") : "https://blueashreel.com"); }
    void StartRuntime() {
        runtime = Process.Start(new ProcessStartInfo(Path.Combine(program,@"runtime\python\pythonw.exe"),
            "-I -B -m app.native_tray --data-dir " + Quote(data) + " --parent-pid " + Process.GetCurrentProcess().Id) {
            UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden, WorkingDirectory = data
        });
    }
    void Command(string action) {
        try {
            if (runtime == null || runtime.HasExited) {
                if (action == "restart" || action == "reconnect") { StartRuntime(); return; }
                throw new IOException();
            }
            Write(Path.Combine(state,"tray-command.json"),new { action = action, created_at = Now() });
        } catch { Error(); }
    }
    bool IsStartup() { using (var key = Registry.CurrentUser.OpenSubKey(runKey)) return key != null && key.GetValue(runName) != null; }
    void SetStartup(bool enabled) {
        using (var key = Registry.CurrentUser.CreateSubKey(runKey)) {
            if (enabled) key.SetValue(runName,Quote(Application.ExecutablePath) + " --data-dir " + Quote(data));
            else key.DeleteValue(runName,false);
        }
        startup.Checked = enabled;
    }
    void Settings(Dictionary<string,object> settings) {
        using (var form = new Form { Text = "Blue Ash Reel settings", Width = 620, Height = 430, StartPosition = FormStartPosition.CenterScreen }) {
            var text = new TextBox { Multiline = true, ReadOnly = true, Dock = DockStyle.Fill, ScrollBars = ScrollBars.Vertical };
            text.Text = "Runtime: your signed-in Windows account\r\nPortal: https://blueashreel.com\r\nLocal port: " + Value(settings,"port") +
                "\r\nProgram files: " + program + "\r\nApplication data: " + data + "\r\n";
            if (settings.ContainsKey("storage")) foreach (var item in (Dictionary<string,object>)settings["storage"]) text.AppendText(item.Key + ": " + item.Value + "\r\n");
            text.AppendText("\r\nUse the installer's Advanced page to choose storage at installation. Existing locations are preserved during upgrades.\r\nAdd libraries from the authenticated Portal; approve folders on this workstation.\r\nOutbound media-provider access is blocked by the Agent's strict-local policy; only the device connector communicates with the Portal.");
            var check = new CheckBox { Text = "Start automatically when I sign in to Windows", Checked = IsStartup(), Dock = DockStyle.Bottom, Height = 40 };
            check.CheckedChanged += delegate { SetStartup(check.Checked); };
            form.Controls.Add(text); form.Controls.Add(check); form.ShowDialog();
        }
    }
    void Tick(object sender, EventArgs args) {
        if (busy || exiting) return;
        busy = true;
        try {
            string status = Path.Combine(state,"tray-status.json");
            if (File.Exists(status)) last = Read(status);
            if (runtime != null && runtime.HasExited && Value(last,"state") == "Stopped") {
                icon.Visible = false; icon.Dispose(); timer.Stop(); ExitThread(); return;
            }
            label = runtime == null || runtime.HasExited || !last.ContainsKey("updated_at") || Now() - Convert.ToDouble(last["updated_at"]) > 75
                ? "Agent error" : Value(last,"state");
            icon.Text = "Blue Ash Reel — " + label;
            if (iconState != label) {
                Color color = label == "Connected" ? Color.SeaGreen : label == "Unpaired" ? Color.Goldenrod :
                    label == "Agent error" || label == "Portal unavailable" ? Color.Firebrick :
                    label == "Paused" ? Color.Gray : label == "Update available" ? Color.MediumPurple : Color.RoyalBlue;
                Icon previous = brand; brand = MakeIcon(color); icon.Icon = brand; previous.Dispose(); iconState = label;
            }
            pause.Text = label == "Paused" ? "Resume Agent" : "Pause Agent";
            string uiRequest = Path.Combine(state,"tray-window.json");
            if (File.Exists(uiRequest)) {
                var action = Read(uiRequest); File.Delete(uiRequest);
                if (Now() - Convert.ToDouble(action["created_at"]) < 15) {
                    if (Value(action,"action") == "status") ShowStatus();
                    else if (Value(action,"action") == "open") Open(false);
                }
            }
            string requests = Path.Combine(state,"tray-requests");
            if (Directory.Exists(requests)) foreach (string path in Directory.GetFiles(requests,"request-*.json")) { Consent(path); break; }
        } catch { label = "Agent error"; icon.Text = "Blue Ash Reel — Agent error"; }
        finally { busy = false; }
    }
    void Consent(string path) {
        var request = Read(path);
        string id = Value(request,"id");
        Guid parsed;
        if (!Guid.TryParseExact(id,"N",out parsed) || Path.GetFileName(path) != "request-" + id + ".json") return;
        string response = Path.Combine(Path.GetDirectoryName(path),"response-" + id + ".json");
        if (File.Exists(response)) return;
        bool approved = false; string selected = null;
        if (Now() < Convert.ToDouble(request["expires_at"])) {
            if (Value(request,"kind") == "folder") {
                selected = Value(request,"path");
                if (String.IsNullOrEmpty(selected)) using (var dialog = new FolderBrowserDialog { Description = "Choose a media folder requested by the authenticated Portal Owner", ShowNewFolderButton = false }) {
                    if (dialog.ShowDialog() == DialogResult.OK) selected = dialog.SelectedPath;
                }
                if (!String.IsNullOrEmpty(selected)) approved = MessageBox.Show("Allow Blue Ash Reel to read media from this folder?\n\n" + selected +
                    "\n\nSource files remain read-only. The path stays on this Agent and in the encrypted Owner session.", "Confirm media folder",
                    MessageBoxButtons.YesNo,MessageBoxIcon.Question) == DialogResult.Yes;
            } else if (Value(request,"kind") == "confirmation") {
                approved = MessageBox.Show(Value(request,"message"),"Confirm Blue Ash Reel pairing",MessageBoxButtons.YesNo,MessageBoxIcon.Question) == DialogResult.Yes;
            }
        }
        approved = approved && Now() < Convert.ToDouble(request["expires_at"]);
        if (File.Exists(path)) Write(response,new { id = id, approved = approved, path = approved ? selected : null });
    }
    void ExitAgent() {
        if (exiting) return; exiting = true; timer.Stop();
        if (runtime != null && !runtime.HasExited) {
            Command("exit");
            if (!runtime.WaitForExit(45000)) runtime.Kill();
        }
        SystemEvents.SessionEnding -= OnSessionEnding;
        icon.Visible = false; icon.Dispose(); dispatcher.Dispose(); ExitThread();
    }
    void OnSessionEnding(object sender, SessionEndingEventArgs args) {
        // SystemEvents arrives on its own thread; shutdown belongs to the tray's
        // UI thread, including draining children and removing the notification icon.
        if (!exiting) dispatcher.Invoke(new Action(ExitAgent));
    }
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        try {
            if ((args.Length != 2 && args.Length != 3) || args[0] != "--data-dir" || (args.Length == 3 && args[2] != "--status"))
                throw new ArgumentException("Launch the Agent from its installed shortcut.");
            string sid = WindowsIdentity.GetCurrent().User.Value;
            string key = Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(Path.GetFullPath(args[1]).ToLowerInvariant())).Replace('/','_');
            bool created;
            using (var singleton = new Mutex(true,"Local\\BlueAshReel-" + sid + "-" + key,out created)) {
                if (!created || args.Length == 3) {
                    string path = Path.Combine(Path.GetFullPath(args[1]),@"state\tray-window.json"); NoLinks(path);
                    File.WriteAllText(path,new JavaScriptSerializer().Serialize(new { action = args.Length == 3 ? "status" : "open", created_at = Now() }));
                    if (!created) return 0;
                }
                Application.Run(new AgentTray(args[1]));
            }
            return 0;
        } catch {
            MessageBox.Show("Blue Ash Reel could not start. Run the installer to repair the per-user Agent. Existing media and Agent data were preserved.", "Blue Ash Reel", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
