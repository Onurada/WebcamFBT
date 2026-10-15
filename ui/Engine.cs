using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows.Media.Imaging;

namespace WebcamFBT;

/// <summary>
/// Runs tracker/webcam_fbt.py --ipc as a hidden child process.
/// Commands go out as JSON lines on stdin, events come back as JSON lines on stdout.
/// If this app dies, the engine sees stdin close and exits by itself.
/// </summary>
public sealed class Engine
{
    private readonly object _sendLock = new();
    private Process? _process;

    /// <summary>Any non-frame event (status, log, calibration, ...). Raised on a background thread.</summary>
    public event Action<JsonElement>? Message;
    /// <summary>Decoded, frozen preview frame. Raised on a background thread.</summary>
    public event Action<BitmapSource>? FrameReady;
    public event Action<string>? ErrorOutput;
    /// <summary>Exit code. Raised on a background thread.</summary>
    public event Action<int>? Exited;

    public bool IsRunning => _process is { HasExited: false };

    public void Start(bool noVr)
    {
        if (IsRunning) return;
        var psi = new ProcessStartInfo(AppPaths.Python)
        {
            WorkingDirectory = AppPaths.Root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        psi.ArgumentList.Add("-u");
        psi.ArgumentList.Add(AppPaths.EngineScript);
        psi.ArgumentList.Add("--ipc");
        if (noVr) psi.ArgumentList.Add("--no-vr");

        var p = new Process { StartInfo = psi, EnableRaisingEvents = true };
        p.Exited += (_, _) => Exited?.Invoke(p.ExitCode);
        p.Start();
        _process = p;
        _ = Task.Run(() => ReadEvents(p.StandardOutput));
        _ = Task.Run(() => ReadErrors(p.StandardError));
    }

    public void Send(object command)
    {
        if (!IsRunning) return;
        lock (_sendLock)
        {
            try
            {
                _process!.StandardInput.WriteLine(JsonSerializer.Serialize(command));
                _process.StandardInput.Flush();
            }
            catch (IOException) { } // engine is exiting
        }
    }

    public async Task StopAsync()
    {
        var p = _process;
        if (p == null || p.HasExited) return;
        Send(new { cmd = "quit" });
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        try { await p.WaitForExitAsync(timeout.Token); }
        catch (OperationCanceledException) { p.Kill(entireProcessTree: true); }
    }

    public void Kill()
    {
        try { if (IsRunning) _process!.Kill(entireProcessTree: true); }
        catch (InvalidOperationException) { }
    }

    private async Task ReadEvents(StreamReader reader)
    {
        string? line;
        while ((line = await reader.ReadLineAsync()) != null)
        {
            JsonElement msg;
            try
            {
                using var doc = JsonDocument.Parse(line);
                msg = doc.RootElement.Clone();
            }
            catch (JsonException) { continue; }

            if (msg.GetProperty("type").GetString() == "frame")
                FrameReady?.Invoke(DecodeJpeg(msg.GetProperty("jpg").GetString()!));
            else
                Message?.Invoke(msg);
        }
    }

    private async Task ReadErrors(StreamReader reader)
    {
        string? line;
        while ((line = await reader.ReadLineAsync()) != null)
            ErrorOutput?.Invoke(line);
    }

    private static BitmapSource DecodeJpeg(string base64)
    {
        var bmp = new BitmapImage();
        bmp.BeginInit();
        bmp.CacheOption = BitmapCacheOption.OnLoad;
        bmp.StreamSource = new MemoryStream(Convert.FromBase64String(base64));
        bmp.EndInit();
        bmp.Freeze();
        return bmp;
    }

    /// <summary>
    /// Lists the cameras by name via tracker/cameras.py (no MediaPipe, so it is quick).
    /// With probe the real cameras are also opened to check them, which needs them to be free.
    /// </summary>
    public static async Task<List<CameraInfo>> ScanCamerasAsync(bool probe)
    {
        var psi = new ProcessStartInfo(AppPaths.Python)
        {
            WorkingDirectory = AppPaths.Root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
        };
        psi.ArgumentList.Add(AppPaths.CamerasScript);
        if (probe) psi.ArgumentList.Add("--probe");

        using var p = Process.Start(psi)!;
        _ = p.StandardError.ReadToEndAsync();
        var result = new List<CameraInfo>();
        string? line;
        while ((line = await p.StandardOutput.ReadLineAsync()) != null)
        {
            using var doc = JsonDocument.Parse(line);
            if (doc.RootElement.GetProperty("type").GetString() != "cameras") continue;
            foreach (var c in doc.RootElement.GetProperty("list").EnumerateArray())
            {
                int? Int(string name) => c.GetProperty(name).ValueKind == JsonValueKind.Number ? c.GetProperty(name).GetInt32() : null;
                bool? Bool(string name) => c.GetProperty(name).ValueKind is JsonValueKind.True or JsonValueKind.False ? c.GetProperty(name).GetBoolean() : null;
                result.Add(new CameraInfo(c.GetProperty("index").GetInt32(),
                    c.GetProperty("name").ValueKind == JsonValueKind.String ? c.GetProperty("name").GetString() : null,
                    c.GetProperty("kind").GetString() ?? "webcam", Int("width"), Int("height"), Bool("working")));
            }
        }
        await p.WaitForExitAsync();
        return result;
    }
}

/// <summary>A video device. Kind is "webcam", "phone" (phone-as-webcam app) or "virtual" (OBS, Meta Quest Link...).</summary>
public sealed record CameraInfo(int Index, string? Name, string Kind, int? Width, int? Height, bool? Working)
{
    public bool IsVirtual => Kind == "virtual";

    public string Label
    {
        get
        {
            var text = Name ?? Loc.T("Camera.ItemSimple", Index);
            if (Kind == "virtual") text += " · " + Loc.T("Camera.Virtual");
            else if (Kind == "phone") text += " · " + Loc.T("Camera.Phone");
            if (Width is int w && Height is int h) text += $" · {w}×{h}";
            if (Working == false) text += " · " + Loc.T("Camera.NotWorking");
            return text;
        }
    }
}
