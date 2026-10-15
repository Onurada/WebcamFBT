# WebcamFBT — Full Body Tracking with a Webcam (Meta Quest / SteamVR)

*Türkçe: [KULLANIM.md](KULLANIM.md)*

Estimates your skeleton from a webcam (MediaPipe Pose), anchors it to your headset's pose and sends it to
SteamVR as **virtual Vive trackers**. Works in VRChat, Vivecraft and any other SteamVR game.

Available trackers: hip, chest, both feet, both knees, both elbows. **Hip + both feet** gives the most solid result; elbows are the noisiest with one camera (games already place your arms from the controllers), so leave them off unless you have two cameras.

## Installing
1. Download `WebcamFBT-Setup-<version>.exe` from the **Releases** page on GitHub.
2. **Close SteamVR** and run it. No Python or .NET to install, everything is included.
   - No administrator rights needed; it installs for your user into `%LOCALAPPDATA%\Programs\WebcamFBT`.
   - The "Register the WebcamFBT driver with SteamVR" option sets up the driver and tracker roles.
   - The installer is not code-signed, so Windows SmartScreen may warn: *More info → Run anyway*.
3. On first launch the setup wizard walks you through the rest.

**Updating:** the app checks for a new version at startup (can be turned off under *General*). Just run the new installer; your settings and calibration are kept.

**Uninstalling:** *Settings → Apps → WebcamFBT → Uninstall*. It removes the driver from SteamVR and the tracker roles, and deletes the folder. Close SteamVR first.

> While the repository is **private**, only you can see the Releases page and the update check. To share with a friend, send them the installer file directly.

## Supported headsets
WebcamFBT only reads the headset's pose from SteamVR, so it works with any headset SteamVR supports:
- **Meta Quest 1, 2, 3, 3S, Pro:** over Link cable, Air Link, Steam Link, Virtual Desktop or ALVR.
- **Oculus Rift, Rift S:** through the Meta Horizon Link app.
- **Other SteamVR headsets:** Valve Index, HTC Vive, Pico, Windows Mixed Reality.

The app recognizes the connected headset and shows its name on the SteamVR chip (e.g. *connected · Meta Quest 3*). Hovering the chip shows the connection and how to recenter on that headset. Tested with a Quest 3 over Steam Link; the others use the same path but have not been tested yet.

## Getting started
1. **Place the camera:** about 2–3 m away, at waist or chest height. At least your **shoulders to feet** must be visible; your head may be out of the picture (with it in view tracking is a little more precise). If you don't fit, tilt the camera down a bit: keeping the feet in the picture matters more than the head. Use good lighting: in a dark room the camera drops from 30 to 20 frames per second.
2. Connect your headset to the PC (for a Quest: Link cable / Air Link / Steam Link / Virtual Desktop / ALVR) and start **SteamVR**.
3. Open `app\WebcamFBT.exe` and press **Start**. The engine starts in about 3 seconds.
4. **Calibrate (no key needed):** wearing the headset, **stand straight in front of the camera, look ahead and hold still for 2 seconds**, arms at your sides.
   - Once it sees you standing still it calibrates **by itself**: two short beeps = measuring, hold still; high beep = done.
   - Long low beep: it didn't work (e.g. you moved while it measured). Just stand still again.
   - This only happens while not calibrated; a working calibration is never changed on its own.
   - **To recalibrate**, hold both hands (controllers or tracked hands) **above your head for 2 seconds**. After the beep lower your arms, stand straight and look ahead. **F8** still works too.
5. Run your game's full body calibration.
   - **VRChat:** T-pose, then pull both triggers.
   - **Vivecraft:** first set the model type to *Split Arms* or *Split Arms/Legs* under *Playermodel Settings*, then run *Calibrate FBT…*.

> **Every session needs a calibration**, but all it takes is standing still in front of the camera for a moment. A Quest's tracking space can change between sessions, so an old calibration is not reused, and it is cleared when SteamVR reconnects. If you move the camera or recenter the headset, raise both hands (or press F8). The floor height is always read live from SteamVR.
> If you enabled new trackers (elbows, knees…), redo the in-game full body calibration too.

