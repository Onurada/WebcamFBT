"""Camera discovery: names of the video devices in the same order OpenCV's CAP_DSHOW numbers them,
plus what kind of device each is, so the app can show "Microsoft LifeCam VX-1000" instead of
"Camera 0" and warn about virtual cameras (OBS, Meta Quest Link, ...) that never show the player.

Kept free of MediaPipe so it starts fast:
    python cameras.py            names only (about a second)
    python cameras.py --probe    also opens each real camera to check it works and read its size
Both print one JSON line: {"type": "cameras", "list": [{index, name, kind, width, height, working}]}.
"""

import argparse
import json
import sys

# Software cameras that show other content (screen capture, headset view), never the room.
VIRTUAL = ("obs virtual", "obs-camera", "meta quest", "oculus", "virtual cam", "snap camera", "xsplit", "manycam",
           "streamlabs", "nvidia broadcast", "splitcam", "vcam")
# Phone apps that turn a phone into a webcam: real cameras, worth using as extra cameras.
PHONE = ("droidcam", "iriun", "epoccam", "camo", "ivcam", "elgato epoccam")


def device_kind(name):
    n = name.lower()
    if any(p in n for p in PHONE):
        return "phone"
    if any(p in n for p in VIRTUAL):
        return "virtual"
    return "webcam"


def device_names():
    """DirectShow video input devices in enumeration order (= cv2.CAP_DSHOW index)."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        return list(FilterGraph().get_input_devices())
    except Exception:
        return []


def probe(index):
    """Opens the camera once: (width, height) if it delivers a frame, else None."""
    import cv2
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    try:
        if not cap.isOpened():
            return None
        ok, frame = cap.read()
        return (int(frame.shape[1]), int(frame.shape[0])) if ok else None
    finally:
        cap.release()


def list_devices(do_probe=False, probe_virtual=False):
    names = device_names()
    devices = []
    for index, name in enumerate(names):
        kind = device_kind(name)
        entry = {"index": index, "name": name, "kind": kind, "width": None, "height": None, "working": None}
        if do_probe and (kind != "virtual" or probe_virtual):
            size = probe(index)
            entry["working"] = size is not None
            if size:
                entry["width"], entry["height"] = size
        devices.append(entry)
    if not names and do_probe:  # no DirectShow names (pygrabber missing): fall back to trying indices
        for index in range(6):
            size = probe(index)
            if size:
                devices.append({"index": index, "name": None, "kind": "webcam", "width": size[0], "height": size[1],
                                "working": True})
    return devices


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", action="store_true", help="open each real camera to check it works")
    ap.add_argument("--probe-virtual", action="store_true", help="also open virtual cameras")
    args = ap.parse_args()
    sys.stdout.write(json.dumps({"type": "cameras", "list": list_devices(args.probe, args.probe_virtual)}) + "\n")


if __name__ == "__main__":
    main()
