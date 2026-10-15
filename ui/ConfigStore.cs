using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace WebcamFBT;

/// <summary>
/// Reads and writes tracker/config.json, keeping keys the UI does not know about.
/// A broken file never stops the app: it is kept aside as config.json.broken-&lt;time&gt; and the app
/// starts from defaults (the engine fills in and validates every value it uses).
/// </summary>
public sealed class ConfigStore
{
    private static readonly JsonSerializerOptions WriteOptions = new() { WriteIndented = true };
    private readonly string _path;
    private readonly JsonObject _root;

    /// <summary>Name of the backup when the file was unreadable, else null.</summary>
    public string? BrokenBackup { get; }

    public ConfigStore(string path)
    {
        _path = path;
        try
        {
            _root = File.Exists(path) ? JsonNode.Parse(File.ReadAllText(path))?.AsObject() ?? new JsonObject() : new JsonObject();
        }
        catch (Exception e) when (e is JsonException or InvalidOperationException)
        {
            var backup = $"{path}.broken-{DateTime.Now:yyyyMMdd-HHmmss}";
            File.Move(path, backup, overwrite: true);
            BrokenBackup = Path.GetFileName(backup);
            _root = new JsonObject();
        }
    }

    // Wrong types (e.g. "abc" where a number belongs) fall back instead of throwing.
    private T Get<T>(JsonNode? node, T fallback)
    {
        try { return node is JsonValue v && v.TryGetValue<T>(out var value) ? value : fallback; }
        catch (Exception e) when (e is InvalidOperationException or FormatException) { return fallback; }
    }

    public double GetDouble(string key, double fallback)
    {
        var node = _root[key];
        return node is JsonValue v && v.TryGetValue<int>(out var i) ? i : Get(node, fallback);
    }

    public int GetInt(string key, int fallback) => Get(_root[key], fallback);
    public bool GetBool(string key, bool fallback) => Get(_root[key], fallback);
    public string GetString(string key, string fallback) => Get(_root[key], fallback);

    // Same defaults as DEFAULTS in tracker/webcam_fbt.py
    private static readonly HashSet<string> DefaultTrackers = ["hip", "left_foot", "right_foot"];

    public List<int> GetIntList(string key) =>
        _root[key] is JsonArray arr ? arr.Select(n => Get(n, int.MinValue)).Where(i => i != int.MinValue).ToList() : [];

    public bool GetTracker(string name) =>
        _root["trackers"] is JsonObject trackers ? Get(trackers[name], DefaultTrackers.Contains(name)) : DefaultTrackers.Contains(name);

    public void Set(string key, JsonNode? value) => _root[key] = value;

    public void SetTracker(string name, bool value)
    {
        if (_root["trackers"] is not JsonObject trackers)
        {
            _root["trackers"] = trackers = new JsonObject();
            foreach (var t in DefaultTrackers) trackers[t] = true;
        }
        trackers[name] = value;
    }

    public void Save() => File.WriteAllText(_path, _root.ToJsonString(WriteOptions));
}