## First launch and the notification area
- **Setup wizard:** on first launch, 5 steps cover the language, the SteamVR driver, the camera, where to put it and calibration. Open it again with *General → Setup wizard…*.
- **Camera names:** cameras are listed by name (e.g. *Microsoft LifeCam VX-1000*). **Virtual** cameras added by OBS or Meta Quest Link are marked, with a warning if one is picked; phone apps (DroidCam, Iriun) show as *phone*.
- **Notification area (system tray):** minimizing sends the window to the notification area and tracking keeps running. The icon's menu offers Show, Calibrate, Pause and Exit, and hovering it shows the status. While the window is hidden, the calibration result and important warnings arrive as balloon notifications. Turn it off under *General*.

## More than one camera (optional)
One camera is the default. **Extra camera 1 / 2** under *Camera and model* add up to two more (a phone works too, with DroidCam or Iriun).

- **What it gains:** each camera is weak along its own viewing axis (depth). With two cameras at 60–90° to each other, one sees well what the other sees poorly, and their results are merged with weights to match. A joint hidden from one camera may be seen by the other.
- **A camera that sees you from behind** can swap left and right, so it is left out while a camera with a front view is available.
- **Calibration:** F8 calibrates all cameras at once. Every camera must see you at least from the shoulders to the feet. If a camera is reported as not calibrated, fix its view and press F8 again.
- **Performance:** each camera runs its own model in parallel. On this PC two cameras with the full model manage about 25 frames per second each.
- If an extra camera cannot be opened, the engine continues without it.

## Setup assistant
Open it with *Camera and model → Setup assistant…* while the engine runs. It has two modes, you pick which one to use:

**Room map with the headset**
- With SteamVR running and the headset on, it reads your play area. Optionally **Scan the room by walking**: walk once along the walls with the headset on, so no camera is suggested outside your walls.
- For 1–3 cameras and your webcam's field of view, a top-down map shows where each camera goes and which way it should face.
- You are the blue dot. Walk to a green numbered spot and put the camera there, facing the middle of the play area, about 1 m off the floor.
- Colours: green = every camera sees you head to toe, yellow = some do, red = none.
- After calibrating, it can also show where the cameras roughly are, in orange.

**Spot score with the webcam**
- No headset needed. Put the camera (or phone) at a candidate spot, step in front of it and stand head to toe in view.
- Every camera gets a 0–100 score and tells you what to do: your feet are cut off, you are too far, the camera is too high, it is dark, and so on.
- A webcam does not know where it is, so this mode makes no map; it scores each spot you try.

## The app
| Section | What it does |
|---|---|
| Status chips | Engine, camera FPS, time the model spends per frame (ms), SteamVR, person, calibration, number of trackers sent |
| Control | Start/Stop, Calibrate (F8), Pause (F9), test without SteamVR |
| General | **Language (Türkçe/English)**, start the engine on launch, keep calibration (optional, off), show preview |
| Fine tuning | Rotates the camera direction ±2° and changes the scale (small corrections after calibrating) |
| Trackers | Which body points are sent, applied immediately |
| Camera and model | Camera selection and scan, model: Fast (lite, 22 ms) / Balanced (full, 37 ms) / Accurate (heavy, 133 ms) |
| Smoothing | Jitter reduction, lag reduction on fast moves, depth smoothing, visibility threshold, floor lock, foot pinning |
| SteamVR driver | Install/repair and remove (while SteamVR is closed) |

## Hotkeys
| Key | Action |
|---|---|
| **F8** (anywhere) | Calibrate |
| **F9** (anywhere) | Pause / resume sending trackers |

The keys can be changed under *General* (F1–F12, also with Ctrl). They use Windows' `RegisterHotKey`, so while WebcamFBT runs the chosen keys are not passed on to the game.

## Performance and stability
Measured on this PC (Ryzen 5 2600):
- Model time: lite 22 ms, full 37 ms, heavy 133 ms. Preview and everything else together take under 2 ms.
- The real limit is the camera: 20 frames per second in a dark room. It can reach 30 with good lighting.

What keeps tracking stable:
- **Driver blending:** the ~20 poses per second are spread smoothly over the headset's 72–120 Hz. This adds about 50 ms of latency.
- **Depth smoothing:** towards/away from the camera is the noisiest axis for a single camera, so it gets stronger smoothing.
- **Visibility-based smoothing:** joints the model can barely see are smoothed harder.
- **Foot pinning:** a planted foot stays put until you lift it. Foot jitter while standing went from 1.1 cm to 0.3 cm on average.
- **CPU priority:** the engine runs at above-normal priority, so it doesn't stutter when the game loads the CPU.
- **Preview:** the preview pauses while the window is minimized, leaving the CPU to the game.
- **Camera connection:** the engine reconnects by itself if the camera drops out.
- **Feet in the room:** the feet are placed with the headset pose of the moment the frame was taken and smoothed in room coordinates, so a planted foot no longer slides along while you walk (hips and arms still follow the head without delay).
- **Feet out of the picture:** a foot the camera cannot see follows your knee (or hips) and stays on the floor instead of freezing behind you; when it is seen again it glides back in 0.3 s. Joints at the very edge of the picture count as unseen.
- **Back to the camera:** MediaPipe tends to swap left and right (legs turn 180°) when it sees you from behind; the headset tells which way you face, so such frames are turned back.

