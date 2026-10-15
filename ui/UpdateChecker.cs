using System.Net.Http;
using System.Text.Json;

namespace WebcamFBT;

/// <summary>
/// Asks GitHub for the latest release of the WebcamFBT repository and compares it with this version.
/// Only the public releases API is called (no account, no data sent beyond the request itself).
/// A private repository answers 404 to anonymous requests, which is reported as "not available".
/// </summary>
public static class UpdateChecker
{
    public const string DefaultRepository = "Onurada/WebcamFBT";

    public sealed record Result(bool Available, string? LatestVersion, string? Url, bool IsNewer);

    public static async Task<Result> CheckAsync(string repository)
    {
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
            http.DefaultRequestHeaders.UserAgent.ParseAdd($"WebcamFBT/{AppPaths.Version}");
            http.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
            using var doc = JsonDocument.Parse(await http.GetStringAsync($"https://api.github.com/repos/{repository}/releases/latest"));
            var tag = doc.RootElement.GetProperty("tag_name").GetString() ?? "";
            var url = doc.RootElement.GetProperty("html_url").GetString();
            var latest = tag.TrimStart('v', 'V');
            var newer = Version.TryParse(latest, out var l) && Version.TryParse(AppPaths.Version, out var c) && l > c;
            return new Result(true, latest, url, newer);
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or JsonException or KeyNotFoundException)
        {
            return new Result(false, null, null, false);
        }
    }
}
