using System.Windows;
using Forms = System.Windows.Forms;

namespace WebcamFBT;

/// <summary>Notification area icon: shows status in its tooltip and offers show / calibrate / pause / exit.</summary>
public sealed class TrayIcon : IDisposable
{
    private readonly Forms.NotifyIcon _icon;
    private readonly Forms.ToolStripMenuItem _show, _calibrate, _pause, _exit;
    private string _calibrateKey = "F8", _pauseKey = "F9";

    public TrayIcon(Action show, Action calibrate, Action pause, Action exit)
    {
        var stream = Application.GetResourceStream(new Uri("pack://application:,,,/Assets/app.ico"))!.Stream;
        _icon = new Forms.NotifyIcon { Icon = new System.Drawing.Icon(stream), Visible = true, Text = "WebcamFBT" };
        _show = new Forms.ToolStripMenuItem("", null, (_, _) => show());
        _calibrate = new Forms.ToolStripMenuItem("", null, (_, _) => calibrate());
        _pause = new Forms.ToolStripMenuItem("", null, (_, _) => pause());
        _exit = new Forms.ToolStripMenuItem("", null, (_, _) => exit());
        var menu = new Forms.ContextMenuStrip();
        menu.Items.AddRange([_show, new Forms.ToolStripSeparator(), _calibrate, _pause, new Forms.ToolStripSeparator(), _exit]);
        _icon.ContextMenuStrip = menu;
        _icon.DoubleClick += (_, _) => show();
        RefreshTexts();
    }

    public void SetHotkeys(string calibrate, string pause)
    {
        _calibrateKey = calibrate;
        _pauseKey = pause;
        RefreshTexts();
    }

    public void RefreshTexts()
    {
        _show.Text = Loc.T("Tray.Show");
        _calibrate.Text = Loc.T("Control.Calibrate", _calibrateKey);
        _pause.Text = Loc.T("Control.Pause", _pauseKey);
        _exit.Text = Loc.T("Tray.Exit");
    }

    public void SetEngineRunning(bool running) => _calibrate.Enabled = _pause.Enabled = running;

    /// <summary>Tooltip text (Windows cuts it at 127 characters).</summary>
    public void SetStatus(string text)
    {
        text = $"WebcamFBT · {text}";
        _icon.Text = text.Length > 127 ? text[..127] : text;
    }

    public void ShowBalloon(string title, string text) => _icon.ShowBalloonTip(4000, title, text, Forms.ToolTipIcon.Info);

    public void Dispose()
    {
        _icon.Visible = false;
        _icon.Dispose();
    }
}
