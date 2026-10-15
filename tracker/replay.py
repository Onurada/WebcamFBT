"""Runs the tracking on a recording (see recorder.py) and measures how the trackers behaved.

    python tracker\\replay.py logs\\recordings\\rec-....npz            # report, old vs new pipeline
    python tracker\\replay.py rec.npz --sweep-latency                   # which camera_latency fits best

Nobody knows where the player's feet really were, so the report uses what must hold for real feet:
a foot on the floor does not slide, trackers do not jump half a metre in one frame, the body faces
roughly where the head looks. It also estimates the camera's latency by lining up the hips' sideways
motion in the picture with the headset's.
"""

import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recorder  # noqa: E402
import webcam_fbt as w  # noqa: E402

PROCESS_DELAY = 0.04  # s from read() returning to the main loop using the result (model time)

OLD = {"room_feet": False, "carry": False, "flip": False, "edge": False}
NEW = {"room_feet": True, "carry": True, "flip": True, "edge": True}


class Hmd:
    """Headset poses of a recording, looked up by time."""

    def __init__(self, rec):
        self.t = rec["hmd_t"]
        self.rot, self.pos, self.height = rec["hmd_rot"], rec["hmd_pos"], rec["hmd_height"]

    def at(self, t):
        if len(self.t) == 0:
            return None
        i = int(np.clip(np.searchsorted(self.t, t), 1, len(self.t) - 1))
        a, b = i - 1, i
        k = float(np.clip((t - self.t[a]) / max(self.t[b] - self.t[a], 1e-6), 0.0, 1.0))
        near = a if k < 0.5 else b
        return w.HmdPose(self.rot[near], self.pos[a] + (self.pos[b] - self.pos[a]) * k,
                         float(self.height[a] + (self.height[b] - self.height[a]) * k), t=t)

    def speed(self, t, dt=0.1):
        a, b = self.at(t - dt), self.at(t)
        if a is None:
            return 0.0
        d = b.pos - a.pos
        return math.hypot(d[0], d[2]) / dt


def calibration_for(rec, camera, t):
    """The camera's calibration in force at time t (the last one made before it), or None."""
    best = None
    for c in rec["calibrations"]:
        if c["camera"] == camera and c["t"] <= t and (best is None or c["t"] >= best["t"]):
            best = c
    if best is None:
        return None
    cal = w.Calibration(camera, sound=False)
    cal.R, cal.scale, cal.floor = np.array(best["R"]), best["scale"], best["floor"]
    cal.shoulders, cal.head_anchor = np.array(best["shoulders"]), best["head_anchor"]
    cal.ready = True
    return cal


def run(rec, opts, cfg=None, latency=None):
    """The engine's per-frame tracking over the recording. Returns per-frame records."""
    cfg = {**w.DEFAULTS, **(cfg or {})}
    cfg["trackers"] = {n: True for n in w.TRACKERS}
    latency = cfg["camera_latency"] if latency is None else latency
    hmd = Hmd(rec)
    filters = {}
    last, feet, carry = {}, w.FootLock(), (w.Carry() if opts["carry"] else None)
    room_filters = {}
    enabled = {n: True for n in w.TRACKERS}
    out = []
    for k in np.argsort(rec["frame_t"]):
        t, cam = float(rec["frame_t"][k]), int(rec["frame_cam"][k])
        cal = calibration_for(rec, cam, t)
        if cal is None:
            continue
        raw, img = rec["raw"][k].astype(float), rec["img"][k].astype(float)
        vis = rec["vis"][k].astype(float)
        if opts["edge"]:
            vis = w.edge_visibility(vis, img)
        now = t + PROCESS_DELAY
        hmd_now = hmd.at(now)
        if hmd_now is None:
            continue
        P, flipped = raw, False
        if opts["flip"]:
            ref = (cal.R.T @ np.array([0.0, 1.0, 0.0]), cal.R.T @ -hmd_now.rot[:, 2])
            P, flipped = w.fix_back_view(raw, ref)
        f = filters.setdefault(cam, w.OneEuro(*w.filter_params(cfg)))
        Pu, P = P, f(P, t, w.visibility_weight(vis))
        then = hmd.at(t - latency) if opts["room_feet"] else None
        W = w.tracked_points(P, Pu, vis, t, cal, hmd_now, then,
                             room_filters.setdefault(cam, w.OneEuro(*w.filter_params(cfg))))
        floor = w.live_floor(hmd_now, cal.floor)
        poses = w.build_poses(W, vis, floor, cfg, enabled, last, feet, now, carry)
        uncorrected = w.facing_error(raw, cal.R.T @ np.array([0.0, 1.0, 0.0]), cal.R.T @ -hmd_now.rot[:, 2])
        out.append({"t": now, "poses": {w.TRACKERS[tid]: np.array(pos) for tid, pos, _ in poses},
                    "floor": floor, "hmd_speed": hmd.speed(now), "flipped": flipped,
                    "back_view": uncorrected >= w.BACK_VIEW,
                    "feet_hidden": [bool(vis[w.REQUIRED[n]].min() < cfg["min_visibility"])
                                    for n in ("left_foot", "right_foot")]})
    return out


