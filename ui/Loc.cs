using System.Globalization;
using System.Windows;

namespace WebcamFBT;

/// <summary>
/// UI language switching. Texts live in Strings/Strings.{code}.xaml and are used from XAML
/// with {DynamicResource S.Key}, so swapping the dictionary relabels the window at once.
/// To add a language: add Strings/Strings.xx.xaml, list it here, and add it to tracker/messages.py.
/// </summary>
public static class Loc
{
    public static readonly (string Code, string NativeName)[] Languages =
    [
        ("tr", "Türkçe"),
        ("en", "English"),
    ];

    private static ResourceDictionary? _current;

    public static string Current { get; private set; } = "en";

    public static string SystemDefault() =>
        Languages.Any(l => l.Code == CultureInfo.CurrentUICulture.TwoLetterISOLanguageName)
            ? CultureInfo.CurrentUICulture.TwoLetterISOLanguageName
            : "en";

    public static void Apply(string code)
    {
        if (Languages.All(l => l.Code != code)) code = "en";
        var dict = new ResourceDictionary { Source = new Uri($"pack://application:,,,/Strings/Strings.{code}.xaml") };
        var merged = Application.Current.Resources.MergedDictionaries;
        if (_current != null) merged.Remove(_current);
        merged.Add(dict);
        _current = dict;
        Current = code;
    }

    public static string T(string key, params object[] args)
    {
        var text = Application.Current.TryFindResource($"S.{key}") as string ?? key;
        return args.Length == 0 ? text : string.Format(CultureInfo.CurrentCulture, text, args);
    }
}
