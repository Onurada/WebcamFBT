using System.IO;
using System.Text;

namespace WebcamFBT;

/// <summary>logs/app.log: everything the app shows in its log box plus engine stderr, rotated at 1 MB.</summary>
public static class FileLog
{
    private const long MaxBytes = 1_000_000;
    private const int Keep = 3;
    private static readonly object Lock = new();
    private static readonly Encoding NoBom = new UTF8Encoding(false);

    public static string? Directory => AppPaths.Root == null ? null : Path.Combine(AppPaths.Root, "logs");

    public static void Write(string line)
    {
        if (Directory == null) return;
        lock (Lock)
        {
            try
            {
                System.IO.Directory.CreateDirectory(Directory);
                var path = Path.Combine(Directory, "app.log");
                if (File.Exists(path) && new FileInfo(path).Length > MaxBytes)
                    Rotate(path);
                File.AppendAllText(path, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {line}{Environment.NewLine}", NoBom);
            }
            catch (IOException) { } // logging must never take the app down
            catch (UnauthorizedAccessException) { }
        }
    }

    private static void Rotate(string path)
    {
        for (var i = Keep - 1; i >= 1; i--)
        {
            var older = $"{path}.{i}";
            if (File.Exists(older)) File.Move(older, $"{path}.{i + 1}", overwrite: true);
        }
        File.Move(path, $"{path}.1", overwrite: true);
    }
}
