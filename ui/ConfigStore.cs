using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace WebcamFBT;

/// <summary>Reads and writes tracker/config.json, keeping keys the UI does not know about.</summary>
public sealed class ConfigStore
{
    private static readonly JsonSerializerOptions WriteOptions = new() { WriteIndented = true };
    private readonly string _path;
    private readonly JsonObject _root;

    public ConfigStore(string path)
    {
        _path = path;
        _root = JsonNode.Parse(File.ReadAllText(path))?.AsObject() ?? new JsonObject();
    }

    public double GetDouble(string key, double fallback) => _root[key]?.GetValue<double>() ?? fallback;
    public int GetInt(string key, int fallback) => _root[key]?.GetValue<int>() ?? fallback;
    public bool GetBool(string key, bool fallback) => _root[key]?.GetValue<bool>() ?? fallback;
    public string GetString(string key, string fallback) => _root[key]?.GetValue<string>() ?? fallback;

    public bool GetTracker(string name) => _root["trackers"]?[name]?.GetValue<bool>() ?? false;

    public void Set(string key, JsonNode? value) => _root[key] = value;

    public void SetTracker(string name, bool value)
    {
        if (_root["trackers"] is not JsonObject trackers)
            _root["trackers"] = trackers = new JsonObject();
        trackers[name] = value;
    }

    public void Save() => File.WriteAllText(_path, _root.ToJsonString(WriteOptions));
}
