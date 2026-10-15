using System.ComponentModel;
using System.Globalization;
using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace WebcamFBT;

public partial class MainWindow : Window
{
    // MediaPipe / TensorFlow chatter on stderr that is not worth showing
    private static readonly Regex StderrNoise = new(@"^(W0000|I0000|INFO:|WARNING: Logging|WARNING: All log messages)");

    private readonly Engine _engine = new();
    private readonly DispatcherTimer _saveTimer = new() { Interval = TimeSpan.FromMilliseconds(600) };
    private readonly DispatcherTimer _watchdog = new() { Interval = TimeSpan.FromSeconds(1) };
    private readonly ConfigStore? _cfg;
    private readonly string? _startupError;
    private bool _loading = true;
    private bool _syncingPause;
    private bool _closeAllowed;
    private bool _restarting;
    private int _framePending;
    private DateTime _lastStatus;
    private DateTime _calibResultUntil;
    private bool? _calibResultOk;
    private string _calibFailSub = "Overlay.FailSub";
    private string _vrState = "off";
    private SetupAssistantWindow? _assistant;
    private TrayIcon? _tray;
    private bool _trayHintShown;
    private DateTime _lastTrayStatus;
    private DateTime _recenterUntil;

    // Automatic restart: a crashed or hung engine is restarted, but not more than MaxRestarts
    // times within RestartWindow, so a permanent problem (e.g. no camera) does not loop forever.
    private const int MaxRestarts = 3;
    private static readonly TimeSpan RestartWindow = TimeSpan.FromMinutes(3);
    private static readonly TimeSpan HangTimeout = TimeSpan.FromSeconds(30);
    // Until the first status the engine may still be opening cameras (slow after the PC wakes up).
    private static readonly TimeSpan StartupTimeout = TimeSpan.FromSeconds(90);
    private bool _gotStatus;
    private readonly Queue<DateTime> _restartTimes = new();
    private bool _userStopped = true;
    private List<CameraInfo>? _scannedCameras;
    private (string Key, object[] Args) _placeholder = ("Placeholder.Off", []);

    private Brush Good => (Brush)FindResource("GoodBrush");
    private Brush Warn => (Brush)FindResource("WarnBrush");
    private Brush Bad => (Brush)FindResource("BadBrush");
    private Brush Idle => (Brush)FindResource("IdleBrush");

    public MainWindow()
    {
        // Settings first: the language must be in place before the window is built.
        if (AppPaths.Root == null)
            _startupError = "Log.RootMissing";
        else if (!File.Exists(AppPaths.Python))
            _startupError = "Log.PythonMissing";
        else
        {
            try { _cfg = new ConfigStore(AppPaths.ConfigFile); }
            catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException)
            {
                _startupError = "Log.ConfigError";
                _startupErrorDetail = e.Message;
            }
        }
        Loc.Apply(_cfg?.GetString("language", "") is { Length: > 0 } lang ? lang : Loc.SystemDefault());

        InitializeComponent();
        Title = $"WebcamFBT {AppPaths.Version}";

        _engine.Message += msg => Dispatcher.BeginInvoke(() => HandleMessage(msg));
        _engine.FrameReady += OnFrame;
        _engine.ErrorOutput += line =>
        {
            if (!string.IsNullOrWhiteSpace(line) && !StderrNoise.IsMatch(line))
                Dispatcher.BeginInvoke(() => Log(line));
        };
        _engine.Exited += code => Dispatcher.BeginInvoke(() => OnEngineExited(code));
        _saveTimer.Tick += (_, _) => SaveConfig();
        _watchdog.Tick += (_, _) => CheckEngineAlive();

        foreach (var (code, name) in Loc.Languages)
            LanguageCombo.Items.Add(new ComboBoxItem { Content = name, Tag = code });
        LanguageCombo.SelectedItem = LanguageCombo.Items.Cast<ComboBoxItem>().First(i => (string)i.Tag == Loc.Current);

        _tray = new TrayIcon(ShowFromTray, () => _engine.Send(new { cmd = "calibrate" }),
            () => _engine.Send(new { cmd = "pause", value = PauseButton.IsChecked != true }), Close);
        _tray.SetEngineRunning(false);
        ResetChips();
        RefreshHotkeyTexts();
        if (_startupError != null)
        {
            Fail(_startupError switch
            {
                "Log.PythonMissing" => Loc.T(_startupError, AppPaths.Python),
                "Log.ConfigError" => Loc.T(_startupError, _startupErrorDetail ?? ""),
                _ => Loc.T(_startupError),
            });
            return;
        }

