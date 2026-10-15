using System.IO;
using System.Threading;
using System.Windows;

namespace WebcamFBT;

/// <summary>
/// Startup: one running copy at a time (a second launch brings the first one to the front), and
/// two command-line actions the installer uses:
///   --install-driver    register the SteamVR driver and tracker roles, then exit
///   --uninstall-driver  remove them again, then exit
/// </summary>
public partial class App : Application
{
    // Inno Setup's AppMutex uses this name to ask the user to close the app before (un)installing.
    public const string MutexName = "WebcamFBT.App";
    private const string ShowEventName = "WebcamFBT.Show";
    private Mutex? _mutex;
    private EventWaitHandle? _showEvent;

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        Loc.Apply(ConfiguredLanguage());

        if (e.Args.Contains("--install-driver") || e.Args.Contains("--uninstall-driver"))
        {
            ShutdownMode = ShutdownMode.OnExplicitShutdown;
            await RunDriverAction(install: e.Args.Contains("--install-driver"));
            Shutdown(0);
            return;
        }

        _mutex = new Mutex(true, MutexName, out var first);
        _showEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ShowEventName);
        if (!first)
        {
            _showEvent.Set(); // the running copy shows its window
            Shutdown(0);
            return;
        }

        var window = new MainWindow();
        MainWindow = window;
        window.Show();
        new Thread(() =>
        {
            while (_showEvent.WaitOne())
                Dispatcher.BeginInvoke(window.ShowFromTray);
        }) { IsBackground = true }.Start();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _mutex?.Dispose();
        base.OnExit(e);
    }

    private static string ConfiguredLanguage()
    {
        try
        {
            if (AppPaths.Root != null && File.Exists(AppPaths.ConfigFile))
            {
                var lang = new ConfigStore(AppPaths.ConfigFile).GetString("language", "");
                if (lang.Length > 0) return lang;
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        return Loc.SystemDefault();
    }

    /// <summary>Installer hooks. Never fail the (un)install: problems are only shown.</summary>
    private static async Task RunDriverAction(bool install)
    {
        if (AppPaths.Root == null) return;
        if (DriverManager.IsSteamVrRunning())
        {
            MessageBox.Show(Loc.T(install ? "Setup.SteamVrOpenInstall" : "Setup.SteamVrOpenUninstall"), "WebcamFBT",
                MessageBoxButton.OK, MessageBoxImage.Information);
            return;
        }
        try
        {
            if (install) await DriverManager.InstallAsync();
            else await DriverManager.UninstallAsync();
        }
        catch (Exception ex)
        {
            MessageBox.Show(Loc.T("Driver.Failed", ex.Message), "WebcamFBT", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
    }
}
