"""A synthetic recording of a player side-stepping past a camera with 100 ms camera latency, feet
leaving the picture and a stretch seen "from behind" (MediaPipe's left/right mix-up), run through
tracker/replay.py: the new pipeline must keep planted feet from sliding, carry hidden feet, undo the
flip, and the latency estimate must find the 100 ms.
Run:  .venv\\Scripts\\python.exe tests\\test_replay.py
"""

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tracker"))
import recorder  # noqa: E402
import replay  # noqa: E402
import test_tracking as t  # noqa: E402

w = t.w
LATENCY = 0.10
SPEED, PERIOD = 0.6, 1.2  # side-step speed (m/s), stride period (s)
WALK, STAND = 1.2, 0.8  # walk one stride, stand still, repeat (starting and stopping is where feet slid)
START = -1.1  # m along RIGHT where the walk begins (stays in the picture)
CAM_R = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]])  # sees the player from the front
CAM_POS = t.BASE + t.FWD * 4.0 + t.UP * 1.0
FEET = {0: [w.L_ANK, w.L_HEEL, w.L_TOE], 1: [w.R_ANK, w.R_HEEL, w.R_TOE]}


def walked(time):
    """Seconds spent walking up to time (the clock of the gait)."""
    cycles, u = divmod(time, WALK + STAND)
    return cycles * WALK + min(u, WALK)


def foot_offset(tau, phase):
    """Sideways position (m) and lift of a foot: planted half the stride, swinging the other half."""
    stride = SPEED * PERIOD
    u = (tau + phase) % PERIOD
    n = math.floor((tau + phase) / PERIOD)
    if u < PERIOD / 2:
        return n * stride, 0.0
    k = (u - PERIOD / 2) / (PERIOD / 2)
    return n * stride + stride * k, 0.08 * math.sin(math.pi * k)


def body(time):
    tau = walked(time)
    W = t.skeleton() + t.RIGHT * (START + SPEED * tau)
    for side, phase in ((0, 0.0), (1, PERIOD / 2)):
        x, lift = foot_offset(tau, phase)
        W[FEET[side]] = t.skeleton()[FEET[side]] + t.RIGHT * (START + x) + t.UP * lift
        knee = w.L_KNEE if side == 0 else w.R_KNEE
        W[knee] = (W[w.L_HIP if side == 0 else w.R_HIP] + W[FEET[side][0]]) / 2 + t.FWD * 0.02
    return W


def project(W):
    d = (W - CAM_POS) @ CAM_R  # camera space: x right, y up, +z towards the camera
    depth = -d[:, 2]
    f = 0.5 / math.tan(math.radians(30))
    return np.column_stack([0.5 + f * d[:, 0] / depth, 0.5 - f * d[:, 1] / depth * 4 / 3])


HIDDEN = (2.0, 2.8)  # feet out of the picture
BACK = (4.6, 5.0)  # seen "from behind"


def make_recording(path, seconds=6.5):
    rec = recorder.Recorder(os.path.dirname(path))
    rec.start(0.0, seconds + 1, 0.0, {"synthetic": True})
    rec.tick(0.0)
    for k in range(int(seconds * 90)):
        tt = k / 90
        hmd = t.headset(body(tt))
        hmd.t = tt
        rec.pose(hmd)
    cal = w.Calibration(0, sound=False)
    cal.R, cal.scale, cal.floor, cal.ready = CAM_R, 1 / t.MP_SCALE, t.FLOOR, True

    class Set:
        cals = {0: cal}
    rec.calibration(0.0, Set)
    for k in range(int((seconds - 0.5) * 20)):
        shot = 0.3 + k / 20
        W = body(shot)
        r = w.Result(None, shot + LATENCY)
        r.raw = ((W - (W[w.L_HIP] + W[w.R_HIP]) / 2) @ CAM_R) * t.MP_SCALE
        r.img = project(W)
        r.vis_raw = np.ones(33)
        if HIDDEN[0] <= shot < HIDDEN[1]:  # MediaPipe guesses the cut-off feet, half sure
            for i in FEET[0] + FEET[1]:
                r.vis_raw[i] = 0.55
                r.raw[i] += np.array([0.0, 0.2, 0.1])
                r.img[i, 1] = 1.02
        if BACK[0] <= shot < BACK[1]:  # left/right swapped, depth mirrored
            r.raw = r.raw[w.SWAP_LR] * np.array([1.0, 1.0, -1.0])
        rec.frame(0, r)
    rec.stop_at = 0.0
    return rec.save()


def against_truth(frames):
    """Mean distance of the foot trackers from the real feet at the time they are shown, and how fast
    the trackers moved while the real foot stood still (sliding)."""
    errors, slide = [], []
    for a, b in zip(frames, frames[1:]):
        truth_a, truth_b = body(a["t"]), body(b["t"])
        for name, joints in (("left_foot", FEET[0]), ("right_foot", FEET[1])):
            if name not in b["poses"]:
                continue
            errors.append(np.linalg.norm(b["poses"][name] - truth_b[joints[0]]))
            planted = np.linalg.norm(truth_b[joints[0]] - truth_a[joints[0]]) < 1e-6
            if planted and name in a["poses"]:
                d = b["poses"][name] - a["poses"][name]
                slide.append(math.hypot(d[0], d[2]) / (b["t"] - a["t"]))
    return float(np.mean(errors)), float(np.mean(slide))


