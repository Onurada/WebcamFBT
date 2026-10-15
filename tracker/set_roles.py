"""Writes WebcamFBT tracker roles into SteamVR's steamvr.vrsettings (run while SteamVR is closed).

Same result as assigning them by hand in SteamVR > Settings > Controllers > Manage Trackers.
A backup is saved next to the file as steamvr.vrsettings.webcamfbt-backup.
"""

import json
import shutil
import sys

PATH = r"C:\Program Files (x86)\Steam\config\steamvr.vrsettings"
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


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else PATH
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    shutil.copyfile(path, path + ".webcamfbt-backup")
    trackers = data.setdefault("trackers", {})
    for serial, role in ROLES.items():
        trackers[f"/devices/webcamfbt/{serial}"] = role
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=3)
    print("Tracker rolleri yazildi:", path)


if __name__ == "__main__":
    main()