        LoadConfigIntoUi();
        RefreshDriverStatus();
        SetPlaceholder("Placeholder.Off");
        _loading = false;
        Log(Loc.T("Log.Folder", AppPaths.Root!));
        if (_cfg!.BrokenBackup != null)
            Log(Loc.T("Log.ConfigBroken", _cfg.BrokenBackup));
    }

    private readonly string? _startupErrorDetail;

    private void Window_Loaded(object sender, RoutedEventArgs e)
    {
        _ = LoadCameraNamesAsync();
        if (_cfg != null && _cfg.GetBool("check_updates", true))
            _ = CheckForUpdatesAsync(manual: false);
        var testing = Environment.GetCommandLineArgs().Contains("--assistant", StringComparer.OrdinalIgnoreCase);
        if (_cfg != null && !_cfg.GetBool("setup_done", false) && !testing)
            OpenWizard();
        else if (_cfg != null && _cfg.GetBool("auto_start", false))
            StartEngine();
        // "WebcamFBT.exe --assistant" opens the setup assistant right away.
        var args = Environment.GetCommandLineArgs();
        if (args.Contains("--assistant", StringComparer.OrdinalIgnoreCase))
        {
            AssistantButton_Click(this, new RoutedEventArgs());
            if (args.Contains("--spot", StringComparer.OrdinalIgnoreCase))
                _assistant?.SelectSpotTab();
            // "--assistant-snapshot <file.png>": save the assistant's content 15 s later (for checks).
            var at = Array.FindIndex(args, a => a.Equals("--assistant-snapshot", StringComparison.OrdinalIgnoreCase));
            if (at >= 0 && at + 1 < args.Length)
            {
                var path = args[at + 1];
                var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(15) };
                timer.Tick += (_, _) => { timer.Stop(); _assistant?.SaveSnapshot(path); };
                timer.Start();
            }
        }
    }

    // ------------------------------------------------------------------ config <-> UI

    private void LoadConfigIntoUi()
    {
        var cfg = _cfg!;
        RebuildCameraItems();

        var model = cfg.GetString("model", "full");
        ModelCombo.SelectedItem = ModelCombo.Items.Cast<ComboBoxItem>().FirstOrDefault(i => (string)i.Tag == model)
                                  ?? ModelCombo.Items[1];

        foreach (var check in TrackerGrid.Children.OfType<CheckBox>())
            check.IsChecked = cfg.GetTracker((string)check.Tag);

        SmoothSlider.Value = cfg.GetDouble("filter_min_cutoff", 1.0);
        BetaSlider.Value = cfg.GetDouble("filter_beta", 1.0);
        DepthSlider.Value = cfg.GetDouble("depth_smoothing", 0.6);
        VisibilitySlider.Value = cfg.GetDouble("min_visibility", 0.5);
        VisibilityText.Text = VisibilitySlider.Value.ToString("0.00");
        FloorLockCheck.IsChecked = cfg.GetBool("floor_lock", true);
        FootLockCheck.IsChecked = cfg.GetBool("foot_lock", true);
        MirrorCheck.IsChecked = cfg.GetBool("mirror_preview", true);
        AutoStartCheck.IsChecked = cfg.GetBool("auto_start", false);
        RememberCalibCheck.IsChecked = cfg.GetBool("remember_calibration", false);
        AutoCalibCheck.IsChecked = cfg.GetBool("auto_calibrate", true);
        GestureCalibCheck.IsChecked = cfg.GetBool("gesture_calibrate", true);
        ShowPreviewCheck.IsChecked = cfg.GetBool("show_preview", true);
        MinimizeToTrayCheck.IsChecked = cfg.GetBool("minimize_to_tray", true);
        CheckUpdatesCheck.IsChecked = cfg.GetBool("check_updates", true);
        FillHotkeyCombo(HotkeyCalibrateCombo, CalibrateKey);
        FillHotkeyCombo(HotkeyPauseCombo, PauseKey);
        RefreshHotkeyTexts();
    }

    private void RebuildCameraItems()
    {
        if (_cfg == null) return;
        var current = _cfg.GetInt("camera_index", 0);
        var wasLoading = _loading;
        _loading = true;
        CameraCombo.Items.Clear();
        if (_scannedCameras == null)
        {
            CameraCombo.Items.Add(new ComboBoxItem { Content = Loc.T("Camera.ItemSimple", current), Tag = current });
        }
        else
        {
            foreach (var cam in _scannedCameras)
                CameraCombo.Items.Add(new ComboBoxItem { Content = cam.Label, Tag = cam.Index });
            if (_scannedCameras.All(c => c.Index != current))
                CameraCombo.Items.Insert(0, new ComboBoxItem { Content = Loc.T("Camera.NotFound", current), Tag = current });
        }
        CameraCombo.SelectedItem = CameraCombo.Items.Cast<ComboBoxItem>().First(i => (int)i.Tag == current);
        RebuildExtraCameraItems();
        _loading = wasLoading;
    }

    /// <summary>Optional extra cameras: "None" plus every camera other than the main one.</summary>
    private void RebuildExtraCameraItems()
    {
        if (_cfg == null) return;
        var main = _cfg.GetInt("camera_index", 0);
        var extras = _cfg.GetIntList("extra_cameras");
        var available = _scannedCameras?.Select(c => (c.Index, Text: c.Label)).ToList()
                        ?? Enumerable.Range(0, 6).Select(i => (Index: i, Text: Loc.T("Camera.ItemSimple", i))).ToList();
        foreach (var e in extras.Where(e => available.All(a => a.Index != e)))
            available.Add((e, Loc.T("Camera.NotFound", e)));
        var wasLoading = _loading;
        _loading = true;
        foreach (var (combo, slot) in new[] { (Extra1Combo, 0), (Extra2Combo, 1) })
        {
            combo.Items.Clear();
            combo.Items.Add(new ComboBoxItem { Content = Loc.T("Camera.None"), Tag = -1 });
            foreach (var (index, text) in available.Where(a => a.Index != main))
                combo.Items.Add(new ComboBoxItem { Content = text, Tag = index });
            var selected = slot < extras.Count ? extras[slot] : -1;
            combo.SelectedItem = combo.Items.Cast<ComboBoxItem>().FirstOrDefault(i => (int)i.Tag == selected) ?? combo.Items[0];
        }
        _loading = wasLoading;
    }

    private async void ExtraCameraCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loading || _cfg == null) return;
        var main = _cfg.GetInt("camera_index", 0);
        var chosen = new[] { Extra1Combo, Extra2Combo }
            .Select(c => c.SelectedItem is ComboBoxItem i ? (int)i.Tag : -1)
            .Where(i => i >= 0 && i != main).Distinct().ToList();
        if (chosen.SequenceEqual(_cfg.GetIntList("extra_cameras"))) return;
        _cfg.Set("extra_cameras", new System.Text.Json.Nodes.JsonArray(chosen.Select(i => (System.Text.Json.Nodes.JsonNode?)i).ToArray()));
        SaveConfig();
        RebuildExtraCameraItems();
        WarnIfVirtual();
        await RestartEngineAsync("Log.ExtraCamerasChanged");
    }

    private void AssistantButton_Click(object sender, RoutedEventArgs e)
    {
        if (_cfg == null) return;
        if (_assistant != null)
        {
            _assistant.Activate();
            return;
        }
        var cameras = 1 + _cfg.GetIntList("extra_cameras").Count;
        _assistant = new SetupAssistantWindow(_engine, cameras, _cfg.GetDouble("camera_hfov", 60), v => SetLive("camera_hfov", Math.Round(v)))
        {
            Owner = this,
        };
        _assistant.Closed += (_, _) => _assistant = null;
        _assistant.Show();
        _assistant.SendMode();
    }

    /// <summary>Stores a setting the engine also uses, saves it shortly after, and pushes it live.</summary>
    private void SetLive(string key, object value)
    {
        if (_loading || _cfg == null) return;
        SetLocal(key, value);
        _engine.Send(new { cmd = "set", key, value });
    }

    /// <summary>Stores a setting only the UI uses.</summary>
    private void SetLocal(string key, object value)
    {
        if (_loading || _cfg == null) return;
        _cfg.Set(key, JsonValue.Create(JsonSerializer.SerializeToElement(value)));
        ScheduleSave();
    }

    private void ScheduleSave()
    {
        _saveTimer.Stop();
        _saveTimer.Start();
    }

    private void SaveConfig()
    {
        _saveTimer.Stop();
        try { _cfg?.Save(); }
        catch (IOException e) { Log(Loc.T("Log.SaveError", e.Message)); }
    }

    // ------------------------------------------------------------------ language

    private void LanguageCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loading || LanguageCombo.SelectedItem is not ComboBoxItem item) return;
        ApplyLanguage((string)item.Tag);
    }

    private void ApplyLanguage(string code)
    {
        if (code == Loc.Current) return;
        _loading = true;
        LanguageCombo.SelectedItem = LanguageCombo.Items.Cast<ComboBoxItem>().First(i => (string)i.Tag == code);
        _loading = false;
        Loc.Apply(code);
        SetLive("language", code);

        // Texts set from code are re-rendered here; XAML texts follow the dictionary by themselves.
        RebuildCameraItems();
        RefreshHotkeyTexts();
        RefreshDriverStatus();
        RenderPlaceholder();
        if (_engine.IsRunning)
            SetChip(EngineDot, EngineValue, Loc.T("Engine.Running"), Good);
        else
            ResetChips();
        if (_calibResultOk is bool ok && DateTime.Now < _calibResultUntil)
            ShowCalibResult(ok, keepTime: true, failSub: _calibFailSub);
    }

    // ------------------------------------------------------------------ engine lifecycle

    private void StartEngine()
    {
        if (_cfg == null || _engine.IsRunning) return;
        _userStopped = false;
        SaveConfig();
        try
        {
            _engine.Start(NoVrCheck.IsChecked == true);
        }
        catch (Exception e)
        {
            Log(Loc.T("Log.StartError", e.Message));
            return;
        }
        _lastStatus = DateTime.Now;
        _gotStatus = false;
        _watchdog.Start();
        SetRunningUi(true);
        SetPlaceholder("Placeholder.Starting");
        SetChip(EngineDot, EngineValue, Loc.T("Engine.Starting"), Warn);
    }

    private async Task StopEngineAsync()
    {
        StartButton.IsEnabled = false;
        await _engine.StopAsync();
        StartButton.IsEnabled = true;
    }

    private async Task RestartEngineAsync(string reasonKey)
    {
        if (!_engine.IsRunning) return;
        Log(Loc.T("Log.Restarting", Loc.T(reasonKey)));
        _restarting = true;
        await StopEngineAsync();
        _restarting = false;
        StartEngine();
    }

    private async void OnEngineExited(int code)
    {
        // During a restart the old process's exit can arrive after the new one has started.
        if (_restarting || _engine.IsRunning) return;
        _watchdog.Stop();
        _assistant?.EngineStopped();
        SetRunningUi(false);
        Preview.Source = null;
        CalibOverlay.Visibility = Visibility.Collapsed;
        ResetChips();
        if (code == 0 || _userStopped || _closeAllowed)
        {
            SetPlaceholder("Placeholder.Off");
            return;
        }

        while (_restartTimes.Count > 0 && DateTime.Now - _restartTimes.Peek() > RestartWindow)
            _restartTimes.Dequeue();
        if (_restartTimes.Count >= MaxRestarts)
        {
            Log(Loc.T("Log.AutoRestartGaveUp", _restartTimes.Count + 1));
            NotifyIfHidden("WebcamFBT", Loc.T("Log.AutoRestartGaveUp", _restartTimes.Count + 1));
            SetPlaceholder("Placeholder.Crashed", code);
            SetChip(EngineDot, EngineValue, Loc.T("Engine.Error"), Bad);
            return;
        }
        _restartTimes.Enqueue(DateTime.Now);
        Log(Loc.T("Log.AutoRestart", code, _restartTimes.Count, MaxRestarts));
        SetPlaceholder("Placeholder.Restarting");
        SetChip(EngineDot, EngineValue, Loc.T("Engine.Restarting"), Warn);
        await Task.Delay(TimeSpan.FromSeconds(2));
        if (!_userStopped && !_closeAllowed && !_engine.IsRunning)
            StartEngine();
    }

    private void CheckEngineAlive()
    {
        if (!_engine.IsRunning || _lastStatus == default) return;
        var silent = DateTime.Now - _lastStatus;
        if (silent > (_gotStatus ? HangTimeout : StartupTimeout))
        {
            // Killing it makes it exit with a non-zero code, which takes the restart path above.
            Log(Loc.T("Log.Hung", (int)silent.TotalSeconds));
            _lastStatus = DateTime.Now;
            _engine.Kill();
        }
        else if (silent > TimeSpan.FromSeconds(15))
            SetChip(EngineDot, EngineValue, Loc.T("Engine.NoResponse"), Bad);
    }

    private void SetRunningUi(bool running)
    {
        StartButton.SetResourceReference(ContentProperty, running ? "S.Control.Stop" : "S.Control.Start");
        CalibrateButton.IsEnabled = PauseButton.IsEnabled = RecordButton.IsEnabled = running;
        if (!running) { _recording = null; RecordButton.Content = Loc.T("Control.Record"); }
        YawLeftButton.IsEnabled = YawRightButton.IsEnabled = ScaleDownButton.IsEnabled = ScaleUpButton.IsEnabled = running;
        NoVrCheck.IsEnabled = ScanButton.IsEnabled = !running;
        InstallDriverButton.IsEnabled = UninstallDriverButton.IsEnabled = !running;
        _tray?.SetEngineRunning(running);
        if (!running) _tray?.SetStatus(Loc.T("Engine.Off"));
        if (!running)
        {
            _syncingPause = true;
            PauseButton.IsChecked = false;
            _syncingPause = false;
            ScaleText.Text = "";
        }
    }

    private bool PreviewWanted => ShowPreviewCheck.IsChecked == true && WindowState != WindowState.Minimized;

    /// <summary>Stops JPEG encoding in the engine and decoding here when nobody is looking.</summary>
    private void ApplyPreviewState()
    {
        if (!_engine.IsRunning) return;
        _engine.Send(new { cmd = "preview", value = PreviewWanted });
        if (!PreviewWanted)
        {
            Preview.Source = null;
            SetPlaceholder("Placeholder.PreviewOff");
        }
    }

    private void Window_StateChanged(object? sender, EventArgs e)
    {
        if (WindowState == WindowState.Minimized && MinimizeToTrayCheck.IsChecked == true)
        {
            Hide(); // keeps running in the notification area
            if (!_trayHintShown)
            {
                _trayHintShown = true;
                _tray?.ShowBalloon(Loc.T("Tray.MinimizedTitle"), Loc.T("Tray.MinimizedText"));
            }
        }
        ApplyPreviewState();
    }

    public void ShowFromTray()
    {
        Show();
        WindowState = WindowState.Normal;
        Activate();
        ApplyPreviewState();
    }

    /// <summary>Balloon from the tray icon when the window is hidden, so news is not missed.</summary>
    private void NotifyIfHidden(string title, string text)
    {
        if (!IsVisible) _tray?.ShowBalloon(title, text);
    }

    private void CheckUpdatesCheck_Click(object sender, RoutedEventArgs e) =>
        SetLocal("check_updates", CheckUpdatesCheck.IsChecked == true);

    private async void UpdateButton_Click(object sender, RoutedEventArgs e) => await CheckForUpdatesAsync(manual: true);

    /// <summary>Automatic checks stay quiet unless there is something new; manual ones always answer.</summary>
    private async Task CheckForUpdatesAsync(bool manual)
    {
        UpdateButton.IsEnabled = false;
        try
        {
            var repo = _cfg?.GetString("update_repository", UpdateChecker.DefaultRepository) ?? UpdateChecker.DefaultRepository;
            var result = await UpdateChecker.CheckAsync(repo);
            if (result.IsNewer)
            {
                Log(Loc.T("Update.Available", result.LatestVersion!, AppPaths.Version));
                _tray?.ShowBalloon("WebcamFBT", Loc.T("Update.Available", result.LatestVersion!, AppPaths.Version));
                if (MessageBox.Show(this, Loc.T("Update.Ask", result.LatestVersion!), "WebcamFBT", MessageBoxButton.YesNo,
                        MessageBoxImage.Information) == MessageBoxResult.Yes && result.Url != null)
                    System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(result.Url) { UseShellExecute = true });
            }
            else if (manual)
                Log(result.Available ? Loc.T("Update.UpToDate", AppPaths.Version) : Loc.T("Update.Unavailable"));
        }
        finally
        {
            UpdateButton.IsEnabled = true;
        }
    }

    private void MinimizeToTrayCheck_Click(object sender, RoutedEventArgs e) =>
        SetLocal("minimize_to_tray", MinimizeToTrayCheck.IsChecked == true);

    // ------------------------------------------------------------------ engine events

    private void OnFrame(BitmapSource frame)
    {
        // Drop frames while the UI is still drawing the previous one.
        if (Interlocked.Exchange(ref _framePending, 1) == 1) return;
        Dispatcher.BeginInvoke(DispatcherPriority.Render, () =>
        {
            if (PreviewWanted)
            {
                Preview.Source = frame;
                PlaceholderPanel.Visibility = Visibility.Collapsed;
            }
            Interlocked.Exchange(ref _framePending, 0);
        });
    }

    private void HandleMessage(JsonElement msg)
    {
        switch (msg.GetProperty("type").GetString())
        {
            case "status":
                UpdateStatus(msg);
                break;
            case "started":
                SetChip(EngineDot, EngineValue, Loc.T("Engine.Running"), Good);
                SetPlaceholder("Placeholder.Camera");
                ApplyPreviewState();
                _assistant?.SendMode();
                break;
            case "room":
            case "spot":
                _assistant?.Handle(msg);
                break;
            case "calibration":
                _recenterUntil = default;
                var moved = msg.TryGetProperty("reason", out var reason) && reason.ValueKind == JsonValueKind.String
                            && reason.GetString() == "calib_moved";
                ShowCalibResult(msg.GetProperty("ok").GetBoolean(), failSub: moved ? "Overlay.FailMovedSub" : "Overlay.FailSub");
                if (msg.TryGetProperty("cameras", out var camResults) && msg.GetProperty("ok").GetBoolean())
                {
                    var failed = camResults.EnumerateObject().Where(p => !p.Value.GetBoolean()).Select(p => p.Name).ToList();
                    if (failed.Count > 0)
                        ShowOverlay(Loc.T("Overlay.OkTitle"), Loc.T("Overlay.OkPartial", string.Join(", ", failed)));
                }
                break;
            case "recenter":
                _recenterUntil = DateTime.Now.AddSeconds(15);
                _calibResultUntil = DateTime.Now.AddSeconds(5);
                ShowOverlay(Loc.T("Overlay.RecenterTitle"), Loc.T("Overlay.RecenterMaybe", CalibrateKey));
                NotifyIfHidden(Loc.T("Overlay.RecenterTitle"), Loc.T("Overlay.RecenterMaybe", CalibrateKey));
                break;
            case "log":
                Log(msg.GetProperty("msg").GetString() ?? "");
                break;
            case "error":
                Log(Loc.T("Log.Error", msg.GetProperty("msg").GetString() ?? ""));
                break;
        }
    }

    private void UpdateStatus(JsonElement s)
    {
        _lastStatus = DateTime.Now;
        _gotStatus = true;
        UpdateRecording(s);
        SetChip(EngineDot, EngineValue, Loc.T("Engine.Running"), Good);

        var fps = s.GetProperty("fps").GetDouble();
        var cameraState = s.TryGetProperty("camera_state", out var camEl) ? camEl.GetString() : "ok";
        var cams = s.TryGetProperty("cameras", out var camsEl) ? camsEl.EnumerateArray().ToList() : [];
        var badCam = cams.FirstOrDefault(c => c.GetProperty("state").GetString() != "ok");
        if (cams.Count > 1 && badCam.ValueKind == JsonValueKind.Object)
            SetChip(CameraDot, CameraValue, Loc.T("Camera.LostN", badCam.GetProperty("index").GetInt32()), Bad);
        else if (cams.Count > 1)
        {
            var minFps = cams.Min(c => c.GetProperty("fps").GetDouble());
            var fpsText = string.Join("/", cams.Select(c => c.GetProperty("fps").GetDouble().ToString("0")));
            SetChip(CameraDot, CameraValue, Loc.T("Camera.Multi", cams.Count, fpsText + " FPS"), minFps >= 18 ? Good : minFps >= 10 ? Warn : Bad);
        }
        else if (cameraState == "no_frames")
            SetChip(CameraDot, CameraValue, Loc.T("Camera.NoFrames"), Bad);
        else if (cameraState == "lost")
            SetChip(CameraDot, CameraValue, Loc.T("Camera.Lost"), Bad);
        else
            SetChip(CameraDot, CameraValue, Loc.T("Camera.Fps", fps), fps >= 18 ? Good : fps >= 10 ? Warn : Bad);

        if (s.TryGetProperty("infer_ms", out var inferEl) && inferEl.GetDouble() > 0)
        {
            var ms = inferEl.GetDouble();
            SetChip(ModelDot, ModelValue, Loc.T("Model.Ms", ms), ms < 50 ? Good : ms < 90 ? Warn : Bad);
        }

        var person = s.GetProperty("person").GetBoolean();
        SetChip(PersonDot, PersonValue, Loc.T(person ? "Person.Yes" : "Person.No"), person ? Good : Bad);

        _vrState = s.GetProperty("vr").GetString() ?? "off";
        var driverState = s.TryGetProperty("driver", out var drvEl) ? drvEl.GetString() : "unknown";
        switch (_vrState)
        {
            case "connected" when driverState == "not_loaded":
                SetChip(VrDot, VrValue, Loc.T("Vr.DriverMissing"), Bad);
                break;
            case "connected" when driverState == "port_busy":
                SetChip(VrDot, VrValue, Loc.T("Vr.PortBusy"), Bad);
                break;
            case "connected" when driverState == "outdated":
                SetChip(VrDot, VrValue, Loc.T("Vr.DriverOutdated"), Warn);
                break;
            case "connected":
                var hmd = s.GetProperty("hmd").GetBoolean();
                var hs = s.TryGetProperty("headset", out var hsEl) && hsEl.ValueKind == JsonValueKind.Object ? hsEl : (JsonElement?)null;
                var name = hs?.GetProperty("name").GetString();
                SetChip(VrDot, VrValue, !hmd ? Loc.T("Vr.NoHmd") : name != null ? Loc.T("Vr.ConnectedTo", name) : Loc.T("Vr.Connected"),
                    hmd ? Good : Warn);
                if (hs is JsonElement h)
                {
                    var family = h.GetProperty("family").GetString();
                    var connection = h.GetProperty("connection").GetString();
                    var how = connection == "virtual_desktop" ? "Recenter.vd" : family is "quest" or "rift" ? $"Recenter.{family}" : "Recenter.generic";
                    VrValue.ToolTip = Loc.T("Vr.Tooltip", name ?? "", Loc.T($"Conn.{connection}"), Loc.T(how));
                }
                break;
            case "waiting":
                SetChip(VrDot, VrValue, Loc.T("Vr.Waiting"), Warn);
                break;
            default:
                SetChip(VrDot, VrValue, Loc.T("Vr.Off"), Idle);
                break;
        }

        var calibrated = s.GetProperty("calibrated").GetBoolean();
        var saved = s.TryGetProperty("calib_saved", out var savedEl) && savedEl.GetBoolean();
        var phase = s.GetProperty("calib_phase").ValueKind == JsonValueKind.String ? s.GetProperty("calib_phase").GetString() : null;
        var remaining = s.GetProperty("calib_remaining").GetDouble();
        if (phase != null)
            SetChip(CalibDot, CalibValue, Loc.T("Calib.Running"), Warn);
        else if (calibrated && DateTime.Now < _recenterUntil)
            SetChip(CalibDot, CalibValue, Loc.T("Calib.Check", CalibrateKey), Warn);
        else if (calibrated && cams.Count > 1)
        {
            var ready = s.TryGetProperty("calibrated_count", out var rc) ? rc.GetInt32() : cams.Count;
            SetChip(CalibDot, CalibValue, Loc.T("Calib.ReadyMulti", ready, cams.Count), ready == cams.Count ? Good : Warn);
        }
        else if (calibrated)
            SetChip(CalibDot, CalibValue, Loc.T(saved ? "Calib.ReadySaved" : "Calib.Ready"), Good);
        else
            SetChip(CalibDot, CalibValue, Loc.T("Calib.None", CalibrateKey), Warn);
        ScaleText.Text = calibrated ? s.GetProperty("scale").GetDouble().ToString("0.00") : "";

        var paused = s.GetProperty("paused").GetBoolean();
        var sent = s.GetProperty("sent").GetInt32();
        if (paused)
            SetChip(SentDot, SentValue, Loc.T("Sent.Paused"), Warn);
        else
            SetChip(SentDot, SentValue, sent > 0 ? Loc.T("Sent.Count", sent) : Loc.T("Sent.None"), sent > 0 ? Good : Idle);
        if (DateTime.Now - _lastTrayStatus > TimeSpan.FromSeconds(1))
        {
            _lastTrayStatus = DateTime.Now;
            _tray?.SetStatus($"{EngineValue.Text} · {CalibValue.Text} · {SentValue.Text}");
        }
        if (PauseButton.IsChecked != paused)
        {
            _syncingPause = true;
            PauseButton.IsChecked = paused;
            _syncingPause = false;
        }

        // Calibration overlay on the preview
        if (phase == "countdown")
            ShowOverlay($"{Math.Ceiling(remaining):0}", Loc.T("Overlay.Countdown"));
        else if (phase == "collect")
            ShowOverlay(Loc.T("Overlay.Collect"), Loc.T("Overlay.CollectSub"));
        else if (DateTime.Now > _calibResultUntil && _vrState == "connected" && s.GetProperty("hmd").GetBoolean() && !calibrated)
            ShowOverlay(Loc.T("Overlay.NeedCalibTitle"),
                        Loc.T(AutoCalibCheck.IsChecked == true ? "Overlay.NeedCalibAutoSub" : "Overlay.NeedCalibSub", CalibrateKey));
        else if (DateTime.Now > _calibResultUntil)
            CalibOverlay.Visibility = Visibility.Collapsed;
    }

    private void ShowCalibResult(bool ok, bool keepTime = false, string failSub = "Overlay.FailSub")
    {
        _calibFailSub = failSub;
        if (!keepTime)
            NotifyIfHidden(Loc.T(ok ? "Overlay.OkTitle" : "Overlay.FailTitle"), Loc.T(ok ? "Overlay.OkSub" : failSub));
        _calibResultOk = ok;
        if (!keepTime)
            _calibResultUntil = DateTime.Now.AddSeconds(ok ? 2 : 4);
        if (ok)
            ShowOverlay(Loc.T("Overlay.OkTitle"), Loc.T("Overlay.OkSub"));
        else
            ShowOverlay(Loc.T("Overlay.FailTitle"), Loc.T(_calibFailSub));
    }

    private void ShowOverlay(string title, string subtitle)
    {
        CalibTitle.Text = title;
        CalibSubtitle.Text = subtitle;
        CalibOverlay.Visibility = Visibility.Visible;
    }

    private void SetPlaceholder(string key, params object[] args)
    {
        _placeholder = (key, args);
        RenderPlaceholder();
        PlaceholderPanel.Visibility = Visibility.Visible;
    }

    private void RenderPlaceholder() => PlaceholderText.Text = Loc.T(_placeholder.Key, _placeholder.Args);

    private void ResetChips()
    {
        SetChip(EngineDot, EngineValue, Loc.T("Engine.Off"), Idle);
        foreach (var (dot, value) in new[] { (CameraDot, CameraValue), (ModelDot, ModelValue), (VrDot, VrValue),
                                             (PersonDot, PersonValue), (CalibDot, CalibValue), (SentDot, SentValue) })
            SetChip(dot, value, "—", Idle);
    }

    private static void SetChip(Ellipse dot, TextBlock value, string text, Brush color)
    {
        dot.Fill = color;
        value.Text = text;
    }

    private void Log(string line)
    {
        FileLog.Write(line);
        if (LogBox.Text.Length > 40000)
            LogBox.Text = LogBox.Text[^20000..];
        LogBox.AppendText($"[{DateTime.Now:HH:mm:ss}] {line}{Environment.NewLine}");
        LogBox.ScrollToEnd();
    }

    private void Fail(string message)
    {
        _loading = false;
        PlaceholderText.Text = message;
        StartButton.IsEnabled = false;
        Log(message);
    }

    // ------------------------------------------------------------------ control handlers

    private async void StartButton_Click(object sender, RoutedEventArgs e)
    {
        if (_engine.IsRunning)
        {
            _userStopped = true;
            await StopEngineAsync();
        }
        else
        {
            _restartTimes.Clear();
            StartEngine();
        }
    }

    private void CalibrateButton_Click(object sender, RoutedEventArgs e)
    {
        if (_vrState == "waiting")
            Log(Loc.T("Log.NoVrWarning"));
        _engine.Send(new { cmd = "calibrate" });
    }

    private string? _recording;  // engine's recorder state: null, "waiting" or "recording"

    private void RecordButton_Click(object sender, RoutedEventArgs e)
    {
        // A second press cancels the wait or stops (and saves) the recording early.
        if (_recording != null)
            _engine.Send(new { cmd = "record", value = false });
        else
            _engine.Send(new { cmd = "record", value = true, seconds = 60, delay = 15 });
    }

    private void UpdateRecording(JsonElement s)
    {
        _recording = s.TryGetProperty("recording", out var r) && r.ValueKind == JsonValueKind.String ? r.GetString() : null;
        var left = s.TryGetProperty("recording_left", out var l) ? l.GetInt32() : 0;
        RecordButton.Content = _recording switch
        {
            "waiting" => Loc.T("Control.RecordWaiting", left),
            "recording" => Loc.T("Control.RecordRunning", left),
            _ => Loc.T("Control.Record"),
        };
    }

    private void PauseButton_Click(object sender, RoutedEventArgs e)
    {
        if (_syncingPause) return;
        _engine.Send(new { cmd = "pause", value = PauseButton.IsChecked == true });
    }

    private void YawButton_Click(object sender, RoutedEventArgs e) =>
        _engine.Send(new { cmd = "yaw", delta = double.Parse((string)((Button)sender).Tag, CultureInfo.InvariantCulture) });

    private void ScaleButton_Click(object sender, RoutedEventArgs e) =>
        _engine.Send(new { cmd = "scale", factor = double.Parse((string)((Button)sender).Tag, CultureInfo.InvariantCulture) });

    private void TrackerCheck_Click(object sender, RoutedEventArgs e)
    {
        if (_loading || _cfg == null) return;
        var check = (CheckBox)sender;
        var name = (string)check.Tag;
        var value = check.IsChecked == true;
        _cfg.SetTracker(name, value);
        ScheduleSave();
        _engine.Send(new { cmd = "tracker", name, value });
    }

    private async void CameraCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loading || _cfg == null || CameraCombo.SelectedItem is not ComboBoxItem item) return;
        var index = (int)item.Tag;
        if (_cfg.GetInt("camera_index", 0) == index) return;
        _cfg.Set("camera_index", index);
        WarnIfVirtual();
        var extras = _cfg.GetIntList("extra_cameras").Where(i => i != index).ToList();
        _cfg.Set("extra_cameras", new System.Text.Json.Nodes.JsonArray(extras.Select(i => (System.Text.Json.Nodes.JsonNode?)i).ToArray()));
        SaveConfig();
        RebuildExtraCameraItems();
        await RestartEngineAsync("Log.CameraChanged");
    }

    private async void ModelCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loading || _cfg == null || ModelCombo.SelectedItem is not ComboBoxItem item) return;
        var model = (string)item.Tag;
        if (_cfg.GetString("model", "full") == model) return;
        _cfg.Set("model", model);
        SaveConfig();
        await RestartEngineAsync("Log.ModelChanged");
    }

    /// <summary>Names only (no camera is opened), so it is safe while the engine runs.</summary>
    private async Task LoadCameraNamesAsync()
    {
        if (_cfg == null) return;
        try
        {
            var found = await Engine.ScanCamerasAsync(probe: false);
            if (found.Count == 0 || _scannedCameras != null) return;
            _scannedCameras = found;
            RebuildCameraItems();
            WarnIfVirtual();
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or IOException or JsonException) { }
    }

    /// <summary>Virtual cameras (OBS, Meta Quest Link...) never show the player: say so when one is picked.</summary>
    private void WarnIfVirtual()
    {
        if (_cfg == null || _scannedCameras == null) return;
        var chosen = new[] { _cfg.GetInt("camera_index", 0) }.Concat(_cfg.GetIntList("extra_cameras"));
        foreach (var cam in _scannedCameras.Where(c => c.IsVirtual && chosen.Contains(c.Index)))
            Log(Loc.T("Log.VirtualCamera", cam.Name ?? cam.Index.ToString()));
    }

    private async void ScanButton_Click(object sender, RoutedEventArgs e)
    {
        ScanButton.IsEnabled = StartButton.IsEnabled = false;
        ScanButton.SetResourceReference(ContentProperty, "S.Camera.Scanning");
        Log(Loc.T("Log.ScanStart"));
        try
        {
            _scannedCameras = await Engine.ScanCamerasAsync(probe: true);
            RebuildCameraItems();
            Log(Loc.T("Log.ScanDone", _scannedCameras.Count));
        }
        catch (Exception ex)
        {
            Log(Loc.T("Log.ScanFailed", ex.Message));
        }
        finally
        {
            ScanButton.SetResourceReference(ContentProperty, "S.Camera.Scan");
            ScanButton.IsEnabled = StartButton.IsEnabled = true;
        }
    }

    private void AutoStartCheck_Click(object sender, RoutedEventArgs e) => SetLocal("auto_start", AutoStartCheck.IsChecked == true);

    // ------------------------------------------------------------------ hotkeys

    // Same set the engine accepts (parse_hotkey in webcam_fbt.py): F1-F12, optionally with Ctrl.
    private static readonly string[] HotkeyChoices =
        Enumerable.Range(1, 12).Select(i => $"F{i}").Concat(Enumerable.Range(1, 12).Select(i => $"Ctrl+F{i}")).ToArray();

    private string CalibrateKey => _cfg?.GetString("hotkey_calibrate", "F8") ?? "F8";
    private string PauseKey => _cfg?.GetString("hotkey_pause", "F9") ?? "F9";

    private static void FillHotkeyCombo(ComboBox combo, string current)
    {
        combo.Items.Clear();
        foreach (var key in HotkeyChoices.Contains(current) ? HotkeyChoices : HotkeyChoices.Prepend(current))
            combo.Items.Add(key);
        combo.SelectedItem = current;
    }

    private void RefreshHotkeyTexts()
    {
        CalibrateButton.Content = Loc.T("Control.Calibrate", CalibrateKey);
        PauseButton.Content = Loc.T("Control.Pause", PauseKey);
        FooterText.Text = Loc.T("Footer", CalibrateKey, PauseKey);
        _tray?.SetHotkeys(CalibrateKey, PauseKey);
    }

    private async void HotkeyCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loading || _cfg == null) return;
        var combo = (ComboBox)sender;
        var key = (string)combo.Tag;
        var value = combo.SelectedItem as string;
        var other = combo == HotkeyCalibrateCombo ? PauseKey : CalibrateKey;
        if (value == null || value == _cfg.GetString(key, "")) return;
        if (value == other)
        {
            Log(Loc.T("Log.HotkeyDuplicate"));
            _loading = true;
            combo.SelectedItem = _cfg.GetString(key, combo == HotkeyCalibrateCombo ? "F8" : "F9");
            _loading = false;
            return;
        }
        _cfg.Set(key, value);
        SaveConfig();
        RefreshHotkeyTexts();
        await RestartEngineAsync("Log.HotkeysChanged"); // hotkeys are registered when the engine starts
    }

    private void WizardButton_Click(object sender, RoutedEventArgs e) => OpenWizard();

    private void OpenWizard()
    {
        if (_cfg == null) return;
        var host = new WizardHost(
            ApplyLanguage: ApplyLanguage,
            MainCamera: () => _cfg.GetInt("camera_index", 0),
            SelectMainCamera: SelectMainCamera,
            ScanCameras: async probe =>
            {
                if (probe && _engine.IsRunning) await StopEngineAsync(); // probing needs the cameras free
                var cams = await Engine.ScanCamerasAsync(probe);
                _scannedCameras = cams;
                RebuildCameraItems();
                return cams;
            },
            AutoStart: () => _cfg.GetBool("auto_start", false),
            SetAutoStart: value =>
            {
                AutoStartCheck.IsChecked = value;
                SetLocal("auto_start", value);
            },
            CalibrateKey: () => CalibrateKey,
            Finish: startNow =>
            {
                SetLocal("setup_done", true);
                SaveConfig();
                if (startNow && !_engine.IsRunning) StartEngine();
            });
        new FirstRunWindow(host) { Owner = this }.ShowDialog();
    }

    private void SelectMainCamera(int index)
    {
        if (_cfg == null || _cfg.GetInt("camera_index", 0) == index) return;
        _cfg.Set("camera_index", index);
        var extras = _cfg.GetIntList("extra_cameras").Where(i => i != index).ToList();
        _cfg.Set("extra_cameras", new System.Text.Json.Nodes.JsonArray(extras.Select(i => (System.Text.Json.Nodes.JsonNode?)i).ToArray()));
        SaveConfig();
        RebuildCameraItems();
        WarnIfVirtual();
    }

    private void OpenLogsButton_Click(object sender, RoutedEventArgs e)
    {
        if (FileLog.Directory == null) return;
        Directory.CreateDirectory(FileLog.Directory);
        System.Diagnostics.Process.Start("explorer.exe", FileLog.Directory);
    }

    private void RememberCalibCheck_Click(object sender, RoutedEventArgs e) =>
        SetLive("remember_calibration", RememberCalibCheck.IsChecked == true);

    private void AutoCalibCheck_Click(object sender, RoutedEventArgs e) =>
        SetLive("auto_calibrate", AutoCalibCheck.IsChecked == true);

    private void GestureCalibCheck_Click(object sender, RoutedEventArgs e) =>
        SetLive("gesture_calibrate", GestureCalibCheck.IsChecked == true);

    private void ShowPreviewCheck_Click(object sender, RoutedEventArgs e)
    {
        SetLocal("show_preview", ShowPreviewCheck.IsChecked == true);
        ApplyPreviewState();
    }

    private void MirrorCheck_Click(object sender, RoutedEventArgs e) => SetLive("mirror_preview", MirrorCheck.IsChecked == true);

    private void FloorLockCheck_Click(object sender, RoutedEventArgs e) => SetLive("floor_lock", FloorLockCheck.IsChecked == true);

    private void FootLockCheck_Click(object sender, RoutedEventArgs e) => SetLive("foot_lock", FootLockCheck.IsChecked == true);

    private void SmoothSlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e) =>
        SetLive("filter_min_cutoff", Math.Round(e.NewValue, 2));

    private void BetaSlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e) =>
        SetLive("filter_beta", Math.Round(e.NewValue, 2));

    private void DepthSlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e) =>
        SetLive("depth_smoothing", Math.Round(e.NewValue, 2));

    private void VisibilitySlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (VisibilityText != null) VisibilityText.Text = e.NewValue.ToString("0.00");
        SetLive("min_visibility", Math.Round(e.NewValue, 2));
    }

    // ------------------------------------------------------------------ driver

    private void RefreshDriverStatus()
    {
        if (AppPaths.Root == null) return;
        var installed = DriverManager.IsInstalled();
        DriverDot.Fill = installed ? Good : Bad;
        DriverStatusText.Text = Loc.T(installed ? "Driver.Installed" : "Driver.NotInstalled");
    }

    private async void InstallDriverButton_Click(object sender, RoutedEventArgs e)
    {
        if (DriverManager.IsSteamVrRunning())
        {
            MessageBox.Show(this, Loc.T("Driver.CloseSteamVr"), "WebcamFBT", MessageBoxButton.OK, MessageBoxImage.Information);
            return;
        }
        await RunDriverAction(DriverManager.InstallAsync, "Driver.InstallDone");
    }

    private async void UninstallDriverButton_Click(object sender, RoutedEventArgs e)
    {
        if (DriverManager.IsSteamVrRunning())
        {
            MessageBox.Show(this, Loc.T("Driver.CloseSteamVr"), "WebcamFBT", MessageBoxButton.OK, MessageBoxImage.Information);
            return;
        }
        if (MessageBox.Show(this, Loc.T("Driver.ConfirmUninstall"), "WebcamFBT",
                MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes)
            return;
        await RunDriverAction(DriverManager.UninstallAsync, "Driver.UninstallDone");
    }

    private async Task RunDriverAction(Func<Task<string>> action, string doneKey)
    {
        InstallDriverButton.IsEnabled = UninstallDriverButton.IsEnabled = false;
        try
        {
            var output = await action();
            if (!string.IsNullOrWhiteSpace(output)) Log(output);
            Log(Loc.T(doneKey));
        }
        catch (Exception ex)
        {
            Log(Loc.T("Driver.Failed", ex.Message));
        }
        finally
        {
            InstallDriverButton.IsEnabled = UninstallDriverButton.IsEnabled = !_engine.IsRunning;
            RefreshDriverStatus();
        }
    }

    // ------------------------------------------------------------------ closing

    protected override void OnClosed(EventArgs e)
    {
        _tray?.Dispose();
        base.OnClosed(e);
    }

    protected override async void OnClosing(CancelEventArgs e)
    {
        if (_closeAllowed || !_engine.IsRunning)
        {
            SaveConfig();
            base.OnClosing(e);
            return;
        }
        // Let the engine release the camera and SteamVR before the window goes away.
        e.Cancel = true;
        IsEnabled = false;
        await _engine.StopAsync();
        _closeAllowed = true;
        Close();
    }
}
