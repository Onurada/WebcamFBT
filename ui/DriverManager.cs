using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json.Nodes;

namespace WebcamFBT;

/// <summary>Registers / removes the webcamfbt SteamVR driver (same as install_driver.bat).</summary>
public static class DriverManager
{
    private static string VrPathFile =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "openvr", "openvrpaths.vrpath");

    public static bool IsSteamVrRunning() => Process.GetProcessesByName("vrserver").Length > 0;

    public static bool IsInstalled()
    {
        var target = Path.GetFullPath(AppPaths.DriverDir).TrimEnd('\\');
        return ReadPaths("external_drivers").Any(d => string.Equals(
            Path.GetFullPath(d).TrimEnd('\\'), target, StringComparison.OrdinalIgnoreCase));
    }

    public static async Task<string> InstallAsync()
    {
        var log = new StringBuilder();
        log.AppendLine(await RunAsync(VrPathReg(), "adddriver", AppPaths.DriverDir));
        var settings = Path.Combine(ReadPaths("config").FirstOrDefault() ?? Path.Combine(SteamPath(), "config"), "steamvr.vrsettings");
        log.AppendLine(await RunAsync(AppPaths.Python, AppPaths.SetRolesScript, settings));
        return log.ToString().Trim();
    }

    public static async Task<string> UninstallAsync()
    {
        var log = new StringBuilder();
        log.AppendLine(await RunAsync(VrPathReg(), "removedriver", AppPaths.DriverDir));
        // The roles are shared by every copy of the driver (same serials): keep them while another
        // WebcamFBT driver (e.g. a developer build) is still registered.
        var others = ReadPaths("external_drivers").Any(d =>
            Path.GetFileName(d.TrimEnd('\\', '/')).Equals("webcamfbt", StringComparison.OrdinalIgnoreCase));
        if (!others)
        {
            var settings = Path.Combine(ReadPaths("config").FirstOrDefault() ?? Path.Combine(SteamPath(), "config"), "steamvr.vrsettings");
            log.AppendLine(await RunAsync(AppPaths.Python, AppPaths.SetRolesScript, "--remove", settings));
        }
        return log.ToString().Trim();
    }

    private static string VrPathReg()
    {
        var runtime = ReadPaths("runtime").FirstOrDefault() ?? Path.Combine(SteamPath(), "steamapps", "common", "SteamVR");
        return Path.Combine(runtime, "bin", "win64", "vrpathreg.exe");
    }

    /// <summary>Steam's install folder from the registry (fallback: the default location).</summary>
    private static string SteamPath()
    {
        using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam");
        return key?.GetValue("SteamPath") is string path ? Path.GetFullPath(path) : @"C:\Program Files (x86)\Steam";
    }

    private static IEnumerable<string> ReadPaths(string key)
    {
        try
        {
            if (JsonNode.Parse(File.ReadAllText(VrPathFile))?[key] is JsonArray arr)
                return arr.Select(n => n?.GetValue<string>()).OfType<string>().ToList();
        }
        catch (Exception e) when (e is IOException or System.Text.Json.JsonException or UnauthorizedAccessException) { }
        return [];
    }

    private static async Task<string> RunAsync(string exe, params string[] args)
    {
        var psi = new ProcessStartInfo(exe)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var a in args) psi.ArgumentList.Add(a);
        using var p = Process.Start(psi)!;
        var stdout = p.StandardOutput.ReadToEndAsync();
        var stderr = p.StandardError.ReadToEndAsync();
        await p.WaitForExitAsync();
        var output = ((await stdout) + (await stderr)).Trim();
        if (p.ExitCode != 0)
            throw new InvalidOperationException($"{Path.GetFileName(exe)} hata kodu {p.ExitCode}: {output}");
        return output;
    }
}
