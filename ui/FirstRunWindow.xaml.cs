using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace WebcamFBT;

/// <summary>What the first-run wizard needs from the main window.</summary>
public sealed record WizardHost(
    Action<string> ApplyLanguage,
    Func<int> MainCamera,
    Action<int> SelectMainCamera,
    Func<bool, Task<List<CameraInfo>>> ScanCameras,
    Func<bool> AutoStart,
    Action<bool> SetAutoStart,
    Func<string> CalibrateKey,
    Action<bool> Finish); // true = start the engine now

/// <summary>Five steps on first launch: language, SteamVR driver, camera, placement, calibration.</summary>
public partial class FirstRunWindow : Window
{
    private readonly WizardHost _host;
    private readonly StackPanel[] _steps;
    private int _step;
    private bool _loading = true;
    private bool _finished;

    public FirstRunWindow(WizardHost host)
    {
        _host = host;
        InitializeComponent();
        _steps = [Step1, Step2, Step3, Step4, Step5];
        foreach (var (code, name) in Loc.Languages)
            LanguageCombo.Items.Add(new ComboBoxItem { Content = name, Tag = code });
        LanguageCombo.SelectedItem = LanguageCombo.Items.Cast<ComboBoxItem>().First(i => (string)i.Tag == Loc.Current);
        AutoStartCheck.IsChecked = host.AutoStart();
        _loading = false;
        Closed += (_, _) => { if (!_finished) _host.Finish(false); };
        ShowStep(0);
    }

    private static readonly string[] Titles = ["Wizard.WelcomeTitle", "Wizard.DriverTitle", "Wizard.CameraTitle", "Wizard.PlacementTitle", "Wizard.CalibrationTitle"];

    private void ShowStep(int step)
    {
        _step = step;
        for (var i = 0; i < _steps.Length; i++)
            _steps[i].Visibility = i == step ? Visibility.Visible : Visibility.Collapsed;
        StepText.Text = Loc.T("Wizard.Step", step + 1, _steps.Length);
        StepTitle.Text = Loc.T(Titles[step]);
        BackButton.IsEnabled = step > 0;
        NextButton.Content = Loc.T(step == _steps.Length - 1 ? "Wizard.Finish" : "Wizard.Next");
        if (step == 1) RefreshDriver();
        if (step == 2 && CameraCombo.Items.Count == 0) _ = LoadCameras(probe: false);
        if (step == 4) CalibrationText.Text = Loc.T("Wizard.CalibrationText", _host.CalibrateKey());
    }

    private void LanguageCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loading || LanguageCombo.SelectedItem is not ComboBoxItem item) return;
        _host.ApplyLanguage((string)item.Tag);
        ShowStep(_step); // re-render texts set from code
    }

    // ------------------------------------------------------------------ driver

    private void RefreshDriver()
    {
        var installed = DriverManager.IsInstalled();
        DriverDot.Fill = (Brush)FindResource(installed ? "GoodBrush" : "BadBrush");
        DriverStatus.Text = Loc.T(installed ? "Driver.Installed" : "Driver.NotInstalled");
    }

    private async void InstallButton_Click(object sender, RoutedEventArgs e)
    {
        if (DriverManager.IsSteamVrRunning())
        {
            DriverMessage.Text = Loc.T("Driver.CloseSteamVr");
            return;
        }
        InstallButton.IsEnabled = false;
        try
        {
            await DriverManager.InstallAsync();
            DriverMessage.Text = Loc.T("Driver.InstallDone");
        }
        catch (Exception ex)
        {
            DriverMessage.Text = Loc.T("Driver.Failed", ex.Message);
        }
        finally
        {
            InstallButton.IsEnabled = true;
            RefreshDriver();
        }
    }

    // ------------------------------------------------------------------ camera

    private async Task LoadCameras(bool probe)
    {
        ScanButton.IsEnabled = false;
        CameraMessage.Text = Loc.T(probe ? "Camera.Scanning" : "Assistant.Waiting");
        try
        {
            var cams = await _host.ScanCameras(probe);
            CameraCombo.Items.Clear();
            foreach (var c in cams)
                CameraCombo.Items.Add(new ComboBoxItem { Content = c.Label, Tag = c.Index });
            var current = _host.MainCamera();
            // Prefer the configured camera, else the first real one.
            var pick = cams.FirstOrDefault(c => c.Index == current && !c.IsVirtual) ?? cams.FirstOrDefault(c => !c.IsVirtual) ?? cams.FirstOrDefault();
            if (pick != null)
                CameraCombo.SelectedItem = CameraCombo.Items.Cast<ComboBoxItem>().First(i => (int)i.Tag == pick.Index);
            CameraMessage.Text = cams.Count == 0 ? Loc.T("Wizard.NoCameras")
                : cams.Any(c => c.IsVirtual) ? Loc.T("Wizard.VirtualNote") : "";
        }
        catch (Exception ex)
        {
            CameraMessage.Text = Loc.T("Log.ScanFailed", ex.Message);
        }
        finally
        {
            ScanButton.IsEnabled = true;
        }
    }

    private async void ScanButton_Click(object sender, RoutedEventArgs e) => await LoadCameras(probe: true);

    // ------------------------------------------------------------------ navigation

    private void BackButton_Click(object sender, RoutedEventArgs e)
    {
        if (_step > 0) ShowStep(_step - 1);
    }

    private void NextButton_Click(object sender, RoutedEventArgs e)
    {
        if (_step == 2 && CameraCombo.SelectedItem is ComboBoxItem cam)
            _host.SelectMainCamera((int)cam.Tag);
        if (_step < _steps.Length - 1)
        {
            ShowStep(_step + 1);
            return;
        }
        _host.SetAutoStart(AutoStartCheck.IsChecked == true);
        _finished = true;
        _host.Finish(StartNowCheck.IsChecked == true);
        Close();
    }

    private void SkipButton_Click(object sender, RoutedEventArgs e)
    {
        _finished = true;
        _host.Finish(false);
        Close();
    }
}
