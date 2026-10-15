"""Which headset (and which PC connection) SteamVR is using, from the HMD's device properties.

WebcamFBT works with any headset SteamVR tracks: it only reads the HMD pose. This module just
names the headset for the app and picks the right wording, e.g. how to recenter on that headset.
"""

import re

FAMILIES = (  # (pattern on the squashed model name, display name, family); order matters: most specific first
    ("quest3s", "Meta Quest 3S", "quest"),
    ("quest3", "Meta Quest 3", "quest"),
    ("questpro", "Meta Quest Pro", "quest"),
    ("quest2", "Meta Quest 2", "quest"),
    ("miramar", "Meta Quest 2", "quest"),  # Quest 2 codename
    ("hollywood", "Meta Quest 2", "quest"),
    ("quest", "Meta Quest", "quest"),
    ("rifts", "Oculus Rift S", "rift"),
    ("rift", "Oculus Rift", "rift"),
    ("index", "Valve Index", "steamvr"),
    ("vive", "HTC Vive", "steamvr"),
    ("pico", "Pico", "pico"),
    ("reverb", "HP Reverb", "wmr"),
    ("windowsmixedreality", "Windows Mixed Reality", "wmr"),
    ("holographic", "Windows Mixed Reality", "wmr"),
)

# Matched against the driver name (Prop_ResourceRoot), serial number and tracking system name together.
# Steam Link reports TrackingSystemName "oculus" too (serial VRLINKHMD...), so it must come before "oculus".
CONNECTIONS = (  # (pattern, connection)
    ("vrlink", "steam_link"),
    ("virtualdesktop", "virtual_desktop"),
    ("alvr", "alvr"),
    ("oculus", "meta_link"),  # Meta Horizon Link app: USB Link, Air Link, and Rift / Rift S
    ("lighthouse", "lighthouse"),
    ("holographic", "wmr"),
    ("pico", "pico_connect"),
)


def _squash(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def identify(model, manufacturer="", tracking_system="", driver="", serial=""):
    """-> {"name", "family", "connection"} from the HMD's device properties (Prop_ModelNumber,
    Prop_ManufacturerName, Prop_TrackingSystemName, Prop_ResourceRoot = driver name, Prop_SerialNumber).
    Unknown values fall back to the raw model name and "other"."""
    m = _squash(model) + _squash(manufacturer)
    name, family = (model or "").strip() or "?", "other"
    for pattern, display, fam in FAMILIES:
        if pattern in m:
            name, family = display, fam
            break
    where = _squash(driver) + "|" + _squash(serial) + "|" + _squash(tracking_system)
    connection = next((c for pattern, c in CONNECTIONS if pattern in where), "other")
    if family == "rift":
        connection = "meta_link"  # PC headsets: always through the Meta Horizon Link app
    return {"name": name, "family": family, "connection": connection}