def metrics(frames, cfg=None):
    cfg = {**w.DEFAULTS, **(cfg or {})}
    rest = cfg["ankle_height"] + 0.04
    slide_walk, slide_still, jumps, jitter = [], [], 0, []
    for a, b in zip(frames, frames[1:]):
        dt = b["t"] - a["t"]
        if dt <= 0 or dt > 0.2:
            continue
        for name in w.TRACKERS:
            if name in a["poses"] and name in b["poses"]:
                d = np.linalg.norm(b["poses"][name] - a["poses"][name])
                if d > 0.2:
                    jumps += 1
        for name in ("left_foot", "right_foot"):
            if name not in a["poses"] or name not in b["poses"]:
                continue
            pa, pb = a["poses"][name], b["poses"][name]
            grounded = pa[1] < a["floor"] + rest and pb[1] < b["floor"] + rest
            speed = math.hypot(pb[0] - pa[0], pb[2] - pa[2]) / dt
            if grounded and b["hmd_speed"] > 0.3:
                slide_walk.append(speed)
            if b["hmd_speed"] < 0.05:
                jitter.append(np.linalg.norm(pb - pa))
                if grounded:
                    slide_still.append(speed)
    n = max(len(frames), 1)

    def pct(v, q):
        return float(np.percentile(v, q)) if v else float("nan")

    return {
        "frames": len(frames),
        "foot slide walking, median m/s": pct(slide_walk, 50),
        "foot slide walking, p90 m/s": pct(slide_walk, 90),
        "foot slide standing, median m/s": pct(slide_still, 50),
        "foot jitter standing, mean cm": float(np.mean(jitter) * 100) if jitter else float("nan"),
        "jumps > 20 cm": jumps,
        "back view frames %": 100.0 * sum(f["back_view"] for f in frames) / n,
        "flips corrected %": 100.0 * sum(f["flipped"] for f in frames) / n,
        "left foot hidden %": 100.0 * sum(f["feet_hidden"][0] for f in frames) / n,
        "right foot hidden %": 100.0 * sum(f["feet_hidden"][1] for f in frames) / n,
    }


def estimate_latency(rec, camera=None):
    """Seconds by which the picture trails the headset: the lag that best lines up the hips' sideways
    speed in the picture with the headset's speed along the camera's sideways axis."""
    t = rec["frame_t"]
    order = np.argsort(t)
    cams = rec["frame_cam"][order]
    camera = int(cams[0]) if camera is None else camera
    sel = order[cams == camera]
    cal = calibration_for(rec, camera, float(t[sel][-1])) if len(sel) else None
    if cal is None or len(sel) < 40:
        return None
    hmd = Hmd(rec)
    ts = t[sel]
    x = (rec["img"][sel][:, w.L_HIP, 0] + rec["img"][sel][:, w.R_HIP, 0]) / 2
    side = cal.R[:, 0]  # the camera's +x (picture right) in VR space
    side = side - side[1] * np.array([0.0, 1.0, 0.0])
    side = side / max(np.linalg.norm(side), 1e-6)
    a = np.gradient(x, ts)
    best, best_lag = -2.0, None
    for lag in np.arange(0.0, 0.301, 0.01):
        v = np.array([(hmd.at(ti - lag + 0.02).pos - hmd.at(ti - lag - 0.02).pos) @ side / 0.04 for ti in ts])
        if np.std(a) < 1e-9 or np.std(v) < 1e-9:
            return None
        c = float(np.corrcoef(a, v)[0, 1])
        c = max(c, -c)  # picture x may run either way along the side axis
        if c > best:
            best, best_lag = c, float(lag)
    return {"latency_s": best_lag, "correlation": best}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recording")
    ap.add_argument("--sweep-latency", action="store_true")
    args = ap.parse_args()
    rec = recorder.load(args.recording)
    span = rec["frame_t"].max() - rec["frame_t"].min() if len(rec["frame_t"]) else 0
    print(f"{os.path.basename(args.recording)}: {len(rec['frame_t'])} frames, {span:.0f} s, "
          f"{len(rec['hmd_t'])} headset poses, {len(rec['calibrations'])} calibrations, meta {rec['meta']}")
    if not rec["calibrations"]:
        print("No calibration in the recording: calibrate before (or while) recording.")
        return
    lat = estimate_latency(rec)
    print("estimated camera latency:", lat)
    old, new = metrics(run(rec, OLD)), metrics(run(rec, NEW))
    print(f"\n{'':36s} {'old':>10s} {'new':>10s}")
    for key in old:
        print(f"{key:36s} {old[key]:10.3f} {new[key]:10.3f}")
    if args.sweep_latency:
        print("\ncamera_latency sweep (new pipeline):")
        for latency in np.arange(0.0, 0.201, 0.02):
            m = metrics(run(rec, NEW, latency=latency))
            print(f"  {latency:.2f} s: slide walking median {m['foot slide walking, median m/s']:.3f} m/s, "
                  f"p90 {m['foot slide walking, p90 m/s']:.3f}")


if __name__ == "__main__":
    main()
