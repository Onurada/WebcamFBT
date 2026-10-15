# Third-party notices

WebcamFBT uses or ships the following third-party components. Their licenses apply to those components only.
When WebcamFBT is packaged for others (installer), the full license texts listed here must be bundled with it.

| Component | Used for | Version | License | Source |
|---|---|---|---|---|
| OpenVR SDK (`driver_src/third_party/openvr_driver.h`) | SteamVR driver interface | master, 2026-10 | BSD-3-Clause, © Valve Corporation | https://github.com/ValveSoftware/openvr |
| MediaPipe | Pose estimation (Python package) | 1.1.1 | Apache-2.0 | https://github.com/google-ai-edge/mediapipe |
| MediaPipe Pose Landmarker models (`tracker/pose_landmarker_*.task`) | Pose estimation models | float16, latest | Apache-2.0 | https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker |
| OpenCV (`opencv-python`) | Camera capture, preview drawing | 5.0.0 | Apache-2.0 (bundled third-party parts: see the package's `LICENSE-3RD-PARTY.txt`) | https://github.com/opencv/opencv-python |
| NumPy | Math | 2.5.4 | BSD-3-Clause (bundled parts: 0BSD, MIT, Zlib, CC0-1.0) | https://numpy.org |
| pyopenvr (`openvr`) | Reading the headset pose from Python | 2.12.1401 | BSD-3-Clause | https://github.com/cmbruns/pyopenvr |
| .NET / WPF | Desktop app runtime | 10 | MIT | https://github.com/dotnet |

The Vive tracker render model and input profile referenced by the driver (`{htc}vr_tracker_vive_1_0`,
`{htc}/input/vive_tracker_profile.json`) are part of SteamVR on the user's machine and are not shipped with WebcamFBT.

WebcamFBT's own code has no license file yet, so all rights are reserved by its author. Pick a license
(for example MIT) before publishing or sharing the source.
