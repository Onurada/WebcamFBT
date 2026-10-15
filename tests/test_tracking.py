"""Offline tests for the tracking math (no camera, no SteamVR needed).

Builds a synthetic skeleton in SteamVR space, sees it through a tilted/rotated "camera" the way
MediaPipe would report it, and checks calibration, tracker placement, smoothing and foot pinning.
Run:  .venv\\Scripts\\python.exe tests\\test_tracking.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tracker"))
import webcam_fbt as w  # noqa: E402

FLOOR = -0.3
FWD = np.array([1.0, 0.0, 0.0])  # person faces +X
UP = np.array([0.0, 1.0, 0.0])
RIGHT = np.cross(FWD, UP)
BASE = np.array([1.0, FLOOR, 2.0])
CAM_R = w.rot_y(math.radians(-130)) @ w.rot_between(UP, np.array([0, math.cos(0.26), math.sin(0.26)]))
MP_SCALE = 0.9  # MediaPipe reports people ~10% small


def at(r, u, f=0.0):
    return BASE + r * RIGHT + u * UP + f * FWD


def skeleton():
    W = np.zeros((33, 3))
    W[w.L_EYE], W[w.R_EYE] = at(-0.03, 1.65, 0.08), at(0.03, 1.65, 0.08)
    W[w.L_EAR], W[w.R_EAR] = at(-0.07, 1.63), at(0.07, 1.63)
    W[w.L_SH], W[w.R_SH] = at(-0.18, 1.42), at(0.18, 1.42)
    W[w.L_ELB], W[w.R_ELB] = at(-0.22, 1.15), at(0.22, 1.15)
    W[w.L_HIP], W[w.R_HIP] = at(-0.1, 0.95), at(0.1, 0.95)
    W[w.L_KNEE], W[w.R_KNEE] = at(-0.1, 0.5, 0.02), at(0.1, 0.5, 0.02)
    W[w.L_ANK], W[w.R_ANK] = at(-0.1, 0.08), at(0.1, 0.08)
    W[w.L_HEEL], W[w.R_HEEL] = at(-0.1, 0.03, -0.05), at(0.1, 0.03, -0.05)
    W[w.L_TOE], W[w.R_TOE] = at(-0.1, 0.02, 0.15), at(0.1, 0.02, 0.15)
    return W


def to_camera(W):
    hip_mid = (W[w.L_HIP] + W[w.R_HIP]) / 2
    return ((W - hip_mid) @ CAM_R) * MP_SCALE


def headset(W):
    rot = np.column_stack([RIGHT, UP, -FWD])
    ears = (W[w.L_EAR] + W[w.R_EAR]) / 2
    pos = ears - rot @ w.HMD_TO_EARS
    return w.HmdPose(rot, pos, pos[1] - FLOOR)


def calibrated(P, hmd):
    cal = w.Calibration()
    cal.phase, cal.t_phase = "collect", 0.0
    for i in range(30):
        cal.update(0.05 * i, P, np.ones(33), hmd)
    assert cal.update(5.0, P, np.ones(33), hmd) is True
    return cal


def cfg(**over):
    return {**w.DEFAULTS, "filter_min_cutoff": 1.0, "filter_beta": 1.0, "min_visibility": 0.5,
            "floor_lock": True, "ankle_height": 0.08, **over}


def test_calibration_and_placement():
    W = skeleton()
    P, hmd = to_camera(W), headset(W)
    cal = calibrated(P, hmd)
    assert abs(cal.floor - FLOOR) < 1e-6
    enabled = {n: True for n in w.TRACKERS}
    poses = w.compute_poses(P, np.ones(33), hmd, cal, cfg(), enabled, {})
    truth = {"hip": (W[w.L_HIP] + W[w.R_HIP]) / 2, "left_foot": W[w.L_ANK], "right_foot": W[w.R_ANK],
             "left_knee": W[w.L_KNEE], "right_knee": W[w.R_KNEE], "left_elbow": W[w.L_ELB], "right_elbow": W[w.R_ELB]}
    for tid, pos, q in poses:
        name = w.TRACKERS[tid]
        if name in truth:
            err = np.linalg.norm(pos - truth[name])
            assert err < 0.03, f"{name} off by {err * 100:.1f} cm"
        assert abs(np.linalg.norm(q) - 1) < 1e-6


def test_foot_jitter_and_step():
    W = skeleton()
    P, hmd = to_camera(W), headset(W)
    cal = calibrated(P, hmd)
    rng = np.random.default_rng(0)
    c = cfg()
    filt = w.OneEuro(*w.filter_params(c))
    feet = w.FootLock()
    enabled = {n: n in ("hip", "left_foot", "right_foot") for n in w.TRACKERS}
    still, step_end = [], None
    for f in range(160):
        t = 10 + f * 0.05
        Pt = P.copy()
        if f >= 100:  # left foot steps 30 cm forward over 0.5 s
            k = min(1.0, (f - 100) / 10)
            Pt[[w.L_ANK, w.L_HEEL, w.L_TOE, w.L_KNEE]] += (FWD * 0.30 * k @ CAM_R) * MP_SCALE
        noise = rng.normal(0, 1, Pt.shape) * np.array([0.01, 0.01, 0.03])  # depth is noisiest
        Pf = filt(Pt + noise, t)
        poses = w.compute_poses(Pf, np.ones(33), hmd, cal, c, enabled, {}, feet, t)
        lf = next(p for i, p, q in poses if w.TRACKERS[i] == "left_foot")
        if 40 <= f < 100:
            still.append(lf)
        step_end = lf
    still = np.array(still)
    jitter = np.linalg.norm(still - still.mean(0), axis=1)
    step_err = np.linalg.norm(step_end - (W[w.L_ANK] + FWD * 0.30))
    assert jitter.max() < 0.01, f"standing foot jitter {jitter.max() * 100:.1f} cm"
    assert step_err < 0.03, f"foot ended {step_err * 100:.1f} cm from where it stepped"


def test_calibration_file_roundtrip(tmp=os.path.join(os.environ.get("TEMP", "."), "webcamfbt_calib_test.json")):
    w.CALIBRATION_FILE = tmp
    c = w.Calibration()
    c.R, c.scale, c.floor, c.ready = w.rot_y(0.5), 1.1, -0.3, True
    c.save(0)
    d = w.Calibration()
    assert d.load(0) and np.allclose(d.R, c.R) and d.scale == 1.1 and d.from_file
    assert not w.Calibration().load(1)  # other camera: must not reuse
    os.remove(tmp)


def test_config_validation():
    cfg, problems = w.validate_config({"filter_beta": "abc", "min_visibility": 7, "model": "huge", "camera_index": 2.0,
                                       "trackers": {"hip": False, "left_foot": "yes"}, "floor_lock": 1,
                                       "auto_start": True})
    assert cfg["filter_beta"] == w.DEFAULTS["filter_beta"]
    assert cfg["min_visibility"] == 1.0  # clamped
    assert cfg["model"] == "full"
    assert cfg["camera_index"] == 2 and isinstance(cfg["camera_index"], int)
    assert cfg["trackers"]["hip"] is False and cfg["trackers"]["left_foot"] is True  # bad entry -> default
    assert cfg["floor_lock"] is True  # 1 is not a bool
    assert cfg["auto_start"] is True  # the app's own keys survive
    bad = {kw["key"] for name, kw in problems}
    assert {"filter_beta", "min_visibility", "model", "floor_lock"} <= bad
    assert w.validate_config({})[1] == []  # missing keys are not problems


def test_corrupt_config_is_set_aside():
    path = os.path.join(os.environ.get("TEMP", "."), "webcamfbt_cfg_test.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    cfg, problems = w.load_config(path)
    assert cfg["model"] == "full" and problems[0][0] == "config_corrupt"
    backup = os.path.join(os.path.dirname(path), problems[0][1]["backup"])
    assert os.path.exists(backup) and not os.path.exists(path)
    os.remove(backup)


def test_recenter_is_detected_and_corrected():
    W = skeleton()
    P, hmd = to_camera(W), headset(W)
    cal = calibrated(P, hmd)
    enabled = {n: True for n in w.TRACKERS}
    # Quest recenter: the whole tracking space turns 70° and shifts, the person did not move.
    R, t = w.rot_y(math.radians(70)), np.array([0.4, 0.05, -1.2])
    W2 = W @ R.T + t
    hmd.t = 0.0
    hmd2 = w.HmdPose(R @ hmd.rot, R @ hmd.pos + t, hmd.height, t=0.05)
    jump = w.space_jump(hmd, hmd2)
    assert jump is not None
    w.apply_space_jump(cal, jump)
    poses = w.compute_poses(P, np.ones(33), hmd2, cal, cfg(), enabled, {})
    hip = next(p for i, p, q in poses if w.TRACKERS[i] == "hip")
    assert np.linalg.norm(hip - (W2[w.L_HIP] + W2[w.R_HIP]) / 2) < 0.03
    assert abs(cal.floor - (FLOOR + t[1])) < 1e-6


def test_fast_head_turn_is_not_a_recenter():
    rot0 = np.eye(3)
    a = w.HmdPose(rot0, np.zeros(3), 1.6, t=0.0)
    turn = math.radians(45)  # 45° in 50 ms = 900°/s, backed by the reported angular velocity
    b = w.HmdPose(w.rot_y(turn), np.array([0.05, 0, 0]), 1.6, vel=np.array([1.0, 0, 0]),
                  ang_vel=np.array([0, turn / 0.05, 0]), t=0.05)
    a.ang_vel = b.ang_vel.copy()
    a.vel = b.vel.copy()
    assert w.space_jump(a, b) is None
    assert w.space_jump(a, w.HmdPose(rot0, np.zeros(3), 1.6, t=1.0)) is None  # long gap: no guess


def test_driver_watch():
    class FakeVR:
        connected = True
        count = 0

        def driver_trackers(self):
            return self.count

    import socket
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    busy_port = blocker.getsockname()[1]
    vr = FakeVR()
    free = w.DriverWatch(busy_port + 1 if busy_port < 65535 else busy_port - 1)
    assert free.update(vr, True, 0.0) is None  # grace period
    assert free.update(vr, True, 5.0) in ("driver_not_loaded", "port_busy")
    busy = w.DriverWatch(busy_port)
    busy.update(vr, True, 0.0)
    assert busy.update(vr, True, 5.0) == "port_busy"
    vr.count = 3
    assert busy.update(vr, True, 11.0) == "driver_ok"
    assert busy.update(vr, True, 17.0) is None  # no repeats
    blocker.close()


def test_messages_complete():
    import messages
    for key, entry in messages.MESSAGES.items():
        for lang in messages.LANGUAGES:
            assert lang in entry, f"{key} has no '{lang}' text"


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} tests passed")