def test_replay_old_vs_new():
    folder = os.path.join(os.environ.get("TEMP", "."), "webcamfbt_replay_test")
    path = make_recording(os.path.join(folder, "x.npz"))
    rec = recorder.load(path)
    old_frames, new_frames = replay.run(rec, replay.OLD), replay.run(rec, replay.NEW, latency=LATENCY)
    old, new = replay.metrics(old_frames), replay.metrics(new_frames)
    (old_err, old_slide), (new_err, new_slide) = against_truth(old_frames), against_truth(new_frames)
    print(f"foot error old {old_err * 100:.1f} cm, new {new_err * 100:.1f} cm; "
          f"planted foot sliding old {old_slide:.3f} m/s, new {new_slide:.3f} m/s")
    print("old", old)
    print("new", new)
    # A swinging foot still trails by the camera's latency (it catches up once planted), so the gain
    # is in the overall error, the cut-off feet and above all the back view.
    assert new_err < 0.8 * old_err and new_slide < old_slide, (old_err, new_err, old_slide, new_slide)
    in_back = lambda frames: [f for f in frames if BACK[0] <= f["t"] - LATENCY - replay.PROCESS_DELAY < BACK[1]]
    assert against_truth(in_back(new_frames))[0] < 0.4 * against_truth(in_back(old_frames))[0]
    in_hidden = lambda frames: [f for f in frames if HIDDEN[0] <= f["t"] - LATENCY - replay.PROCESS_DELAY < HIDDEN[1]]
    assert against_truth(in_hidden(new_frames))[0] < 0.8 * against_truth(in_hidden(old_frames))[0]
    assert new["flips corrected %"] > 5 and new["jumps > 20 cm"] < old["jumps > 20 cm"], (old, new)
    assert new["left foot hidden %"] > 10  # edge distrust noticed the cut-off feet
    lat = replay.estimate_latency(rec)
    assert lat and abs(lat["latency_s"] - LATENCY) <= 0.03, lat
    os.remove(path)


def test_hidden_foot_follows_the_body():
    """A foot out of the picture moves along with the player instead of staying behind."""
    cfg = t.cfg()
    enabled = {n: n in ("hip", "left_foot") for n in w.TRACKERS}
    carry, last = w.Carry(), {}
    W0 = t.skeleton()
    vis = np.ones(33)
    w.build_poses(W0, vis, t.FLOOR, cfg, enabled, last, None, 0.0, carry)
    hidden = vis.copy()
    hidden[[w.L_ANK, w.L_HEEL, w.L_TOE]] = 0.1
    W1 = W0 + t.RIGHT * 1.0
    poses = {w.TRACKERS[i]: p for i, p, _ in w.build_poses(W1, hidden, t.FLOOR, cfg, enabled, last, None, 0.1, carry)}
    assert np.linalg.norm(poses["left_foot"] - W1[w.L_ANK]) < 0.05, poses["left_foot"] - W1[w.L_ANK]
    # seen again 20 cm away from where it was carried: glides there instead of jumping
    W2 = W1.copy()
    W2[[w.L_ANK, w.L_HEEL, w.L_TOE]] += t.FWD * 0.2
    first = {w.TRACKERS[i]: p for i, p, _ in w.build_poses(W2, vis, t.FLOOR, cfg, enabled, last, None, 0.2, carry)}
    assert np.linalg.norm(first["left_foot"] - W2[w.L_ANK]) > 0.15  # still near the carried spot
    later = {w.TRACKERS[i]: p for i, p, _ in w.build_poses(W2, vis, t.FLOOR, cfg, enabled, last, None, 0.6, carry)}
    assert np.linalg.norm(later["left_foot"] - W2[w.L_ANK]) < 1e-6


def test_back_view_is_turned_around():
    W = t.skeleton()
    hmd = t.headset(W)
    P = t.camera_view(W, CAM_R)
    up, fwd = CAM_R.T @ np.array([0.0, 1.0, 0.0]), CAM_R.T @ -hmd.rot[:, 2]
    same, flipped = w.fix_back_view(P, (up, fwd))
    assert not flipped and same is P
    mistaken = P[w.SWAP_LR] * np.array([1.0, 1.0, -1.0])
    fixed, flipped = w.fix_back_view(mistaken, (up, fwd))
    assert flipped and np.allclose(fixed, P)


def test_edge_landmarks_count_as_hidden():
    vis = np.ones(33)
    img = np.full((33, 2), 0.5)
    img[w.L_ANK] = (0.5, 0.995)
    img[w.R_ANK] = (1.2, 0.5)
    out = w.edge_visibility(vis, img)
    assert out[w.L_ANK] <= 0.2 and out[w.R_ANK] <= 0.2 and out[w.L_HIP] == 1.0


def test_hmd_history_lookup():
    h = w.HmdHistory()
    for k in range(100):
        h.add(w.HmdPose(np.eye(3), np.array([k * 0.01, 0.0, 0.0]), 1.6, t=k * 0.01))
    p = h.at(0.505)
    assert abs(p.pos[0] - 0.505) < 1e-9
    assert h.at(-5).pos[0] == 0.0 and h.at(5).pos[0] == 0.99


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok  ", fn.__name__)
    print(f"{len(tests)} tests passed")
