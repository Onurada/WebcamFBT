"""Headset / connection naming (tracker/headset.py).
Run:  .venv\\Scripts\\python.exe tests\\test_headset.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tracker"))
import headset  # noqa: E402


def check(expected_name, expected_family, expected_conn, **props):
    info = headset.identify(**props)
    assert (info["name"], info["family"], info["connection"]) == (expected_name, expected_family, expected_conn), info


def test_quest_3_over_steam_link():
    # Values from a real SteamVR log: Steam Link reports TrackingSystemName "oculus" too.
    check("Meta Quest 3", "quest", "steam_link", model="Oculus Quest3", tracking_system="oculus",
          driver="vrlink", serial="VRLINKHMDQUEST3")


def test_meta_family_over_link():
    for model, name in (("Oculus Quest", "Meta Quest"), ("Oculus Quest2", "Meta Quest 2"), ("Miramar", "Meta Quest 2"),
                        ("Meta Quest 3S", "Meta Quest 3S"), ("Meta Quest Pro", "Meta Quest Pro"),
                        ("Meta Quest 3", "Meta Quest 3")):
        check(name, "quest", "meta_link", model=model, tracking_system="oculus", driver="oculus", serial="1WMHH000000000")


def test_rift_headsets_always_meta_link():
    check("Oculus Rift S", "rift", "meta_link", model="Oculus Rift S", tracking_system="oculus")
    check("Oculus Rift", "rift", "meta_link", model="Oculus Rift CV1", tracking_system="oculus")


def test_other_connections_and_headsets():
    check("Meta Quest 2", "quest", "virtual_desktop", model="Oculus Quest2", tracking_system="oculus", driver="VirtualDesktop")
    check("Meta Quest 3", "quest", "alvr", model="Meta Quest 3", tracking_system="ALVR Tracker", driver="alvr_server")
    check("Valve Index", "steamvr", "lighthouse", model="Index", tracking_system="lighthouse", driver="lighthouse")
    check("Some HMD 9000", "other", "other", model="Some HMD 9000", tracking_system="foo")
    check("?", "other", "other", model="")


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} tests passed")
