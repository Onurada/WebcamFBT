using System.Globalization;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Shapes;

namespace WebcamFBT;

/// <summary>
/// Setup assistant: where to put the cameras.
/// Room tab: the engine reads the SteamVR play area (or records a walk along the walls with the
/// headset on) and sends recommended camera spots, a coverage grid and the player's position;
/// this window draws them as a top-down map (front of the play area at the top).
/// Spot tab: the engine scores every camera's current view of the player with hints.
/// </summary>
public partial class SetupAssistantWindow : Window
{
    private readonly Engine _engine;
    private readonly Action<double> _hfovChanged;
    private readonly bool _loading = true;
    private bool _walking;
    private JsonElement? _lastRoom;

    private static readonly Brush PlayAreaBrush = Frozen(Color.FromArgb(200, 120, 160, 255));
    private static readonly Brush RoomBrush = Frozen(Color.FromArgb(220, 230, 230, 235));
    private static readonly Brush SpotBrush = Frozen(Color.FromRgb(0x3D, 0xDC, 0x97));
    private static readonly Brush ActualBrush = Frozen(Color.FromRgb(0xFF, 0xB5, 0x47));
    private static readonly Brush PlayerBrush = Frozen(Color.FromRgb(0x6C, 0x8C, 0xFF));
    // Premultiplied BGRA pixels for the coverage bitmap
    private static readonly uint CoverageNone = Premultiplied(40, 255, 93, 108);   // seen by no camera
    private static readonly uint CoverageSome = Premultiplied(70, 255, 181, 71);   // seen by some
    private static readonly uint CoverageAll = Premultiplied(80, 61, 220, 151);    // seen by all

    private static uint Premultiplied(byte a, byte r, byte g, byte b) =>
        (uint)(a << 24 | (r * a / 255) << 16 | (g * a / 255) << 8 | (b * a / 255));

    public SetupAssistantWindow(Engine engine, int cameras, double hfov, Action<double> hfovChanged)
    {
        _engine = engine;
        _hfovChanged = hfovChanged;
        InitializeComponent();
        CountCombo.SelectedIndex = Math.Clamp(cameras, 1, 3) - 1;
        FovSlider.Value = hfov;
        FovText.Text = $"{hfov:0}°";
        UpdateWalkButton();
        RoomSourceText.Text = Loc.T("Assistant.Waiting");
        SetMapNotice(Loc.T("Assistant.Waiting"));
        _loading = false;
    }

    private void SetMapNotice(string text)
    {
        MapNotice.Text = text;
        MapNoticeBox.Visibility = string.IsNullOrEmpty(text) ? Visibility.Collapsed : Visibility.Visible;
    }

    private static Brush Frozen(Color c)
    {
        var b = new SolidColorBrush(c);
        b.Freeze();
        return b;
    }

    private string Mode => Tabs.SelectedItem == SpotTab ? "spot" : "room";

    private int CameraCount => int.Parse((string)((ComboBoxItem)CountCombo.SelectedItem).Tag, CultureInfo.InvariantCulture);

    /// <summary>Tells the engine what to send. Called on open, on changes and after an engine restart.</summary>
    public void SendMode()
    {
        EngineNotice.Visibility = _engine.IsRunning ? Visibility.Collapsed : Visibility.Visible;
        _engine.Send(new { cmd = "assistant", mode = Mode, cameras = CameraCount, hfov = FovSlider.Value });
    }

