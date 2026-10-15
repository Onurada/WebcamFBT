"""Session recordings: what the cameras saw and where the headset was, for tuning offline.

A recording holds, for every processed camera frame, MediaPipe's unfiltered 3D landmarks, their
visibility and image positions, plus every headset pose polled (~90 per second) and each
calibration made. tracker/replay.py runs the tracking on a recording and measures how the trackers
behaved (foot sliding, jumps, left/right flips, camera latency), so changes can be checked against
the player's real movement instead of guesses.
"""

import json
import os
import time

import numpy as np


class Recorder:
    MAX_SECONDS = 300

    def __init__(self, folder):
        self.folder = folder
        self.state = None  # None, "waiting" or "recording"
        self.start_at = self.stop_at = 0.0
        self._clear()

    def _clear(self):
        self.frames = []  # (t, camera, raw 33x3, MediaPipe's visibility 33, img 33x2, flipped)
        self.hmd = []  # (t, rot 3x3, pos 3, height, left hand 3, right hand 3)
        self.calibrations = []  # dicts
        self.meta = {}

    @property
    def active(self):
        return self.state == "recording"

    def start(self, now, seconds, delay, meta):
        self._clear()
        self.meta = dict(meta)
        self.start_at = now + max(0.0, delay)
        self.stop_at = self.start_at + min(max(5.0, seconds), self.MAX_SECONDS)
        self.state = "waiting"

    def cancel(self):
        self.state = None
        self._clear()

    def remaining(self, now):
        if self.state == "waiting":
            return self.start_at - now
        if self.state == "recording":
            return self.stop_at - now
        return 0.0

    def tick(self, now):
        """'started' when recording begins, 'due' when it should be saved, else None."""
        if self.state == "waiting" and now >= self.start_at:
            self.state = "recording"
            return "started"
        if self.state == "recording" and now >= self.stop_at:
            return "due"
        return None

    def frame(self, camera, r):
        if self.active and r is not None and r.raw is not None:
            self.frames.append((r.stamp, camera, r.raw.astype(np.float32), r.vis_raw.astype(np.float32),
                                r.img.astype(np.float32), bool(r.flipped)))

    def pose(self, hmd):
        if self.active and hmd is not None:
            hands = list(hmd.hands[:2]) + [np.full(3, np.nan)] * (2 - len(hmd.hands[:2]))
            self.hmd.append((hmd.t, hmd.rot, hmd.pos, hmd.height, hands[0], hands[1]))

    def calibration(self, now, calset):
        if self.state is None:
            return
        for i, c in calset.cals.items():
            if c.ready:
                self.calibrations.append({"t": now, "camera": i, "R": c.R.tolist(), "scale": c.scale,
                                          "floor": c.floor, "shoulders": c.shoulders.tolist(),
                                          "head_anchor": c.head_anchor})

    def save(self):
        """Writes the recording and returns its path (None if there was nothing to save)."""
        self.state = None
        if not self.frames:
            self._clear()
            return None
        os.makedirs(self.folder, exist_ok=True)
        path = os.path.join(self.folder, time.strftime("rec-%Y%m%d-%H%M%S.npz"))
        f = self.frames
        h = self.hmd
        np.savez_compressed(
            path,
            frame_t=np.array([x[0] for x in f]), frame_cam=np.array([x[1] for x in f], dtype=np.int16),
            raw=np.stack([x[2] for x in f]), vis=np.stack([x[3] for x in f]), img=np.stack([x[4] for x in f]),
            flipped=np.array([x[5] for x in f]),
            hmd_t=np.array([x[0] for x in h]), hmd_rot=np.array([x[1] for x in h]).reshape(-1, 3, 3),
            hmd_pos=np.array([x[2] for x in h]).reshape(-1, 3), hmd_height=np.array([x[3] for x in h]),
            hand_l=np.array([x[4] for x in h]).reshape(-1, 3), hand_r=np.array([x[5] for x in h]).reshape(-1, 3),
            calibrations=json.dumps(self.calibrations), meta=json.dumps(self.meta))
        self._clear()
        return path


def load(path):
    """A recording as a dict of arrays (calibrations and meta decoded)."""
    with np.load(path, allow_pickle=False) as z:
        rec = {k: z[k] for k in z.files}
    rec["calibrations"] = json.loads(str(rec["calibrations"]))
    rec["meta"] = json.loads(str(rec["meta"]))
    return rec
