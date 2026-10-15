using System.IO;

namespace WebcamFBT;

/// <summary>Locations of the Python engine, config and driver, relative to the WebcamFBT root folder.</summary>
public static class AppPaths
{
    public static string? Root { get; } = FindRoot();

    public static string Python => Path.Combine(Root!, ".venv", "Scripts", "python.exe");
    public static string EngineScript => Path.Combine(Root!, "tracker", "webcam_fbt.py");
    public static string SetRolesScript => Path.Combine(Root!, "tracker", "set_roles.py");
    public static string ConfigFile => Path.Combine(Root!, "tracker", "config.json");
    public static string DriverDir => Path.Combine(Root!, "webcamfbt");

    // The exe lives somewhere under the WebcamFBT folder (app\ when published, ui\bin\... when debugging).
    private static string? FindRoot()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            if (File.Exists(Path.Combine(dir.FullName, "tracker", "webcam_fbt.py")))
                return dir.FullName;
        }
        return null;
    }
}