**Movement recordings:** *Control → Record movement (60 s)*. Press it, put the headset on within 15 s and step in front of the camera; two beeps start it, a high beep ends it. Walk, turn, sit, take steps. The recording (skeleton + headset poses, no video) goes to `logs
ecordings` and stays on your PC. `python tracker
eplay.py <file>` replays it and reports foot sliding, jumps, left/right flips and the camera's latency, comparing the old and new tracking.

What protects against failures:
- **Automatic restart:** if the engine crashes or stops responding for 30 seconds, the app restarts it. After 3 attempts within 3 minutes it stops and logs why.
- **Log files:** `logs\app.log` (app) and `logs\engine.log` (engine, including crash details), rotated at 1 MB. Open them with *General → Open log folder*.
- **Settings validation:** invalid values fall back to defaults or are clamped. A broken `config.json` is kept as `config.json.broken-<date>` and the app starts with defaults.
- **Clear error messages:** if the camera can't be opened, is used by another program, the driver isn't installed or its UDP port is taken, you get a message saying what to do, plus two low beeps.
- **Driver version check:** warns when SteamVR still runs an older WebcamFBT driver.
- **Headset recenter:** if SteamVR reports a tracking space change, the app warns you (at most every 5 minutes); if anything looks off, hold both hands above your head for 2 seconds (or press F8). The calibration is never changed on its own: the automatic correction in the first version misfired when the headset woke from standby and threw the body off, so it was removed.

## Limits (single camera)
Adding a second camera (see above) largely reduces the first two.
- Moving towards/away from the camera (depth) is the weakest axis.
- With **your back to the camera**, left and right can swap.
- Sitting, lying down and limbs hidden from the camera track poorly.
- Total latency is about 120–170 ms (camera + model + blending).

## Troubleshooting
- **No trackers in SteamVR:** the engine must be running and calibrated. Trackers only appear once data arrives.
- **Trackers offset or reversed:** recalibrate with F8. If still off, use *Fine tuning*.
- **Some trackers missing in a game (e.g. elbows):** they may have been added after the game started; redo the in-game full body calibration. In Vivecraft, elbows don't show with the *Vanilla* model type.
- **Wrong tracker roles:** in SteamVR → Settings → Controllers → *Manage Trackers*, set WCFBT_HIP = Waist, WCFBT_LFOOT = Left foot, and so on. Or press *Install / repair* in the app.
- **To remove:** close SteamVR and press *Remove* in the app (or run `uninstall_driver.bat`).

## Folder
| File | Purpose |
|---|---|
| `app\WebcamFBT.exe` | The app |
| `tracker\webcam_fbt.py` | Engine (Python, MediaPipe) |
| `tracker\messages.py` | Engine texts (Turkish/English) |
| `tracker\config.json` | Settings |
| `tracker\calibration.json` | Saved calibration |
| `webcamfbt\` | SteamVR driver (source in `driver_src\`, built with `build_driver.bat`) |
| `ui\` | App source code (language files in `ui\Strings\`) |
| `start.bat` | Starts the engine without the app, with an OpenCV window |

**Rebuilding:**
- **App:** `dotnet publish ui -c Release -o app -p:OutputPath=..\build\ui-bin\`
- **Driver:** run `build_driver.bat` while SteamVR is closed.

**Building the installer:** `powershell -ExecutionPolicy Bypass -File installer\package.ps1` → `build\installer\WebcamFBT-Setup-<version>.exe`. Needs Visual Studio (C++), the .NET 10 SDK, Python 3.12 and Inno Setup 6. The version lives in `VERSION`. Pushing a tag (`git tag v<version>`, `git push origin v<version>`) makes GitHub build the installer and publish it under Releases.

**Adding a language:**
1. Copy `ui\Strings\Strings.en.xaml` to `Strings.xx.xaml`, translate it and add it to the list in `ui\Loc.cs`.
2. Add an `"xx"` translation to every message in `tracker\messages.py`.
