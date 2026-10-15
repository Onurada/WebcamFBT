"""Writes (or with --remove deletes) the WebcamFBT tracker roles in SteamVR's steamvr.vrsettings.
Run while SteamVR is closed. Same result as assigning them by hand in SteamVR > Settings >
Controllers > Manage Trackers. A backup is saved next to the file as steamvr.vrsettings.webcamfbt-backup.

    python set_roles.py [--remove] [path\\to\\steamvr.vrsettings]

Without a path the file is found through %LOCALAPPDATA%\\openvr\\openvrpaths.vrpath, then the
Steam install folder from the registry.
"""

import json
import os
import shutil
import sys

ROLES = {
    "WCFBT_HIP": "TrackerRole_Waist",
    "WCFBT_LFOOT": "TrackerRole_LeftFoot",
    "WCFBT_RFOOT": "TrackerRole_RightFoot",
    "WCFBT_LKNEE": "TrackerRole_LeftKnee",
    "WCFBT_RKNEE": "TrackerRole_RightKnee",
    "WCFBT_CHEST": "TrackerRole_Chest",
    "WCFBT_LELBOW": "TrackerRole_LeftElbow",
    "WCFBT_RELBOW": "TrackerRole_RightElbow",
}


def find_settings():
    try:
        with open(os.path.join(os.environ["LOCALAPPDATA"], "openvr", "openvrpaths.vrpath"), encoding="utf-8") as f:
            for folder in json.load(f).get("config", []):
                path = os.path.join(folder, "steamvr.vrsettings")
                if os.path.exists(path):
                    return path
    except (OSError, ValueError, KeyError):
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = winreg.QueryValueEx(key, "SteamPath")[0]
        return os.path.join(os.path.normpath(steam), "config", "steamvr.vrsettings")
    except OSError:
        return None


def main():
    args = [a for a in sys.argv[1:] if a != "--remove"]
    remove = "--remove" in sys.argv[1:]
    path = args[0] if args else find_settings()
    if not path or not os.path.exists(path):
        print("steamvr.vrsettings not found; nothing to do")
        return
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    shutil.copyfile(path, path + ".webcamfbt-backup")
    trackers = data.setdefault("trackers", {})
    for serial, role in ROLES.items():
        key = f"/devices/webcamfbt/{serial}"
        if remove:
            trackers.pop(key, None)
        else:
            trackers[key] = role
    if remove and not trackers:
        data.pop("trackers")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=3)
    print(("Tracker roles removed: " if remove else "Tracker roles written: ") + path)


if __name__ == "__main__":
    main()