    private void Tabs_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (e.Source == Tabs && !_loading) SendMode();
    }

    private void RoomSettings_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (!_loading) SendMode();
    }

    private void FovSlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (FovText == null) return;
        FovText.Text = $"{e.NewValue:0}°";
        if (_loading) return;
        _hfovChanged(e.NewValue);
        SendMode();
    }

    private void WalkButton_Click(object sender, RoutedEventArgs e)
    {
        _walking = !_walking;
        _engine.Send(new { cmd = "room_walk", value = _walking });
        UpdateWalkButton();
    }

    private void UpdateWalkButton() =>
        WalkButton.Content = Loc.T(_walking ? "Assistant.WalkStop" : "Assistant.WalkStart");

    private void ShowActualCheck_Click(object sender, RoutedEventArgs e)
    {
        if (_lastRoom is JsonElement room) DrawRoom(room);
    }

    private void Map_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (_lastRoom is JsonElement room) DrawRoom(room);
    }

    private void Window_Closed(object? sender, EventArgs e)
    {
        if (_walking) _engine.Send(new { cmd = "room_walk", value = false });
        _engine.Send(new { cmd = "assistant", mode = "off" });
    }

    /// <summary>Engine messages forwarded by the main window.</summary>
    public void Handle(JsonElement msg)
    {
        EngineNotice.Visibility = Visibility.Collapsed;
        switch (msg.GetProperty("type").GetString())
        {
            case "room":
                _lastRoom = msg;
                UpdateRoomPanel(msg);
                DrawRoom(msg);
                break;
            case "spot":
                UpdateSpots(msg);
                break;
        }
    }

    public void EngineStopped() => EngineNotice.Visibility = Visibility.Visible;

    /// <summary>Saves this window's content as a PNG (used by "--assistant-snapshot path" for checks).</summary>
    public void SaveSnapshot(string path)
    {
        var content = (FrameworkElement)Content;
        var dpi = VisualTreeHelper.GetDpi(this);
        var bmp = new System.Windows.Media.Imaging.RenderTargetBitmap(
            (int)(content.ActualWidth * dpi.DpiScaleX), (int)(content.ActualHeight * dpi.DpiScaleY),
            dpi.PixelsPerInchX, dpi.PixelsPerInchY, PixelFormats.Pbgra32);
        var visual = new DrawingVisual();
        using (var dc = visual.RenderOpen())
        {
            var size = new Rect(0, 0, content.ActualWidth, content.ActualHeight);
            dc.DrawRectangle(Background ?? Brushes.Black, null, size);
            dc.DrawRectangle(new VisualBrush(content), null, size);
        }
        bmp.Render(visual);
        var encoder = new System.Windows.Media.Imaging.PngBitmapEncoder();
        encoder.Frames.Add(System.Windows.Media.Imaging.BitmapFrame.Create(bmp));
        using var file = System.IO.File.Create(path);
        encoder.Save(file);
    }

    public void SelectSpotTab() => Tabs.SelectedItem = SpotTab;

    // ------------------------------------------------------------------ room tab

    private void UpdateRoomPanel(JsonElement m)
    {
        var vr = m.GetProperty("vr").GetBoolean();
        var hmd = m.GetProperty("hmd").GetBoolean();
        var hasPlayArea = m.GetProperty("play_area").ValueKind == JsonValueKind.Array;
        var hasRoom = m.GetProperty("room").ValueKind == JsonValueKind.Array;
        _walking = m.GetProperty("walking").GetBoolean();
        UpdateWalkButton();

        if (!vr || !hmd)
            RoomSourceText.Text = Loc.T("Assistant.NoVr");
        else if (_walking)
            RoomSourceText.Text = Loc.T("Assistant.Walking", m.GetProperty("walk_points").GetInt32());
        else if (hasRoom)
            RoomSourceText.Text = Loc.T(hasPlayArea ? "Assistant.SourceBoth" : "Assistant.SourceWalk");
        else if (hasPlayArea)
            RoomSourceText.Text = Loc.T("Assistant.SourcePlayArea");
        else
            RoomSourceText.Text = Loc.T("Assistant.SourceNone");

        var lines = new List<string>
        {
            Loc.T("Assistant.MinDistance", m.GetProperty("min_distance").GetDouble()),
        };
        var player = m.GetProperty("player");
        var area = m.GetProperty("area");
        double cx = area[0].GetDouble(), cz = area[1].GetDouble();
        var n = 1;
        foreach (var s in m.GetProperty("spots").EnumerateArray())
        {
            double x = s.GetProperty("x").GetDouble(), z = s.GetProperty("z").GetDouble();
            var where = Loc.T(DirectionKey(s.GetProperty("angle").GetDouble()));
            var line = Loc.T("Assistant.SpotLine", n, where, Math.Sqrt((x - cx) * (x - cx) + (z - cz) * (z - cz)));
            if (player.ValueKind == JsonValueKind.Object)
            {
                double px = player.GetProperty("x").GetDouble(), pz = player.GetProperty("z").GetDouble();
                line += " " + Loc.T("Assistant.YouAreAway", Math.Sqrt((x - px) * (x - px) + (z - pz) * (z - pz)));
            }
            if (s.GetProperty("too_close").GetBoolean())
                line += " " + Loc.T("Assistant.TooClose");
            lines.Add(line);
            n++;
        }
        foreach (var a in m.GetProperty("actual").EnumerateArray())
            lines.Add(Loc.T("Assistant.ActualLine", a.GetProperty("index").GetInt32(), a.GetProperty("distance").GetDouble()));
        SpotList.ItemsSource = lines;
    }

    private static string DirectionKey(double angle)
    {
        var a = ((angle % 360) + 540) % 360 - 180;
        return a switch
        {
            > -20 and < 20 => "Assistant.DirFront",
            >= 20 and < 100 => "Assistant.DirFrontRight",
            > -100 and <= -20 => "Assistant.DirFrontLeft",
            >= 100 and < 160 => "Assistant.DirBackRight",
            > -160 and <= -100 => "Assistant.DirBackLeft",
            _ => "Assistant.DirBack",
        };
    }

    private void DrawRoom(JsonElement m)
    {
        Map.Children.Clear();
        double W = Map.ActualWidth, H = Map.ActualHeight;
        if (W < 10 || H < 10) return;
        SetMapNotice(m.GetProperty("vr").GetBoolean() && m.GetProperty("hmd").GetBoolean() ? "" : Loc.T("Assistant.NoVr"));

        var b = m.GetProperty("bounds");
        double x0 = b[0].GetDouble(), z0 = b[1].GetDouble(), x1 = b[2].GetDouble(), z1 = b[3].GetDouble();
        var scale = Math.Min((W - 20) / (x1 - x0), (H - 20) / (z1 - z0));
        var offX = (W - (x1 - x0) * scale) / 2;
        var offY = (H - (z1 - z0) * scale) / 2;
        // Front of the play area (-z) is at the top.
        Point P(double x, double z) => new(offX + (x - x0) * scale, offY + (z - z0) * scale);

        // Coverage: how many cameras see the player head to toe on each 20 cm cell.
        var showActual = ShowActualCheck.IsChecked == true && m.GetProperty("coverage_actual").ValueKind == JsonValueKind.Object;
        var cov = m.GetProperty(showActual ? "coverage_actual" : "coverage");
        var cameraCount = showActual ? m.GetProperty("actual").GetArrayLength() : m.GetProperty("spots").GetArrayLength();
        if (cov.ValueKind == JsonValueKind.Object)
        {
            double cell = cov.GetProperty("cell").GetDouble(), cx0 = cov.GetProperty("x0").GetDouble(), cz0 = cov.GetProperty("z0").GetDouble();
            int cols = cov.GetProperty("cols").GetInt32(), rows = cov.GetProperty("rows").GetInt32();
            var pixels = new uint[cols * rows];
            var i = 0;
            foreach (var count in cov.GetProperty("counts").EnumerateArray())
            {
                var c = count.GetInt32();
                pixels[i++] = c == 0 ? CoverageNone : c >= cameraCount ? CoverageAll : CoverageSome;
            }
            var bmp = new System.Windows.Media.Imaging.WriteableBitmap(cols, rows, 96, 96, PixelFormats.Pbgra32, null);
            bmp.WritePixels(new Int32Rect(0, 0, cols, rows), pixels, cols * 4, 0);
            var img = new Image { Source = bmp, Width = cols * cell * scale, Height = rows * cell * scale, Stretch = Stretch.Fill };
            RenderOptions.SetBitmapScalingMode(img, BitmapScalingMode.NearestNeighbor);
            var p = P(cx0, cz0);
            Canvas.SetLeft(img, p.X);
            Canvas.SetTop(img, p.Y);
            Map.Children.Add(img);
        }

        // Walls from the walk, and the play area.
        if (m.GetProperty("room").ValueKind == JsonValueKind.Array)
        {
            var poly = new Polygon { Stroke = RoomBrush, StrokeThickness = 2 };
            foreach (var pt in m.GetProperty("room").EnumerateArray())
                poly.Points.Add(P(pt[0].GetDouble(), pt[1].GetDouble()));
            Map.Children.Add(poly);
        }
        var area = m.GetProperty("area");
        {
            double cx = area[0].GetDouble(), cz = area[1].GetDouble(), w = area[2].GetDouble(), d = area[3].GetDouble();
            var tl = P(cx - w / 2, cz - d / 2);
            var rect = new Rectangle
            {
                Width = w * scale, Height = d * scale, Stroke = PlayAreaBrush, StrokeThickness = 2,
                StrokeDashArray = [4, 3],
            };
            Canvas.SetLeft(rect, tl.X);
            Canvas.SetTop(rect, tl.Y);
            Map.Children.Add(rect);
            AddLabel(Loc.T("Assistant.PlayAreaLabel"), P(cx - w / 2, cz - d / 2), PlayAreaBrush, 0, -18);
            AddLabel(Loc.T("Assistant.FrontLabel"), P(cx, cz - d / 2), PlayAreaBrush, -20, -34);
        }

        var hfov = m.GetProperty("hfov").GetDouble();
        var reach = m.GetProperty("min_distance").GetDouble() + 1.0;
        var n = 1;
        foreach (var s in m.GetProperty("spots").EnumerateArray())
            DrawCamera(s, $"{n++}", SpotBrush, hfov, reach, P, scale);
        if (ShowActualCheck.IsChecked == true)
            foreach (var a in m.GetProperty("actual").EnumerateArray())
                DrawCamera(a, $"K{a.GetProperty("index").GetInt32()}", ActualBrush, hfov, reach, P, scale);

        var player = m.GetProperty("player");
        if (player.ValueKind == JsonValueKind.Object)
        {
            var pp = P(player.GetProperty("x").GetDouble(), player.GetProperty("z").GetDouble());
            var dot = new Ellipse { Width = 14, Height = 14, Fill = PlayerBrush };
            Canvas.SetLeft(dot, pp.X - 7);
            Canvas.SetTop(dot, pp.Y - 7);
            Map.Children.Add(dot);
            var a = player.GetProperty("facing").GetDouble() * Math.PI / 180;
            Map.Children.Add(new Line
            {
                X1 = pp.X, Y1 = pp.Y, X2 = pp.X + Math.Sin(a) * 22, Y2 = pp.Y - Math.Cos(a) * 22,
                Stroke = PlayerBrush, StrokeThickness = 3,
            });
            AddLabel(Loc.T("Assistant.YouLabel"), pp, PlayerBrush, 10, 6);
        }
    }

    private void DrawCamera(JsonElement c, string label, Brush brush, double hfov, double reach, Func<double, double, Point> P, double scale)
    {
        double x = c.GetProperty("x").GetDouble(), z = c.GetProperty("z").GetDouble();
        var facing = c.GetProperty("facing").GetDouble();
        var p = P(x, z);
        // Field of view wedge
        var wedge = new Polygon { Fill = new SolidColorBrush(((SolidColorBrush)brush).Color) { Opacity = 0.18 }, Stroke = brush, StrokeThickness = 1 };
        wedge.Points.Add(p);
        for (var k = -1.0; k <= 1.0; k += 0.25)
        {
            var a = (facing + k * hfov / 2) * Math.PI / 180;
            wedge.Points.Add(new Point(p.X + Math.Sin(a) * reach * scale, p.Y - Math.Cos(a) * reach * scale));
        }
        Map.Children.Add(wedge);
        var dot = new Ellipse { Width = 18, Height = 18, Fill = brush };
        Canvas.SetLeft(dot, p.X - 9);
        Canvas.SetTop(dot, p.Y - 9);
        Map.Children.Add(dot);
        AddLabel(label, p, Brushes.Black, -4, -9, bold: true);
    }

    private void AddLabel(string text, Point at, Brush brush, double dx, double dy, bool bold = false)
    {
        var t = new TextBlock { Text = text, Foreground = brush, FontSize = 12, FontWeight = bold ? FontWeights.Bold : FontWeights.Normal };
        Canvas.SetLeft(t, at.X + dx);
        Canvas.SetTop(t, at.Y + dy);
        Map.Children.Add(t);
    }

    // ------------------------------------------------------------------ spot tab

    private void UpdateSpots(JsonElement m)
    {
        var cards = new List<UIElement>();
        foreach (var c in m.GetProperty("cameras").EnumerateArray())
        {
            var score = c.GetProperty("score").GetInt32();
            var color = score >= 80 ? (Brush)FindResource("GoodBrush") : score >= 50 ? (Brush)FindResource("WarnBrush") : (Brush)FindResource("BadBrush");
            var panel = new StackPanel();
            panel.Children.Add(new TextBlock { Text = Loc.T("Assistant.CameraN", c.GetProperty("index").GetInt32()), FontWeight = FontWeights.SemiBold });
            panel.Children.Add(new TextBlock { Text = $"{score}", FontSize = 56, FontWeight = FontWeights.Bold, Foreground = color });
            panel.Children.Add(new ProgressBar { Value = score, Maximum = 100, Height = 6, Foreground = color, Margin = new Thickness(0, 0, 0, 10) });
            var hints = c.GetProperty("hints").EnumerateArray().Select(h => h.GetString()!).ToList();
            if (hints.Count == 0)
                panel.Children.Add(new TextBlock { Text = Loc.T("Assistant.SpotGood"), TextWrapping = TextWrapping.Wrap, Foreground = color });
            foreach (var h in hints)
                panel.Children.Add(new TextBlock { Text = "• " + Loc.T("Spot." + h), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 4) });
            cards.Add(new Border
            {
                Width = 300, Margin = new Thickness(0, 0, 12, 12), Padding = new Thickness(16), CornerRadius = new CornerRadius(8),
                Background = (Brush)FindResource("CardBackgroundFillColorDefaultBrush"),
                BorderBrush = (Brush)FindResource("CardStrokeColorDefaultBrush"), BorderThickness = new Thickness(1),
                Child = panel,
            });
        }
        SpotCards.ItemsSource = cards;
    }
}
