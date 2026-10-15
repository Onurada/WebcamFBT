"""WebcamFBT - webcam full body tracking for SteamVR.

Reads the webcam, estimates the body skeleton with MediaPipe Pose, anchors it to the
headset pose from SteamVR and streams tracker poses over UDP to the webcamfbt driver.

Without --ipc an OpenCV preview window is shown (keys: C calibrate, P pause, [ ] yaw,
- = scale, Q/Esc quit). With --ipc there is no window: the WPF app drives the engine with
JSON lines on stdin and receives status / preview frames / log lines as JSON lines on stdout.
Global hotkeys (default F8 = calibrate, F9 = pause/resume, configurable) work in both modes.
"""

import argparse
import base64
import collections
import ctypes
import ctypes.wintypes
import json
import logging
import logging.handlers
import math
import os
import queue
import re
import socket
import struct
import sys
import threading
import time
import winsound

import cv2
import numpy as np
import openvr

# MediaPipe takes ~1.5 s to import; it is loaded by load_mediapipe() while the cameras open.
mp = mp_tasks = vision = None


def load_mediapipe():
    global mp, mp_tasks, vision
    if mp is None:
        import mediapipe
        from mediapipe.tasks import python as tasks
        from mediapipe.tasks.python import vision as vis
        mp, mp_tasks, vision = mediapipe, tasks, vis

import cameras
import headset
import messages
import placement
import recorder
from messages import T, ascii_fold

HERE = os.path.dirname(os.path.abspath(__file__))


def read_version():
    try:
        with open(os.path.join(HERE, "..", "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "?"


VERSION = read_version()
CALIBRATION_FILE = os.path.join(HERE, "calibration.json")

# Order must match kTrackers in driver_webcamfbt.cpp
TRACKERS = ["hip", "left_foot", "right_foot", "left_knee", "right_knee", "chest", "left_elbow", "right_elbow"]
MAGIC = 0x54424657
PROTOCOL = 2  # must match kProtocol in driver_webcamfbt.cpp (sent after the tracker records)

# MediaPipe BlazePose landmark indices
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 2, 5, 7, 8
L_SH, R_SH, L_ELB, R_ELB = 11, 12, 13, 14
L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANK, R_ANK = 23, 24, 25, 26, 27, 28
L_HEEL, R_HEEL, L_TOE, R_TOE = 29, 30, 31, 32

# Landmarks that must be visible for each tracker to update (otherwise it holds its last pose)
REQUIRED = {
    "hip": [L_HIP, R_HIP],
    "left_foot": [L_ANK],
    "right_foot": [R_ANK],
    "left_knee": [L_KNEE],
    "right_knee": [R_KNEE],
    "chest": [L_SH, R_SH],
    "left_elbow": [L_ELB],
    "right_elbow": [R_ELB],
}

# The foot's heel and toe set its direction only: with them cut off by the bottom of the picture
# (common when standing close) the ankle still places the foot, which then keeps its last direction.
FOOT_DIRECTION = {"left_foot": [L_HEEL, L_TOE], "right_foot": [R_HEEL, R_TOE]}

# HMD origin -> midpoint between the ears, in HMD local space (x right, y up, +z backwards)
HMD_TO_EARS = np.array([0.0, -0.02, 0.09])

# The head does not have to be in the picture: the body can hang from the headset by the shoulders.
# Standing adults' shoulder height is about 0.874 of their eye height, and the shoulders' midpoint sits
# under the ears; calibration measures the player's own offset whenever it sees the ears.
SHOULDER_EYE_RATIO = 0.874
HMD_TO_SHOULDERS_Z = 0.08
HEAD = [L_EYE, R_EYE, L_EAR, R_EAR]
BODY = [L_SH, R_SH, L_HIP, R_HIP, L_ANK, R_ANK]  # must be seen to calibrate (head optional)


def default_shoulder_offset(eye_height):
    """HMD origin -> shoulders' midpoint in HMD local space (looking ahead), from the eye height."""
    return np.array([0.0, -(1.0 - SHOULDER_EYE_RATIO) * eye_height, HMD_TO_SHOULDERS_Z])



DEFAULTS = {
    "camera_index": 0,
    "camera_width": 640,
    "camera_height": 480,
    "camera_fps": 30,
    "model": "full",
    "trackers": {"hip": True, "left_foot": True, "right_foot": True, "left_knee": False, "right_knee": False,
                 "chest": False, "left_elbow": False, "right_elbow": False},
    "filter_min_cutoff": 1.0,
    "filter_beta": 1.0,
    "depth_smoothing": 0.6,
    "min_visibility": 0.5,
    "floor_lock": True,
    "foot_lock": True,
    "ankle_height": 0.08,
    "mirror_preview": True,
    "udp_port": 39570,
    "language": None,  # None = follow the Windows display language
    "remember_calibration": False,  # a Quest's tracking space changes between sessions: calibrate each time
    "auto_calibrate": True,  # calibrate by itself once the player stands still in view (only when not calibrated)
    "gesture_calibrate": True,  # both hands above the head for 2 s recalibrates
    "high_priority": True,
    "preview_fps": 20,
    "hotkey_calibrate": "F8",
    "hotkey_pause": "F9",
    "extra_cameras": [],  # optional extra cameras fused with the main one (at most 2)
    "camera_hfov": 60.0,  # horizontal field of view assumed by the setup assistant
    "camera_latency": 0.10,  # s from exposure to read() (measured 0.10 s on a LifeCam VX-1000 with replay.py); feet use the headset pose of then
}
RANGES = {
    "camera_latency": (0.0, 0.3),
    "camera_index": (0, 15), "camera_width": (160, 3840), "camera_height": (120, 2160), "camera_fps": (5, 120),
    "filter_min_cutoff": (0.05, 10.0), "filter_beta": (0.0, 20.0), "depth_smoothing": (0.0, 1.0),
    "min_visibility": (0.0, 1.0), "ankle_height": (0.0, 0.3), "udp_port": (1024, 65535), "preview_fps": (1, 60),
    "camera_hfov": (40.0, 120.0),
}
CHOICES = {"model": ("lite", "full", "heavy"), "language": (None,) + messages.LANGUAGES}

LOG_DIR = os.path.normpath(os.path.join(HERE, "..", "logs"))
_file_log = None  # logging.Logger once setup_file_log() ran
_out = print  # where log lines are shown: the console, or Ipc.log in --ipc mode


def log(*args):
    text = " ".join(str(a) for a in args)
    if _file_log:
        _file_log.info(text)
    _out(text)


def setup_file_log():
    """logs/engine.log, rotated at 1 MB (3 old files kept), including crash tracebacks."""
    global _file_log
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(os.path.join(LOG_DIR, "engine.log"), maxBytes=1_000_000,
                                                       backupCount=3, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _file_log = logging.getLogger("webcamfbt")
    _file_log.setLevel(logging.INFO)
    _file_log.addHandler(handler)
    threading.excepthook = lambda a: _file_log.error("thread crashed", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def validate_config(raw):
    """Returns (config, problems). Missing keys get defaults; wrong types and out-of-range values are
    replaced (numbers are clamped) and reported. Unknown keys (the app's own settings) are kept."""
    cfg, problems = dict(raw), []

    def fix(key, value, fallback):
        problems.append(("config_invalid", {"key": key, "value": value, "default": fallback}))
        return fallback

    for key, default in DEFAULTS.items():
        if key not in raw:
            cfg[key] = json.loads(json.dumps(default))
            continue
        value = raw[key]
        if key == "trackers":
            trackers = dict(default)
            if isinstance(value, dict):
                for name in trackers:
                    if isinstance(value.get(name), bool):
                        trackers[name] = value[name]
            else:
                fix(key, value, trackers)
            cfg[key] = trackers
        elif key == "extra_cameras":
            ok = isinstance(value, list) and all(isinstance(i, int) and not isinstance(i, bool) for i in value)
            if not ok:
                cfg[key] = fix(key, value, [])
                continue
            main_index = cfg.get("camera_index", DEFAULTS["camera_index"])
            cleaned = []
            for i in value:
                if 0 <= i <= 15 and i != main_index and i not in cleaned:
                    cleaned.append(i)
            if len(cleaned) > 2 or cleaned != value:
                fix(key, value, cleaned[:2])
            cfg[key] = cleaned[:2]
        elif key.startswith("hotkey_"):
            cfg[key] = value if parse_hotkey(value) else fix(key, value, default)
        elif key in CHOICES:
            cfg[key] = value if value in CHOICES[key] else fix(key, value, default)
        elif isinstance(default, bool):
            cfg[key] = value if isinstance(value, bool) else fix(key, value, default)
        elif isinstance(default, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                cfg[key] = fix(key, value, default)
                continue
            lo, hi = RANGES.get(key, (-math.inf, math.inf))
            clamped = min(max(value, lo), hi)
            if isinstance(default, int):
                clamped = int(round(clamped))
            if clamped != value:
                fix(key, value, clamped)
            cfg[key] = clamped
    return cfg, problems


def load_config(path):
    """A missing or unreadable config never stops the engine: a broken file is kept aside as
    config.json.broken-<time> for inspection and the defaults are used."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        return validate_config({})
    except (OSError, ValueError) as e:
        backup = f"{path}.broken-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            os.replace(path, backup)
        except OSError:
            backup = path
        cfg, problems = validate_config({})
        return cfg, [("config_corrupt", {"err": e, "backup": os.path.basename(backup)})] + problems
    return validate_config(raw)


def system_language():
    lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
    return "tr" if lang_id == 0x1F else "en"


# --------------------------------------------------------------------------- math

def norm(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_between(a, b):
    """Rotation matrix that turns unit vector a onto unit vector b."""
    a, b = norm(a), norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else rot_y(math.pi)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k * (1.0 / (1.0 + c))


def yaw_of(v):
    return math.atan2(v[0], v[2])


def frame_right_up(right, up):
    y = norm(up)
    x = norm(right - np.dot(right, y) * y)
    return np.column_stack([x, y, np.cross(x, y)])


def frame_up_forward(up, fwd):
    y = norm(up)
    f = norm(fwd - np.dot(fwd, y) * y)
    z = -f  # OpenVR: -Z is forward
    return np.column_stack([np.cross(y, z), y, z])


def quat_from_matrix(m):
    """Returns (w, x, y, z)."""
    t = m[0, 0] + m[1, 1] + m[2, 2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = (0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s)
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = ((m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s)
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = ((m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s)
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = ((m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s)
    q = np.array(q)
    return q / np.linalg.norm(q)


def matrix_from_quat(q):
    """(w, x, y, z) -> 3x3 rotation matrix (inverse of quat_from_matrix)."""
    w_, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w_ * z), 2 * (x * z + w_ * y)],
                     [2 * (x * y + w_ * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w_ * x)],
                     [2 * (x * z - w_ * y), 2 * (y * z + w_ * x), 1 - 2 * (x * x + y * y)]])


class OneEuro:
    """One Euro filter over an array of values (low lag when moving fast, smooth when still)."""

    def __init__(self, min_cutoff, beta, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _alpha(dt, cutoff):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t, weight=1.0):
        """weight (broadcastable to x) scales the cutoff; < 1 smooths those values harder."""
        if self.x is None or t - self.t > 0.5:
            self.x, self.dx, self.t = x.copy(), np.zeros_like(x), t
            return x
        dt = max(t - self.t, 1e-3)
        dx = (x - self.x) / dt
        a_d = self._alpha(dt, self.d_cutoff)
        self.dx = a_d * dx + (1 - a_d) * self.dx
        cutoff = (self.min_cutoff + self.beta * np.abs(self.dx)) * weight
        a = self._alpha(dt, cutoff)
        self.x = a * x + (1 - a) * self.x
        self.t = t
        return self.x


def filter_params(cfg):
    """Per-axis One Euro settings. Camera depth (z) is by far the noisiest axis with a single
    webcam, so it gets lower cutoffs; depth_smoothing 0..1 sets how much stronger."""
    depth = 1.0 - 0.85 * float(np.clip(cfg["depth_smoothing"], 0.0, 1.0))
    axis = np.array([1.0, 1.0, depth])
    return float(cfg["filter_min_cutoff"]) * axis, float(cfg["filter_beta"]) * axis


def visibility_weight(vis):
    """Joints the model can barely see are guesses that jump around: smooth them harder."""
    return np.clip(vis, 0.3, 1.0)[:, None]


class FootLock:
    """Pins a planted foot so webcam noise does not make it slide while standing still."""

    LOCK_SPEED = 0.25  # m/s horizontal: slower than this on the ground -> pin the foot
    RELEASE_DIST = 0.07  # m the estimate may wander from the pin before the foot is let go
    LIFT = 0.05  # m above resting ankle height that counts as a lifted foot
    SETTLE_RATE = 1.5  # 1/s: a pinned foot slowly slides to the true average position

    def __init__(self):
        self.state = {}

    def apply(self, W, joints, ground_y, now):
        ank = joints[0]
        p = W[ank].copy()
        s = self.state.setdefault(ank, {"pin": None, "prev": p, "t": now})
        dt = max(now - s["t"], 1e-3)
        speed = np.linalg.norm((p - s["prev"])[[0, 2]]) / dt
        s["prev"], s["t"] = p, now
        grounded = p[1] < ground_y + self.LIFT
        pin = s["pin"]
        if pin is not None and (not grounded or np.linalg.norm((p - pin)[[0, 2]]) > self.RELEASE_DIST):
            s["pin"] = pin = None
        elif pin is None and grounded and speed < self.LOCK_SPEED:
            s["pin"] = pin = p
        elif pin is not None:
            s["pin"] = pin = pin + (p - pin) * min(1.0, self.SETTLE_RATE * dt)
        if pin is not None:
            W[list(joints)] += pin - p


# --------------------------------------------------------------------------- io

class Camera:
    """Grabs frames on its own thread and reconnects by itself if the camera drops out.

    Two failure modes: read() keeps failing (unplugged, busy) - noticed by the reader thread - and
    read() never returns at all (seen after the PC wakes from sleep) - noticed by check_stall(),
    which the main loop calls; the stuck thread is abandoned and a fresh one reopens the camera."""

    LOST_AFTER = 2.0  # s without frames before the camera counts as disconnected
    STALL_AFTER = 4.0  # s without frames before a hanging read() is given up on

    def __init__(self, index, width, height, fps):
        self.index, self.width, self.height, self.fps = index, width, height, fps
        cap = self._open()
        if cap is None:
            raise RuntimeError(T("camera_open_failed", i=index))
        self.cap = cap
        self.frame, self.frame_id, self.stamp = None, 0, 0.0
        self.last_ok = time.monotonic()
        self.lost = False
        self.ever_ok = False  # no frame yet: "busy/blocked" rather than "disconnected"
        self.events = queue.Queue()  # message keys for the main thread to log
        self.lock = threading.Lock()
        self.running = True
        self.generation = 0
        self.thread = self._start_reader(cap)

    def _start_reader(self, cap):
        self.generation += 1
        t = threading.Thread(target=self._run, args=(self.generation, cap), daemon=True)
        t.start()
        return t

    def _open(self):
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            return None
        # Each DirectShow format change restarts the camera (~2.5 s), so only change what differs.
        if (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) != (self.width, self.height):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        reported_fps = cap.get(cv2.CAP_PROP_FPS)
        if reported_fps > 0 and abs(reported_fps - self.fps) > 1:  # -1 = unknown: setting it costs a restart
            cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self, generation, cap):
        while self.running and generation == self.generation:
            ok, f = cap.read() if cap is not None else (False, None)
            if generation != self.generation:
                return  # replaced by check_stall() while this read() hung; leave the old capture alone
            now = time.monotonic()
            if ok:
                if self.lost:
                    self.lost = False
                    self.events.put("camera_back")
                self.last_ok = now
                self.ever_ok = True
                with self.lock:
                    self.frame, self.frame_id, self.stamp = f, self.frame_id + 1, now
                continue
            if not self.lost and now - self.last_ok > self.LOST_AFTER:
                self.lost = True
                self.events.put("camera_lost" if self.ever_ok else "camera_no_frames")
            if self.lost:
                if cap is not None:
                    cap.release()
                time.sleep(1.0)
                if self.running and generation == self.generation:
                    cap = self.cap = self._open()
            else:
                time.sleep(0.01)
        if cap is not None and generation == self.generation:
            cap.release()

    def check_stall(self, now):
        """Main thread: if no frame came for STALL_AFTER s and the reader did not notice, it is stuck."""
        if self.running and not self.lost and now - self.last_ok > self.STALL_AFTER:
            self.lost = True
            self.events.put("camera_lost" if self.ever_ok else "camera_no_frames")
            self.cap = None
            self.thread = self._start_reader(None)

    def latest(self):
        with self.lock:
            return self.frame, self.frame_id, self.stamp

    @property
    def state(self):
        if not self.lost:
            return "ok"
        return "lost" if self.ever_ok else "no_frames"

    def close(self):
        self.running = False
        self.thread.join(timeout=2)
        if self.cap is not None and not self.thread.is_alive():
            self.cap.release()


class HmdPose:
    def __init__(self, rot, pos, height, vel=None, ang_vel=None, t=0.0, std_rot=None, std_pos=None, hands=None):
        self.rot, self.pos, self.height = rot, pos, height
        self.hands = [] if hands is None else hands  # left/right controller (or hand) positions, raw space
        self.vel = np.zeros(3) if vel is None else vel
        self.ang_vel = np.zeros(3) if ang_vel is None else ang_vel
        self.t = t
        # The same pose in the standing (play area) universe, used by the setup assistant's room map
        self.std_rot = rot if std_rot is None else std_rot
        self.std_pos = pos if std_pos is None else std_pos

    def to_standing(self, p):
        """A raw-space point in standing space (the play area's frame)."""
        return self.std_rot @ self.rot.T @ (np.asarray(p, float) - self.pos) + self.std_pos

    def dir_to_standing(self, v):
        return self.std_rot @ self.rot.T @ np.asarray(v, float)


class VRLink:
    """Background OpenVR client used only to read the headset pose."""

    SPACE_EVENTS = (openvr.VREvent_ChaperoneUniverseHasChanged, openvr.VREvent_StandingZeroPoseReset,
                    openvr.VREvent_SeatedZeroPoseReset)

    def __init__(self, enabled):
        self.enabled = enabled
        self.sys = None
        self.next_try = 0.0
        self.raw_arr = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
        self.std_arr = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
        self.headset = None  # {"name", "family", "connection"} once connected
        self.identified_at = 0.0
        self.headset_changed = False  # set when the headset's name changed after connecting
        self.just_connected = False  # set on (re)connect, read and cleared by the main loop
        # SteamVR announced a recenter / chaperone change. Only reported: Steam Link sends these on
        # every wake from standby too, and moving the calibration on such events wrecked tracking.
        self.space_event = False

    def play_area(self):
        """SteamVR play area as (centre x, centre z, width, depth) in standing space, or None."""
        if self.sys is None:
            return None
        try:
            ok, w, d = openvr.VRChaperone().getPlayAreaSize()
            if not ok or w < 0.5 or d < 0.5:
                return None
            ok, rect = openvr.VRChaperone().getPlayAreaRect()
            if ok:
                xs = [rect.vCorners[i].v[0] for i in range(4)]
                zs = [rect.vCorners[i].v[2] for i in range(4)]
                return (float(np.mean(xs)), float(np.mean(zs)), float(w), float(d))
            return (0.0, 0.0, float(w), float(d))
        except Exception:
            return None

    def driver_trackers(self):
        """(count, driver protocol) of the WebcamFBT trackers SteamVR has, or None if unknown.
        Drivers from before the version check report no version, which counts as protocol 1."""
        if self.sys is None:
            return None
        try:
            count, protocol = 0, None
            for i in range(1, openvr.k_unMaxTrackedDeviceCount):
                if self.sys.getTrackedDeviceClass(i) != openvr.TrackedDeviceClass_GenericTracker:
                    continue
                if self.sys.getStringTrackedDeviceProperty(i, openvr.Prop_TrackingSystemName_String) != "webcamfbt":
                    continue
                count += 1
                if protocol is None:
                    try:
                        protocol = int(self.sys.getStringTrackedDeviceProperty(i, openvr.Prop_DriverVersion_String))
                    except Exception:
                        protocol = 1
            return count, protocol
        except Exception:
            return None

    @property
    def connected(self):
        return self.sys is not None

    def disconnect(self):
        try:
            openvr.shutdown()
        except Exception:
            pass
        self.sys = None
        self.headset = None

    def _identify_headset(self):
        """Model and PC connection of the HMD (any SteamVR headset works; this only names it)."""
        def prop(p):
            try:
                return self.sys.getStringTrackedDeviceProperty(openvr.k_unTrackedDeviceIndex_Hmd, p)
            except Exception:
                return ""
        return headset.identify(prop(openvr.Prop_ModelNumber_String), prop(openvr.Prop_ManufacturerName_String),
                                prop(openvr.Prop_TrackingSystemName_String), prop(openvr.Prop_ResourceRoot_String),
                                prop(openvr.Prop_SerialNumber_String))

    def poll(self):
        if not self.enabled:
            return None
        if self.sys is None:
            if time.time() < self.next_try:
                return None
            self.next_try = time.time() + 3
            try:
                self.sys = openvr.init(openvr.VRApplication_Background)
                self.headset = self._identify_headset()
                self.identified_at = time.monotonic()
                self.just_connected = True
                log(T("vr_connected", name=self.headset["name"], conn=T("conn_" + self.headset["connection"])))
            except Exception:
                return None
        try:
            ev = openvr.VREvent_t()
            hmd_props_changed = False
            while self.sys.pollNextEvent(ev):
                if ev.eventType == openvr.VREvent_Quit:
                    self.sys.acknowledgeQuit_Exiting()
                    self.disconnect()
                    log(T("vr_closed"))
                    return None
                if ev.eventType in self.SPACE_EVENTS:
                    self.space_event = True
                if ev.eventType == openvr.VREvent_PropertyChanged and ev.trackedDeviceIndex == openvr.k_unTrackedDeviceIndex_Hmd:
                    hmd_props_changed = True
            # Steam Link fills in the headset's real properties a few seconds after SteamVR starts,
            # so the name read at connect time can be a placeholder: read it again now and then.
            if hmd_props_changed or time.monotonic() - self.identified_at > 10:
                self.identified_at = time.monotonic()
                found = self._identify_headset()
                if found != self.headset:
                    self.headset = found
                    self.headset_changed = True
                    log(T("vr_headset", name=found["name"], conn=T("conn_" + found["connection"])))
            raw = self.sys.getDeviceToAbsoluteTrackingPose(openvr.TrackingUniverseRawAndUncalibrated, 0.0, self.raw_arr)
            std = self.sys.getDeviceToAbsoluteTrackingPose(openvr.TrackingUniverseStanding, 0.0, self.std_arr)
        except Exception as e:
            log(T("vr_error", err=e))
            self.disconnect()
            return None
        if not raw[0].bPoseIsValid:
            return None
        m = raw[0].mDeviceToAbsoluteTracking
        m = np.array([[m[i][j] for j in range(4)] for i in range(3)])
        sm = std[0].mDeviceToAbsoluteTracking
        sm = np.array([[sm[i][j] for j in range(4)] for i in range(3)])
        v, w = raw[0].vVelocity, raw[0].vAngularVelocity
        return HmdPose(m[:, :3], m[:, 3], sm[1][3], np.array([v[0], v[1], v[2]]), np.array([w[0], w[1], w[2]]),
                       time.monotonic(), sm[:, :3], sm[:, 3], self._hands())

    HAND_ROLES = (openvr.TrackedControllerRole_LeftHand, openvr.TrackedControllerRole_RightHand)

    def _hands(self):
        """Raw-space positions of the left and right hand controllers that are tracked right now."""
        out = []
        try:
            for role in self.HAND_ROLES:
                i = self.sys.getTrackedDeviceIndexForControllerRole(role)
                if 0 < i < openvr.k_unMaxTrackedDeviceCount and self.raw_arr[i].bPoseIsValid:
                    m = self.raw_arr[i].mDeviceToAbsoluteTracking
                    out.append(np.array([m[0][3], m[1][3], m[2][3]]))
        except Exception:
            pass
        return out


class Sender:
    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addr = ("127.0.0.1", port)

    def send(self, poses):
        data = struct.pack("<II", MAGIC, len(poses))
        for tid, pos, q in poses:
            data += struct.pack("<II3f4f", tid, 1, *pos, *q)
        data += struct.pack("<I", PROTOCOL)
        self.sock.sendto(data, self.addr)


def beep(freq, ms):
    threading.Thread(target=winsound.Beep, args=(freq, ms), daemon=True).start()


def beep_twice(freq):
    """Two short beeps: an automatic calibration starts (hold still)."""
    def run():
        for _ in range(2):
            winsound.Beep(freq, 90)
            time.sleep(0.06)
    threading.Thread(target=run, daemon=True).start()


def beep_warning():
    """Two low beeps: something needs attention (heard inside the headset too)."""
    def run():
        for _ in range(2):
            winsound.Beep(400, 140)
            time.sleep(0.08)
    threading.Thread(target=run, daemon=True).start()


def udp_port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class DriverWatch:
    """Notices when poses are sent but SteamVR shows no WebcamFBT trackers, and says why:
    the driver is not loaded (port free) or another program holds the driver's UDP port."""

    GRACE = 4.0  # s of sending before judging (trackers appear on the first packets)
    INTERVAL = 5.0

    def __init__(self, port):
        self.port = port
        self.sending_since = None
        self.next_check = 0.0
        self.state = "unknown"
        self.driver_protocol = None

    def update(self, vr, sending, now):
        """Returns a message key when the state changes in a way worth telling, else None."""
        if not sending or not vr.connected:
            self.sending_since = None
            return None
        if self.sending_since is None:
            self.sending_since = now
        if now - self.sending_since < self.GRACE or now < self.next_check:
            return None
        self.next_check = now + self.INTERVAL
        found = vr.driver_trackers()
        if found is None:
            return None
        count, self.driver_protocol = found
        if count == 0:
            new = "not_loaded" if udp_port_free(self.port) else "port_busy"
        else:
            new = "ok" if self.driver_protocol == PROTOCOL else "outdated"
        old, self.state = self.state, new
        if new == old or (new == "ok" and old == "unknown"):
            return None
        return {"ok": "driver_ok", "not_loaded": "driver_not_loaded", "port_busy": "port_busy",
                "outdated": "driver_outdated"}[new]


def raise_priority():
    """Games eat the CPU too; above-normal priority keeps pose estimation from stuttering."""
    ABOVE_NORMAL_PRIORITY_CLASS = 0x8000
    kernel32 = ctypes.windll.kernel32
    kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), ABOVE_NORMAL_PRIORITY_CLASS)


HOTKEY_RE = re.compile(r"^((ctrl|alt|shift)\+)*f([1-9]|1[0-2])$", re.IGNORECASE)
_MOD_FLAGS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4}
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


def parse_hotkey(text):
    """'Ctrl+F8' -> (modifier flags, virtual key), or None if not a supported combination."""
    if not isinstance(text, str) or not HOTKEY_RE.match(text.strip()):
        return None
    *mods, key = [p.strip().lower() for p in text.split("+")]
    flags = 0
    for m in mods:
        flags |= _MOD_FLAGS[m]
    return flags, 0x6F + int(key[1:])  # VK_F1 = 0x70


class Hotkeys:
    """System-wide hotkeys through RegisterHotKey: Windows reports only these combinations,
    so the keyboard is never polled (which antivirus tools can mistake for a keylogger).
    A registered key is not passed on to the game while WebcamFBT runs."""

    def __init__(self, bindings):
        self.bindings = dict(bindings)  # action -> "F8"
        self.events = queue.Queue()
        self.failed = []  # (action, key) that could not be registered
        self.thread_id = None
        ready = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        self.thread.start()
        ready.wait(2)

    def _run(self, ready):
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self.thread_id = kernel32.GetCurrentThreadId()
        actions = {}
        for hotkey_id, (action, text) in enumerate(self.bindings.items(), start=1):
            parsed = parse_hotkey(text)
            if parsed and user32.RegisterHotKey(None, hotkey_id, parsed[0] | MOD_NOREPEAT, parsed[1]):
                actions[hotkey_id] = action
            else:
                self.failed.append((action, text))
        ready.set()
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in actions:
                self.events.put(actions[msg.wParam])
        for hotkey_id in actions:
            user32.UnregisterHotKey(None, hotkey_id)

    def pressed(self):
        """Actions triggered since the last call."""
        out = []
        while not self.events.empty():
            out.append(self.events.get_nowait())
        return out

    def close(self):
        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            self.thread.join(timeout=1)


# --------------------------------------------------------------------------- calibration

class Calibration:
    """Relation of one camera to SteamVR space, solved against the headset (see _solve)."""

    COUNTDOWN = 3.0
    COLLECT = 2.0
    MAX_MOVE = 0.10  # m the headset may wander while samples are taken
    MAX_TURN = 15.0  # degrees the headset may turn meanwhile

    def __init__(self, index=0, sound=True):
        self.index = index
        self.sound = sound  # CalibrationSet beeps once for all cameras instead
        self.ready = False
        self.from_file = False
        self.R = np.eye(3)  # camera space -> raw VR space
        self.scale = 1.0
        self.floor = 0.0  # floor height in raw VR space when calibrated (SteamVR's live floor is used if known)
        self.shoulders = default_shoulder_offset(1.6)  # HMD -> shoulders' midpoint, HMD local space
        self.head_anchor = True  # hang the body from the ears when they are seen (only if calibrated with them)
        self.phase = None
        self.t_phase = 0.0
        self.countdown = self.COUNTDOWN
        self.samples = []
        self.failure = None  # why the last calibration failed: "calib_failed" or "calib_moved"

    def reset(self):
        self.ready = self.from_file = False
        self.phase = None

    def start(self, now, countdown=None):
        """countdown=0 starts taking samples at once (automatic calibration: the player already stands still)."""
        self.countdown = self.COUNTDOWN if countdown is None else countdown
        self.phase, self.t_phase, self.samples = "countdown", now, []
        if self.sound:
            beep(660, 150)

    def remaining(self, now):
        total = self.countdown if self.phase == "countdown" else self.COLLECT
        return max(0.0, total - (now - self.t_phase)) if self.phase else 0.0

    def update(self, now, P, vis, hmd):
        """Returns True/False when a calibration finishes (success/failure), otherwise None."""
        if self.phase == "countdown":
            if now - self.t_phase < self.countdown:
                return None
            self.phase, self.t_phase = "collect", now
            if self.sound:
                beep(880, 150)
        if self.phase != "collect":
            return None
        if P is not None and hmd is not None and vis[BODY].min() > 0.5:
            self.samples.append((P.copy(), vis.copy(), hmd))
        if now - self.t_phase < self.COLLECT:
            return None
        self.phase = None
        self.failure = "calib_failed" if len(self.samples) < 10 else ("calib_moved" if self._moved() else None)
        if self.failure:
            log(T(self.failure, i=self.index))
            if self.sound:
                beep(300, 600)
            return False
        self._solve()
        if self.sound:
            beep(1200, 300)
        return True

    def _moved(self):
        """True if the player walked or turned while the samples were taken (the result would be off)."""
        pos = np.array([h.pos for _, _, h in self.samples])
        yaws = np.array([yaw_of(-h.rot[:, 2]) for _, _, h in self.samples])
        turn = np.degrees(np.abs(np.angle(np.exp(1j * (yaws - yaws[0])))))
        return bool(np.linalg.norm(pos - pos.mean(axis=0), axis=1).max() > self.MAX_MOVE or turn.max() > self.MAX_TURN)

    def _solve(self):
        # Medians throughout: a few bad frames (a blink of a hand over the face, a misread foot) must
        # not tilt or turn the whole calibration.
        # 1) Camera tilt: the person stands upright, so ankles->shoulders (plumb line) must map to +Y.
        ups = [norm((P[L_SH] + P[R_SH]) / 2 - (P[L_ANK] + P[R_ANK]) / 2) for P, _, _ in self.samples]
        r_tilt = rot_between(np.median(ups, axis=0), np.array([0.0, 1.0, 0.0]))
        # The head is optional: with it in the picture the eyes give the scale and the ears the anchor.
        head = np.mean([vis[HEAD].min() > 0.5 for _, vis, _ in self.samples]) >= 0.8

        # 2) Camera yaw: body facing direction must match where the headset looks.
        diffs, eye_scales, shoulder_scales, hmd_heights, floors = [], [], [], [], []
        for P, _, hmd in self.samples:
            Q = P @ r_tilt.T
            right = (Q[R_HIP] - Q[L_HIP]) + (Q[R_SH] - Q[L_SH])
            right[1] = 0
            body_fwd = np.cross([0.0, 1.0, 0.0], right)
            hmd_fwd = -hmd.rot[:, 2]
            diffs.append(yaw_of(hmd_fwd) - yaw_of(body_fwd))
            ground = min(Q[i][1] for i in (L_HEEL, R_HEEL, L_TOE, R_TOE))
            eye_scales.append(hmd.height / max((Q[L_EYE][1] + Q[R_EYE][1]) / 2 - ground, 0.3))
            shoulder_scales.append(SHOULDER_EYE_RATIO * hmd.height / max((Q[L_SH][1] + Q[R_SH][1]) / 2 - ground, 0.25))
            hmd_heights.append(hmd.height)
            floors.append(hmd.pos[1] - hmd.height)
        diffs = np.array(diffs)
        center = math.atan2(np.sin(diffs).sum(), np.cos(diffs).sum())
        yaw = center + float(np.median(np.angle(np.exp(1j * (diffs - center)))))
        eye_height = float(np.median(hmd_heights))

        self.R = rot_y(yaw) @ r_tilt
        scale = np.median(eye_scales) if head else np.median(shoulder_scales)
        self.scale = float(np.clip(scale, 0.6, 1.6))
        self.floor = float(np.median(floors))
        if _file_log:
            _file_log.info(f"calibration detail cam {self.index}: head={head} scale eye {np.median(eye_scales):.3f} "
                           f"(spread {np.std(eye_scales):.3f}) shoulders {np.median(shoulder_scales):.3f} "
                           f"(spread {np.std(shoulder_scales):.3f}) yaw spread {math.degrees(np.std(diffs - center)):.1f} deg")
        self.head_anchor = bool(head)
        if head:  # measure where this player's shoulders hang from the headset
            M = (self.scale * self.R).T
            offs = []
            for P, _, hmd in self.samples:
                ears = hmd.pos + hmd.rot @ HMD_TO_EARS
                sh = ears + ((P[L_SH] + P[R_SH]) / 2 - (P[L_EAR] + P[R_EAR]) / 2) @ M
                A = 0.5 * (hmd.rot + heading_rotation(hmd.rot))  # inverse of shoulder_anchor
                offs.append(HMD_TO_EARS + np.linalg.solve(A, sh - ears))
            self.shoulders = np.mean(offs, axis=0)
        else:
            self.shoulders = default_shoulder_offset(eye_height)
        self.ready = True
        self.from_file = False
        log(T("calib_ok", i=self.index, yaw=math.degrees(yaw), scale=self.scale, floor=self.floor, n=len(self.samples))
            + ("" if head else " " + T("calib_no_head")))

    # The camera -> SteamVR space relation stays valid until the camera moves or the
    # headset's tracking space is reset, so it is kept between sessions, one entry per camera:
    # {"cameras": {"0": {"R": ..., "scale": ..., "floor": ...}, "1": {...}}}
    @staticmethod
    def _read_file():
        try:
            with open(CALIBRATION_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        if isinstance(data.get("cameras"), dict):
            return data["cameras"]
        if "camera_index" in data:  # single-camera file from before multi-camera support
            return {str(data["camera_index"]): data}
        return {}

    def save(self, camera_index=None, headset_name=None):
        index = self.index if camera_index is None else camera_index
        cameras = self._read_file()
        cameras[str(index)] = {"R": self.R.tolist(), "scale": self.scale, "floor": self.floor, "headset": headset_name,
                               "shoulders": self.shoulders.tolist(), "head_anchor": self.head_anchor,
                               "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            with open(CALIBRATION_FILE, "w", encoding="utf-8") as f:
                json.dump({"cameras": cameras}, f, indent=2)
        except OSError as e:
            log(T("error", err=e))

    def load(self, camera_index=None, headset_name=None):
        """A calibration only fits the headset (and its tracking space) it was made with."""
        index = self.index if camera_index is None else camera_index
        entry = self._read_file().get(str(index))
        if entry is None or entry.get("headset") != headset_name:
            return False
        try:
            self.R = np.array(entry["R"], dtype=float).reshape(3, 3)
            self.scale = float(entry["scale"])
            self.floor = float(entry["floor"])
            self.shoulders = np.array(entry.get("shoulders", default_shoulder_offset(1.6)), dtype=float).reshape(3)
            self.head_anchor = bool(entry.get("head_anchor", True))
        except (KeyError, TypeError, ValueError):
            return False
        self.ready = self.from_file = True
        return True


class CalibrationSet:
    """One F8 calibration for every camera at once. Each camera is solved on its own against the
    headset, so the cameras never need to be calibrated against each other."""

    def __init__(self, indices):
        self.cals = {i: Calibration(i, sound=False) for i in indices}
        self.primary = self.cals[indices[0]]

    @property
    def phase(self):
        return self.primary.phase

    @property
    def ready(self):
        return any(c.ready for c in self.cals.values())

    @property
    def from_file(self):
        ready = [c for c in self.cals.values() if c.ready]
        return bool(ready) and all(c.from_file for c in ready)

    @property
    def scale(self):
        ready = [c.scale for c in self.cals.values() if c.ready]
        return float(np.mean(ready)) if ready else 1.0

    @property
    def floor(self):
        ready = [c.floor for c in self.cals.values() if c.ready]
        return float(np.mean(ready)) if ready else 0.0

    def ready_count(self):
        return sum(c.ready for c in self.cals.values())

    def remaining(self, now):
        return self.primary.remaining(now)

    @property
    def failure(self):
        return self.primary.failure

    def reset(self):
        for c in self.cals.values():
            c.reset()

    def start(self, now, auto=False):
        """auto: the player already stands still (StillDetector), so samples are taken at once."""
        for c in self.cals.values():
            c.start(now, countdown=0.0 if auto else None)
        if auto:
            beep_twice(880)
        else:
            beep(660, 150)

    def update(self, now, inputs, hmd):
        """inputs: {camera index: (P, vis)} for cameras with a new frame. Returns {index: ok}
        when the calibration finished, else None."""
        if self.phase is None:
            return None
        was = self.phase
        results = {}
        for i, c in self.cals.items():
            P, vis = inputs.get(i, (None, None))
            r = c.update(now, P, vis, hmd)
            if r is not None:
                results[i] = r
        if was == "countdown" and self.phase == "collect" and self.primary.countdown > 0:
            beep(880, 150)
        if not results:
            return None
        beep(1200, 300) if any(results.values()) else beep(300, 600)
        return results

    def adjust(self, rotation=None, scale=None):
        for c in self.cals.values():
            if c.ready:
                if rotation is not None:
                    c.R = rotation @ c.R
                if scale is not None:
                    c.scale *= scale

    def save(self, headset_name=None):
        for c in self.cals.values():
            if c.ready:
                c.save(headset_name=headset_name)

    def load(self, headset_name=None):
        return [i for i, c in self.cals.items() if c.load(headset_name=headset_name)]


def joint_angle(a, b, c):
    """Angle at b (degrees) between b->a and b->c."""
    u, v = a - b, c - b
    nu, nv = np.linalg.norm(u), np.linalg.norm(v)
    if nu < 1e-6 or nv < 1e-6:
        return 0.0
    return math.degrees(math.acos(float(np.clip(np.dot(u, v) / (nu * nv), -1.0, 1.0))))


def standing_pose_problem(P, vis):
    """None if the camera sees someone standing upright, facing it, shoulders to feet; else why not.
    Camera space only (the camera's tilt is not known yet), so it uses angles between body parts."""
    if P is None or vis[BODY + [L_KNEE, R_KNEE]].min() < 0.6:
        return "not_seen"
    for hip, knee, ank in ((L_HIP, L_KNEE, L_ANK), (R_HIP, R_KNEE, R_ANK)):
        if joint_angle(P[hip], P[knee], P[ank]) < 160:
            return "knees_bent"
    sh, hip, ank = [(P[a] + P[b]) / 2 for a, b in ((L_SH, R_SH), (L_HIP, R_HIP), (L_ANK, R_ANK))]
    if 180 - joint_angle(sh, hip, ank) > 15:
        return "bent"
    up = norm(sh - ank)
    right = (P[R_HIP] - P[L_HIP]) + (P[R_SH] - P[L_SH])
    fwd = np.cross(up, right)
    if np.linalg.norm(fwd) < 1e-6 or norm(fwd)[2] < 0.5:  # +z points at the camera
        return "not_facing"
    if min(vis[NOSE], vis[L_EAR], vis[R_EAR]) > 0.6:  # head seen: it must look the way the body faces
        look = P[NOSE] - (P[L_EAR] + P[R_EAR]) / 2
        look = look - np.dot(look, up) * up
        if np.linalg.norm(look) > 1e-6 and joint_angle(look, np.zeros(3), fwd) > 25:
            return "head_turned"
    return None


class StillDetector:
    """Notices the player standing upright and still in front of the camera with the headset level:
    the moment to calibrate without pressing a key. Anything off restarts the wait."""

    HOLD = 1.5  # s
    MOVE = 0.05  # m (headset) / m in MediaPipe units (joints)
    TURN = 8.0  # degrees
    MAX_PITCH = 25.0

    def __init__(self):
        self.since = None
        self.ref = None

    def reset(self):
        self.since = self.ref = None

    def update(self, now, P, vis, hmd):
        """True once the player has held still long enough."""
        if hmd is None or standing_pose_problem(P, vis) is not None:
            self.reset()
            return False
        fwd = -hmd.rot[:, 2]
        if abs(math.degrees(math.asin(float(np.clip(fwd[1], -1, 1))))) > self.MAX_PITCH:
            self.reset()
            return False
        joints = P[[L_SH, R_SH, L_KNEE, R_KNEE, L_ANK, R_ANK]]
        key = (hmd.pos.copy(), yaw_of(fwd), joints.copy())
        if self.ref is None or self._moved(self.ref, key):
            self.ref, self.since = key, now
        return now - self.since >= self.HOLD

    def _moved(self, a, b):
        turn = abs(math.degrees(math.atan2(math.sin(a[1] - b[1]), math.cos(a[1] - b[1]))))
        return (np.linalg.norm(a[0] - b[0]) > self.MOVE or turn > self.TURN
                or np.linalg.norm(a[2] - b[2], axis=1).max() > self.MOVE)


class HandsUp:
    """Both hands (controllers or tracked hands) held above the head for HOLD seconds: recalibrate
    without the keyboard. Fires once; the hands must come down before it can fire again."""

    HOLD = 2.0
    ABOVE = 0.15  # m above the headset

    def __init__(self):
        self.since = None
        self.armed = True

    def update(self, now, hmd):
        up = hmd is not None and len(hmd.hands) >= 2 and all(h[1] > hmd.pos[1] + self.ABOVE for h in hmd.hands)
        if not up:
            self.since, self.armed = None, True
            return False
        if self.since is None:
            self.since = now
        if self.armed and now - self.since >= self.HOLD:
            self.armed = False
            return True
        return False


# --------------------------------------------------------------------------- poses

def heading_rotation(rot):
    """The headset's rotation with pitch and roll removed (yaw only)."""
    f = -rot[:, 2].copy()
    if math.hypot(f[0], f[2]) < 1e-3:  # looking straight down/up: the head's top points the way it faces
        f = rot[:, 1] * (1.0 if f[1] < 0 else -1.0)
    f[1] = 0.0
    back = -norm(f)
    up = np.array([0.0, 1.0, 0.0])
    return np.column_stack([np.cross(up, back), up, back])


def shoulder_anchor(hmd, offset):
    """Where the shoulders' midpoint hangs from the headset (raw space).

    The ears are rigid on the head; below them the neck bends partly at the skull and partly near the
    shoulders, so the ears->shoulders offset follows half of the head's pitch and roll (and all of its
    yaw, which turns about the spine). That halves the worst error of either extreme when looking down."""
    ears = hmd.pos + hmd.rot @ HMD_TO_EARS
    v = np.asarray(offset, float) - HMD_TO_EARS  # ears -> shoulders, head level
    return ears + 0.5 * (hmd.rot @ v) + 0.5 * (heading_rotation(hmd.rot) @ v)


def head_weight(vis):
    """0..1: how much to trust the ears over the shoulders as the anchor (ears barely seen -> 0)."""
    if vis is None:
        return 1.0
    return float(np.clip((min(vis[L_EAR], vis[R_EAR]) - 0.5) / 0.3, 0.0, 1.0))


def camera_points(P, hmd, cal, vis=None):
    """One camera's landmarks (camera space, y up) in raw VR space, hung from the headset.

    The ears are fixed to the head, so they are the best anchor when the camera sees them. With the
    head out of the picture the shoulders' midpoint is hung from the headset instead (its offset was
    measured at calibration, or estimated from the eye height when the head was not seen then either)."""
    M = (cal.scale * cal.R).T
    shoulders = shoulder_anchor(hmd, cal.shoulders) - ((P[L_SH] + P[R_SH]) / 2) @ M
    wh = head_weight(vis) if cal.head_anchor else 0.0
    if wh > 0.0:
        ears = hmd.pos + hmd.rot @ HMD_TO_EARS - ((P[L_EAR] + P[R_EAR]) / 2) @ M
        shift = wh * ears + (1.0 - wh) * shoulders
    else:
        shift = shoulders
    return P @ M + shift


EDGE = 0.015  # image border (share of the picture) inside which a landmark counts as cut off


def edge_visibility(vis, img):
    """MediaPipe still places joints that are cut off by the picture's border, often with a fair
    visibility score; those are guesses, so they count as hidden."""
    out = vis.copy()
    outside = (img[:, 0] < EDGE) | (img[:, 0] > 1 - EDGE) | (img[:, 1] < EDGE) | (img[:, 1] > 1 - EDGE)
    out[outside] = np.minimum(out[outside], 0.2)
    return out


_LR = [(1, 4), (2, 5), (3, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16), (17, 18), (19, 20), (21, 22),
       (23, 24), (25, 26), (27, 28), (29, 30), (31, 32)]
SWAP_LR = np.arange(33)
for _a, _b in _LR:
    SWAP_LR[_a], SWAP_LR[_b] = _b, _a


def facing_error(P, up, fwd):
    """Degrees between the body's facing in P and fwd, both seen from above (up = vertical)."""
    right = (P[R_HIP] - P[L_HIP]) + (P[R_SH] - P[L_SH])
    body = np.cross(up, right)
    body = body - np.dot(body, up) * up
    f = fwd - np.dot(fwd, up) * up
    nb, nf = np.linalg.norm(body), np.linalg.norm(f)
    if nb < 1e-6 or nf < 1e-6:
        return 0.0
    return math.degrees(math.acos(float(np.clip(np.dot(body, f) / (nb * nf), -1.0, 1.0))))


BACK_VIEW = 110.0  # degrees: the body cannot face this far from where the head looks


def fix_back_view(P, ref):
    """MediaPipe tends to take a person seen from behind for one facing the camera: left and right
    labels swap and the depth is mirrored, so the legs turn 180 degrees. The headset says which way
    the player really faces, so such a frame is turned back (labels swapped, depth mirrored).
    Returns (P, flipped)."""
    if ref is None:
        return P, False
    up, fwd = ref
    if facing_error(P, up, fwd) < BACK_VIEW:
        return P, False
    mirrored = P[SWAP_LR] * np.array([1.0, 1.0, -1.0])
    swapped = P[SWAP_LR]
    best = min((mirrored, swapped), key=lambda Q: facing_error(Q, up, fwd))
    if facing_error(best, up, fwd) < 180.0 - BACK_VIEW:
        return best, True
    return P, False


class HmdHistory:
    """The last second of headset poses, to look up where the head was when a frame was taken."""

    def __init__(self, seconds=1.0):
        self.seconds = seconds
        self.poses = collections.deque()

    def add(self, hmd):
        if hmd is None:
            return
        self.poses.append(hmd)
        while self.poses and hmd.t - self.poses[0].t > self.seconds:
            self.poses.popleft()

    def at(self, t):
        """Pose at time t (position interpolated, rotation of the nearer pose); None if unknown."""
        if not self.poses:
            return None
        if t >= self.poses[-1].t:
            return self.poses[-1]
        if t <= self.poses[0].t:
            return self.poses[0]
        hi = len(self.poses) - 1
        lo = 0
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.poses[mid].t <= t:
                lo = mid
            else:
                hi = mid
        a, b = self.poses[lo], self.poses[hi]
        k = (t - a.t) / max(b.t - a.t, 1e-6)
        near = a if k < 0.5 else b
        return HmdPose(near.rot, a.pos + (b.pos - a.pos) * k, a.height + (b.height - a.height) * k, t=t)


# How much each joint is tracked in the room (1) rather than hung from the current head pose (0).
# A planted foot stands still in the room while the body walks on. Hung from the head, with a body
# shape that is ~150 ms old and smoothed relative to the hips, it slid along with every step; so the
# feet are placed with the headset pose of the moment the frame was taken and smoothed in room
# coordinates, where a planted foot does not move at all. Hips and arms follow the head closely and
# stay hung from its current pose (no added lag); knees sit in between.
WORLD_WEIGHT = np.zeros(33)
WORLD_WEIGHT[[L_ANK, R_ANK, L_HEEL, R_HEEL, L_TOE, R_TOE]] = 1.0
WORLD_WEIGHT[[L_KNEE, R_KNEE]] = 0.5


def tracked_points(P, raw, vis, stamp, cal, hmd_now, hmd_then, room_filter):
    """One camera's landmarks in raw VR space. P: smoothed camera-space landmarks (hung from the
    current head); raw: the unsmoothed ones, placed in the room with the head pose of when the frame
    was taken and smoothed there by room_filter (a OneEuro working along the camera's axes, so its
    depth axis is still the camera's)."""
    W = camera_points(P, hmd_now, cal, vis)
    if hmd_then is None or raw is None:
        return W
    room = camera_points(raw, hmd_then, cal, vis) @ cal.R  # room positions along the camera's axes
    room = room_filter(room, stamp, visibility_weight(vis)) @ cal.R.T
    return W + WORLD_WEIGHT[:, None] * (room - W)


DEPTH_NOISE = 3.0  # a single webcam's error along its viewing axis is about 3x its sideways error


def body_facing_angle(W, hmd):
    """Angle between the body's facing in W and where the headset looks (horizontal)."""
    up = (W[L_SH] + W[R_SH]) / 2 - (W[L_HIP] + W[R_HIP]) / 2
    right = (W[R_HIP] - W[L_HIP]) + (W[R_SH] - W[L_SH])
    body = np.cross(up, right)
    head = -hmd.rot[:, 2]
    body[1] = head[1] = 0.0
    nb, nh = np.linalg.norm(body), np.linalg.norm(head)
    if nb < 1e-6 or nh < 1e-6:
        return 0.0
    return math.acos(float(np.clip(np.dot(body, head) / (nb * nh), -1.0, 1.0)))


def fuse_views(views, hmd=None, max_facing=math.radians(110)):
    """views: [(W 33x3 in VR space, vis 33, camera viewing axis in VR space)] -> (W, vis).

    Each camera is trusted DEPTH_NOISE times less along its own viewing axis, and per joint by how
    well it sees that joint, so two cameras at an angle fix each other's depth. A camera that sees
    the person from behind (MediaPipe then tends to swap left and right) is left out while another
    camera has a front view."""
    if hmd is not None and len(views) > 1:
        angles = [body_facing_angle(W, hmd) for W, _, _ in views]
        kept = [v for v, a in zip(views, angles) if a < max_facing]
        views = kept or [views[int(np.argmin(angles))]]
    if len(views) == 1:
        return views[0][0], views[0][1]
    A = np.zeros((33, 3, 3))
    b = np.zeros((33, 3))
    for W, vis, axis in views:
        d = norm(np.asarray(axis, dtype=float))
        M = np.eye(3) - np.outer(d, d) + np.outer(d, d) / DEPTH_NOISE ** 2
        weight = np.clip(vis, 0.05, 1.0)
        A += weight[:, None, None] * M
        b += weight[:, None] * (W @ M)  # M is symmetric
    fused = np.linalg.solve(A, b[..., None])[..., 0]
    return fused, np.max([v for _, v, _ in views], axis=0)


class Carry:
    """Trackers whose joints the camera cannot see (feet out of the picture while walking) follow
    the body instead of freezing where they were last seen: a hidden foot keeps its last offset to
    its knee if the knee is seen (floor-clamped), else to the hips (on the floor). Whenever a
    tracker's source changes (seen, carried by the knee, carried by the hips) it glides from where
    it was to the new place over BLEND seconds instead of jumping: a knee at the edge of the picture
    flickering in and out of view made a carried foot hop 30-40 cm between the two."""

    BLEND = 0.3
    FOOT_KNEE = {"left_foot": L_KNEE, "right_foot": R_KNEE}

    def __init__(self):
        self.rel = {}  # name -> (position, rotation) relative to the hips' yaw frame
        self.rel_knee = {}  # foot -> offset from its knee in the hips' yaw frame
        self.out = {}  # name -> last position sent
        self.mode = {}  # name -> "seen", "knee" or "hips"
        self.glide = {}  # name -> (offset, start time)

    def _emit(self, name, mode, pos, now):
        if self.mode.get(name) != mode and name in self.out:
            self.glide[name] = (self.out[name] - pos, now)
        self.mode[name] = mode
        if name in self.glide:
            off, t0 = self.glide[name]
            k = 1.0 - (now - t0) / self.BLEND
            if k <= 0:
                del self.glide[name]
            else:
                pos = pos + off * k
        self.out[name] = pos
        return pos

    def seen(self, name, pos, rot, hip, yaw, now, knee=None):
        self.rel[name] = (yaw.T @ (pos - hip), yaw.T @ rot)
        if knee is not None:
            self.rel_knee[name] = yaw.T @ (pos - knee)
        return self._emit(name, "seen", pos, now), rot

    def carried(self, name, hip, yaw, ground_y, knee=None, now=0.0):
        """Pose of a tracker that is not seen now, or None if it was never seen."""
        if name not in self.rel:
            return None
        rel_pos, rel_rot = self.rel[name]
        mode = "hips"
        if name in self.FOOT_KNEE:
            if knee is not None and name in self.rel_knee:
                pos = knee + yaw @ self.rel_knee[name]
                pos[1] = max(pos[1], ground_y)
                mode = "knee"
            else:
                pos = hip + yaw @ rel_pos
                pos[1] = ground_y
        else:
            pos = hip + yaw @ rel_pos
        return self._emit(name, mode, pos, now), yaw @ rel_rot


def yaw_frame(right):
    """Rotation turning only about the vertical so that x points along right (seen from above)."""
    r = np.array([right[0], 0.0, right[2]])
    if np.linalg.norm(r) < 1e-6:
        return np.eye(3)
    r = norm(r)
    up = np.array([0.0, 1.0, 0.0])
    return np.column_stack([r, up, np.cross(r, up)])


def build_poses(W, vis, floor, cfg, enabled, last, feet=None, now=0.0, carry=None):
    """W: 33x3 landmarks in raw VR space. Returns [(id, pos, quat)] for the enabled trackers."""
    W = W.copy()
    min_ankle = floor + cfg["ankle_height"]
    for joints in ((L_ANK, L_HEEL, L_TOE), (R_ANK, R_HEEL, R_TOE)):
        if cfg["floor_lock"]:
            W[list(joints), 1] += max(0.0, min_ankle - W[joints[0]][1])
        if feet is not None and cfg["foot_lock"]:
            feet.apply(W, joints, min_ankle, now)

    hip_mid = (W[L_HIP] + W[R_HIP]) / 2
    sh_mid = (W[L_SH] + W[R_SH]) / 2
    up = sh_mid - hip_mid
    right = (W[R_HIP] - W[L_HIP]) + (W[R_SH] - W[L_SH])
    body_fwd = np.cross(up, right)
    lfoot_fwd = W[L_TOE] - W[L_HEEL]
    rfoot_fwd = W[R_TOE] - W[R_HEEL]

    builders = {
        "hip": lambda: (hip_mid, frame_right_up(W[R_HIP] - W[L_HIP], up)),
        "chest": lambda: (hip_mid + 0.8 * up, frame_right_up(W[R_SH] - W[L_SH], up)),
        "left_foot": lambda: (W[L_ANK], frame_up_forward(W[L_KNEE] - W[L_ANK], lfoot_fwd)),
        "right_foot": lambda: (W[R_ANK], frame_up_forward(W[R_KNEE] - W[R_ANK], rfoot_fwd)),
        "left_knee": lambda: (W[L_KNEE], frame_up_forward(W[L_HIP] - W[L_KNEE], lfoot_fwd)),
        "right_knee": lambda: (W[R_KNEE], frame_up_forward(W[R_HIP] - W[R_KNEE], rfoot_fwd)),
        "left_elbow": lambda: (W[L_ELB], frame_up_forward(W[L_SH] - W[L_ELB], body_fwd)),
        "right_elbow": lambda: (W[R_ELB], frame_up_forward(W[R_SH] - W[R_ELB], body_fwd)),
    }

    yaw = yaw_frame(right)
    out = []
    for tid, name in enumerate(TRACKERS):
        if not enabled[name]:
            continue
        knee_i = Carry.FOOT_KNEE.get(name)
        knee = W[knee_i] if knee_i is not None and vis[knee_i] >= cfg["min_visibility"] else None
        pose = None
        if vis[REQUIRED[name]].min() >= cfg["min_visibility"]:
            pose = builders[name]()
            heel_toe = FOOT_DIRECTION.get(name)
            if heel_toe and vis[heel_toe].min() < cfg["min_visibility"]:
                if carry is not None and name in carry.rel:
                    pose = (pose[0], yaw @ carry.rel[name][1])
                elif name in last:
                    pose = (pose[0], matrix_from_quat(last[name][2]))
            if carry is not None:
                pose = carry.seen(name, pose[0], pose[1], hip_mid, yaw, now, knee)
        elif carry is not None and name != "hip":
            pose = carry.carried(name, hip_mid, yaw, min_ankle, knee, now)
        if pose is not None:
            last[name] = (tid, pose[0].copy(), quat_from_matrix(pose[1]))
        if name in last:
            out.append(last[name])
    return out


def live_floor(hmd, fallback):
    """Floor height in raw VR space straight from SteamVR (raw and standing space differ only by a
    yaw and an offset), so the floor never depends on an old calibration."""
    return float(hmd.pos[1] - hmd.height) if hmd is not None else fallback


def compute_poses(P, vis, hmd, cal, cfg, enabled, last, feet=None, now=0.0):
    """Single camera: P is 33x3 landmarks in camera space (y up). Returns [(id, pos, quat)]."""
    return build_poses(camera_points(P, hmd, cal, vis), vis, live_floor(hmd, cal.floor), cfg, enabled, last, feet, now)


# --------------------------------------------------------------------------- ui

def draw(frame, landmarks, mirror, lines):
    """A copy of the frame with the skeleton (and optional text lines) drawn on it."""
    load_mediapipe()
    h, w = frame.shape[:2]
    frame = cv2.flip(frame, 1) if mirror else frame.copy()
    if landmarks:
        pts = [(int(((1 - l.x) if mirror else l.x) * w), int(l.y * h)) for l in landmarks]
        for c in vision.PoseLandmarksConnections.POSE_LANDMARKS:
            cv2.line(frame, pts[c.start], pts[c.end], (0, 255, 0), 2)
        for p in pts:
            cv2.circle(frame, p, 3, (0, 0, 255), -1)
    return put_lines(frame, lines)


def put_lines(img, lines):
    y = 28
    for text, color in lines:
        text = ascii_fold(text)
        cv2.putText(img, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(img, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
        y += 28
    return img


def list_cameras():
    """Real cameras with their names (cameras.py does the work; virtual ones are listed, not opened)."""
    found = cameras.list_devices(do_probe=True)
    for c in found:
        label = f"{c['name'] or '?'} [{c['kind']}]"
        if c["working"]:
            log(T("camera_ok", i=c["index"], w=c["width"], h=c["height"]) + f"  {label}")
        else:
            log(f"{c['index']}: {label}")
    return found


class Ipc:
    """JSON lines over stdin (commands) / stdout (events) for the WPF app."""

    def __init__(self):
        self.out = sys.stdout
        sys.stdout = sys.stderr  # stray prints must not corrupt the protocol
        self.lock = threading.Lock()
        self.commands = queue.Queue()
        self.closed = False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in sys.stdin:
            try:
                self.commands.put(json.loads(line))
            except ValueError:
                pass
        self.closed = True  # stdin closed = the app is gone

    def emit(self, **msg):
        data = json.dumps(msg, separators=(",", ":"))
        with self.lock:
            try:
                self.out.write(data + "\n")
                self.out.flush()
            except (OSError, ValueError):
                self.closed = True

    def log(self, *args):
        self.emit(type="log", msg=" ".join(str(a) for a in args))


# --------------------------------------------------------------------------- cameras

def create_landmarker(model):
    load_mediapipe()
    return vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=mp_tasks.BaseOptions(model_asset_path=os.path.join(HERE, f"pose_landmarker_{model}.task")),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    ))


class Result:
    """One processed frame of one camera."""

    __slots__ = ("frame", "stamp", "P", "vis", "landmarks", "raw", "img", "flipped", "vis_raw", "Pu")

    def __init__(self, frame, stamp, P=None, vis=None, landmarks=None):
        self.frame, self.stamp, self.P, self.vis, self.landmarks = frame, stamp, P, vis, landmarks
        self.Pu = None  # P before smoothing (left/right fixed): the feet are smoothed in room coordinates
        self.raw = self.img = self.vis_raw = None  # unfiltered landmarks (camera space) and image positions (0..1), for recordings
        self.flipped = False  # left/right were swapped back (seen from behind)


class CameraWorker:
    """A camera with its own pose model and filter on its own thread. MediaPipe releases the GIL
    while it runs, so two cameras are processed in parallel (measured: ~25 fps each with 'full')."""

    def __init__(self, index, cfg, cam=None, landmarker=None):
        self.index = index
        self.cam = cam or Camera(index, cfg["camera_width"], cfg["camera_height"], cfg["camera_fps"])
        try:
            self.landmarker = landmarker or create_landmarker(cfg["model"])
        except Exception:
            self.cam.close()
            raise
        self.filt = OneEuro(*filter_params(cfg))
        # (up, forward) of the headset in this camera's space, kept current by the main loop once the
        # camera is calibrated; used to catch MediaPipe swapping left and right on a back view.
        self.facing_ref = None
        self.lock = threading.Lock()
        self.result, self.result_id = None, 0
        self.fps = self.infer_ms = 0.0
        self.error = None
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            self._loop()
        except Exception as e:  # reported by the main loop, which then stops the engine
            self.error = e
            if _file_log:
                _file_log.exception(f"camera {self.index} worker crashed")

    def _loop(self):
        last_id, last_ts, t0 = 0, -1, time.monotonic()
        frames, fps_t = 0, time.monotonic()
        while self.running:
            frame, fid, stamp = self.cam.latest()
            if frame is None or fid == last_id:
                time.sleep(0.002)
                continue
            last_id = fid
            ts = max(int((stamp - t0) * 1000), last_ts + 1)
            last_ts = ts
            t = time.perf_counter()
            res = self.landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), ts)
            ms = (time.perf_counter() - t) * 1000
            self.infer_ms = ms if not self.infer_ms else 0.9 * self.infer_ms + 0.1 * ms
            r = Result(frame, stamp)
            if res.pose_world_landmarks:
                r.img = np.array([[l.x, l.y] for l in res.pose_landmarks[0]])
                r.vis_raw = np.array([l.visibility or 0.0 for l in res.pose_landmarks[0]])
                r.vis = edge_visibility(r.vis_raw, r.img)
                r.raw = np.array([[l.x, -l.y, -l.z] for l in res.pose_world_landmarks[0]])  # camera space, y up
                r.Pu, r.flipped = fix_back_view(r.raw, self.facing_ref)
                r.P = self.filt(r.Pu, stamp, visibility_weight(r.vis))
                r.landmarks = res.pose_landmarks[0]
            with self.lock:
                self.result, self.result_id = r, self.result_id + 1
            frames += 1
            now = time.monotonic()
            if now - fps_t >= 1.0:
                self.fps, frames, fps_t = frames / (now - fps_t), 0, now

    def latest(self):
        with self.lock:
            return self.result, self.result_id

    @property
    def live_fps(self):
        return 0.0 if self.cam.lost else self.fps

    def close(self):
        self.running = False
        self.thread.join(timeout=2)
        self.landmarker.close()
        self.cam.close()


def compose_preview(items, mirror, lines):
    """Side by side preview of every camera (each scaled to 240 px high when there are several)."""
    images = [draw(frame, landmarks, mirror, []) for frame, landmarks in items]
    if len(images) > 1:
        images = [cv2.resize(img, (int(img.shape[1] * 240 / img.shape[0]), 240)) for img in images]
        img = np.hstack(images)
    else:
        img = images[0]
    return put_lines(img, lines)


def frame_brightness(frame):
    return float(cv2.cvtColor(cv2.resize(frame, (64, 48)), cv2.COLOR_BGR2GRAY).mean())


def camera_tilt_deg(P):
    """How far a camera looks down (+) from level, from the standing player's plumb line."""
    up = (P[L_SH] + P[R_SH]) / 2 - (P[L_ANK] + P[R_ANK]) / 2
    return math.degrees(math.atan2(up[2], up[1]))


# --------------------------------------------------------------------------- setup assistant

class Assistant:
    """Engine side of the app's setup assistant (see placement.py)."""

    def __init__(self, cfg, n_cameras):
        self.mode = None  # None, "room" or "spot"
        self.cameras = max(1, min(3, n_cameras if n_cameras > 1 else 2))
        self.hfov = float(cfg["camera_hfov"])
        self.walking = False
        self.walk = []
        self.play_area = None
        self.play_area_t = 0.0
        self._cov_key = self._cov = None
        self._act_key = self._act = None

    def apply(self, c):
        cmd = c.get("cmd")
        if cmd == "assistant":
            self.mode = c.get("mode") if c.get("mode") in ("room", "spot") else None
            if c.get("cameras") is not None:
                self.cameras = max(1, min(3, int(c["cameras"])))
            if c.get("hfov") is not None:
                self.hfov = float(np.clip(float(c["hfov"]), 40, 120))
        elif cmd == "room_walk":
            self.walking = bool(c.get("value"))
            if self.walking:
                self.walk = []

    def record(self, hmd):
        if self.walking and hmd is not None:
            p = hmd.std_pos
            if not self.walk or math.dist(self.walk[-1], (p[0], p[2])) >= 0.1:
                self.walk.append((float(p[0]), float(p[2])))

    def room_message(self, vr, hmd, calset, workers, now):
        if now - self.play_area_t > 2.0:
            self.play_area, self.play_area_t = vr.play_area(), now
        room = placement.simplify_walk(self.walk) if len(self.walk) >= 6 else None
        eye = float(hmd.height) if hmd is not None else 1.6
        spots, area = placement.recommend(self.cameras, self.hfov, eye, self.play_area, None if self.walking else room)

        actual = []
        if hmd is not None:
            for w in workers:
                cal = calset.cals[w.index]
                res, _ = w.latest()
                if not cal.ready or res is None or res.P is None:
                    continue
                pts = [(l.x, l.y) for l in res.landmarks]
                eyes = min(pts[i][1] for i in (L_EYE, R_EYE))
                heels = max(pts[i][1] for i in (L_HEEL, R_HEEL))
                Wc = camera_points(res.P, hmd, cal, res.vis)
                player = hmd.to_standing((Wc[L_HIP] + Wc[R_HIP]) / 2)
                fwd = hmd.dir_to_standing(-cal.R[:, 2])
                est = placement.estimate_camera((fwd[0], fwd[2]), (player[0], player[2]), heels - eyes, eye, self.hfov)
                if est:
                    est["index"] = w.index
                    actual.append(est)

        bounds = placement.map_bounds(area, room, spots + actual)
        key = (self.cameras, self.hfov, round(eye, 1), tuple(round(v, 2) for v in area), tuple(bounds))
        if key != self._cov_key:
            self._cov_key, self._cov = key, placement.coverage(spots, self.hfov, eye, bounds)
        act_key = (tuple((round(a["x"], 1), round(a["z"], 1), round(a["facing"] / 5)) for a in actual), key)
        if actual and act_key != self._act_key:
            self._act_key, self._act = act_key, placement.coverage(actual, self.hfov, eye, bounds)

        player = None
        if hmd is not None:
            fwd = -hmd.std_rot[:, 2]
            player = {"x": float(hmd.std_pos[0]), "z": float(hmd.std_pos[2]), "facing": placement.heading((fwd[0], fwd[2]))}
        return dict(type="room", vr=vr.connected, hmd=hmd is not None, walking=self.walking,
                    play_area=self.play_area, area=area, room=room, walk_points=len(self.walk),
                    spots=spots, actual=actual, player=player, hfov=self.hfov, cameras=self.cameras,
                    min_distance=placement.min_distance(eye, self.hfov), bounds=bounds,
                    coverage=self._cov, coverage_actual=self._act if actual else None)

    def spot_message(self, workers):
        cams = []
        for w in workers:
            res, _ = w.latest()
            if res is None:
                cams.append({"index": w.index, "score": 0, "hints": ["spot_no_image"]})
                continue
            pts = [(l.x, l.y) for l in res.landmarks] if res.landmarks else None
            tilt = camera_tilt_deg(res.P) if res.P is not None else 0.0
            score, hints = placement.spot_check(pts, res.vis, frame_brightness(res.frame), w.live_fps, tilt)
            cams.append({"index": w.index, "score": score, "hints": hints})
        return dict(type="spot", cameras=cams)


# --------------------------------------------------------------------------- main

LIVE_SETTINGS = {"filter_min_cutoff", "filter_beta", "depth_smoothing", "min_visibility", "floor_lock", "foot_lock",
                 "ankle_height", "mirror_preview", "remember_calibration", "preview_fps", "language", "camera_hfov",
                 "auto_calibrate", "gesture_calibrate"}


class State:
    def __init__(self, cfg, workers):
        self.cfg = cfg
        self.workers = workers
        self.enabled = {n: bool(cfg["trackers"].get(n, False)) for n in TRACKERS}
        self.feet = FootLock()
        self.carry = Carry()
        self.room_filters = {w.index: OneEuro(*filter_params(cfg)) for w in workers}
        self.recorder = recorder.Recorder(os.path.join(LOG_DIR, "recordings"))
        self.cal = CalibrationSet([w.index for w in workers])
        self.assistant = Assistant(cfg, len(workers))
        self.last_poses = {}
        self.paused = False
        self.preview = True
        self.quit = False
        self.headset_name = None  # set when SteamVR connects

    def toggle_pause(self):
        self.paused = not self.paused
        beep(500 if self.paused else 1000, 150)

    def save_calibration(self):
        if self.cfg["remember_calibration"]:
            self.cal.save(self.headset_name)

    def adjust_yaw(self, deg):
        if self.cal.ready:
            self.cal.adjust(rotation=rot_y(math.radians(deg)))
            self.save_calibration()

    def adjust_scale(self, factor):
        if self.cal.ready:
            self.cal.adjust(scale=factor)
            self.save_calibration()

    def apply(self, c, now):
        cmd = c.get("cmd")
        if cmd == "calibrate":
            self.cal.start(now)
        elif cmd == "pause":
            if bool(c.get("value", not self.paused)) != self.paused:
                self.toggle_pause()
        elif cmd == "preview":
            self.preview = bool(c.get("value", True))
        elif cmd == "yaw":
            self.adjust_yaw(float(c.get("delta", 0)))
        elif cmd == "scale":
            self.adjust_scale(float(c.get("factor", 1)))
        elif cmd == "tracker":
            name = c.get("name")
            if name in self.enabled:
                self.enabled[name] = bool(c.get("value"))
                self.cfg["trackers"][name] = self.enabled[name]
                if not self.enabled[name]:
                    self.last_poses.pop(name, None)
        elif cmd in ("assistant", "room_walk"):
            self.assistant.apply(c)
        elif cmd == "record":
            if c.get("value", True):
                seconds, delay = float(c.get("seconds", 60)), float(c.get("delay", 15))
                self.recorder.start(now, seconds, delay, {
                    "version": VERSION, "cameras": [w.index for w in self.workers],
                    "camera_latency": self.cfg["camera_latency"], "model": self.cfg["model"],
                    "trackers": self.cfg["trackers"], "headset": self.headset_name})
                log(T("rec_waiting", s=round(delay), n=round(seconds)))
            elif self.recorder.state == "waiting":
                self.recorder.cancel()
                log(T("rec_cancelled"))
            elif self.recorder.state == "recording":
                self.recorder.stop_at = now  # saved by the main loop
        elif cmd == "set":
            key = c.get("key")
            if key not in LIVE_SETTINGS:
                return
            checked, problems = validate_config({**self.cfg, key: c.get("value")})
            for name, kw in problems:
                log(T(name, **kw))
            self.cfg[key] = checked[key]
            for w in self.workers:
                w.filt.min_cutoff, w.filt.beta = filter_params(self.cfg)
            for f in self.room_filters.values():
                f.min_cutoff, f.beta = filter_params(self.cfg)
            if key == "language":
                messages.set_language(self.cfg["language"] or system_language())
            if key == "camera_hfov":
                self.assistant.hfov = float(self.cfg["camera_hfov"])
        elif cmd == "quit":
            self.quit = True


def overlay_lines(st, vr, no_vr, fps, person, sent, now):
    cal = st.cal
    lines = [(T("ov_fps", fps=fps, person=T("ov_person_yes" if person else "ov_person_no")), (255, 255, 255))]
    if no_vr:
        lines.append((T("ov_vr_off"), (0, 200, 255)))
    elif vr.connected:
        lines.append((T("ov_vr_on"), (0, 255, 0)))
    else:
        lines.append((T("ov_vr_wait"), (0, 200, 255)))
    if cal.phase == "countdown":
        lines.append((T("ov_countdown", t=cal.remaining(now)), (0, 255, 255)))
    elif cal.phase == "collect":
        lines.append((T("ov_collect"), (0, 255, 255)))
    elif not cal.ready:
        lines.append((T("ov_nocal"), (0, 200, 255)))
    else:
        lines.append((T("ov_cal", scale=cal.scale, n=sent), (0, 255, 0)))
    if st.paused:
        lines.append((T("ov_paused"), (0, 0, 255)))
    return lines


def main():
    global _out
    ap = argparse.ArgumentParser(description="WebcamFBT")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--camera", type=int, help="camera index (overrides config)")
    ap.add_argument("--model", choices=["lite", "full", "heavy"], help="pose model (overrides config)")
    ap.add_argument("--no-vr", action="store_true", help="preview only, do not connect to SteamVR")
    ap.add_argument("--list-cameras", action="store_true")
    ap.add_argument("--ipc", action="store_true", help="no window; talk JSON with the WPF app")
    args = ap.parse_args()

    ipc = Ipc() if args.ipc else None
    if ipc:
        _out = ipc.log
    else:
        sys.stdout.reconfigure(errors="replace")  # the console code page may lack ı/ş/…
    setup_file_log()

    cfg, problems = load_config(args.config)
    messages.set_language(cfg["language"] or system_language())
    messages.set_hotkeys(cfg["hotkey_calibrate"], cfg["hotkey_pause"])
    for name, kw in problems:
        log(T(name, **kw))

    try:
        run(args, cfg, ipc)
    except Exception as e:
        if _file_log:
            _file_log.exception("engine crashed")
        log(T("crashed", err=e))
        if ipc:
            ipc.emit(type="error", msg=str(e))
        sys.exit(1)


def emit_status(ipc, args, vr, st, driver, *, person, hmd, sent, now):
    workers, cal = st.workers, st.cal
    main_cam = workers[0]
    ipc.emit(type="status", fps=round(main_cam.live_fps, 1), person=person,
             vr="off" if args.no_vr else ("connected" if vr.connected else "waiting"),
             hmd=hmd, calibrated=cal.ready, calib_saved=cal.from_file,
             calib_phase=cal.phase, calib_remaining=round(cal.remaining(now), 1),
             scale=round(cal.scale, 3), sent=sent, paused=st.paused,
             infer_ms=round(float(np.mean([w.infer_ms for w in workers])), 1),
             camera_ok=not main_cam.cam.lost, camera_state=main_cam.cam.state, driver=driver.state,
             headset=vr.headset,
             recording=st.recorder.state, recording_left=round(max(0.0, st.recorder.remaining(now))),
             calibrated_count=cal.ready_count(),
             cameras=[{"index": w.index, "fps": round(w.live_fps, 1), "state": w.cam.state,
                       "infer_ms": round(w.infer_ms, 1), "calibrated": cal.cals[w.index].ready,
                       "person": bool(w.latest()[0] is not None and w.latest()[0].P is not None)} for w in workers])


def open_workers(cfg, ipc):
    """The main camera must open; extra cameras are optional and are skipped with a message.

    Opening a DirectShow camera takes ~2 s, so all cameras open on their own threads while this
    thread imports MediaPipe and creates the pose models; startup then costs the slowest of these
    instead of their sum."""
    indices = [cfg["camera_index"]] + cfg["extra_cameras"]
    cams, errors = {}, {}

    def open_one(i):
        try:
            cams[i] = Camera(i, cfg["camera_width"], cfg["camera_height"], cfg["camera_fps"])
        except Exception as e:
            errors[i] = e

    threads = [threading.Thread(target=open_one, args=(i,), daemon=True) for i in indices]
    for t in threads:
        t.start()
    try:
        models = {i: create_landmarker(cfg["model"]) for i in indices}
    except Exception:
        for t in threads:
            t.join()
        for c in cams.values():  # an open camera thread would keep the process alive
            c.close()
        raise
    for t in threads:
        t.join()
    main_index = indices[0]
    if main_index in errors:
        for c in cams.values():
            c.close()
        for m in models.values():
            m.close()
        raise errors[main_index]
    workers = []
    for i in indices:
        if i in cams:
            workers.append(CameraWorker(i, cfg, cams[i], models[i]))
        else:
            models[i].close()
            log(T("extra_camera_failed", i=i, err=errors[i]))
            beep_warning()
    return workers


FRESH = 0.15  # s: results older than this are not fused (that camera stalled)


def run(args, cfg, ipc):
    if args.list_cameras:
        cams = list_cameras()
        if ipc:
            ipc.emit(type="cameras", list=cams)
        return

    if args.camera is not None:
        cfg["camera_index"] = args.camera
        cfg["extra_cameras"] = [i for i in cfg["extra_cameras"] if i != args.camera]
    if args.model:
        cfg["model"] = args.model
    if cfg["high_priority"]:
        raise_priority()

    try:
        workers = open_workers(cfg, ipc)
    except Exception as e:
        log(T("error", err=e))
        if ipc:
            ipc.emit(type="error", msg=str(e))
        sys.exit(2)

    st = State(cfg, workers)
    cal = st.cal
    vr = VRLink(not args.no_vr)
    sender = Sender(cfg["udp_port"])
    driver = DriverWatch(cfg["udp_port"])
    hotkeys = Hotkeys({"calibrate": cfg["hotkey_calibrate"], "pause": cfg["hotkey_pause"]})
    for action, key in hotkeys.failed:
        log(T("hotkey_failed", key=key))
    seen = {w.index: 0 for w in workers}
    still, hands = StillDetector(), HandsUp()
    hist = HmdHistory()
    auto_running, auto_wait_until = False, 0.0
    last_status = last_preview = last_assistant = last_vr_poll = 0.0
    last_space_warning = -1e9
    hmd = None
    win = "WebcamFBT"
    indices = [w.index for w in workers]
    if ipc:
        ipc.emit(type="started", camera=cfg["camera_index"], model=cfg["model"], cameras=indices)
    else:
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        log(T("help"))
    log(T("engine_started", cam=", ".join(map(str, indices)), model=cfg["model"]) + f" (v{VERSION})")

    try:
        while not st.quit:
            if ipc:
                if ipc.closed:
                    break
                while not ipc.commands.empty():
                    st.apply(ipc.commands.get_nowait(), time.monotonic())
            now = time.monotonic()
            for w in workers:
                if w.error:
                    raise RuntimeError(f"camera {w.index}: {w.error}")
                w.cam.check_stall(now)
                while not w.cam.events.empty():
                    event = w.cam.events.get_nowait()
                    log(T(event, i=w.index))
                    if event != "camera_back":
                        beep_warning()

            new = {}
            for w in workers:
                res, rid = w.latest()
                if rid != seen[w.index]:
                    seen[w.index] = rid
                    new[w.index] = res

            # SteamVR and the hotkeys are served even while no camera delivers frames. The headset is
            # polled at ~90 Hz so the pose of the moment each frame was taken can be looked up.
            if new or now - last_vr_poll >= 0.011:
                last_vr_poll = now
                hmd = vr.poll()
                hist.add(hmd)
                st.recorder.pose(hmd)
                st.assistant.record(hmd)
            for w in workers:
                c = cal.cals[w.index]
                w.facing_ref = (c.R.T @ np.array([0.0, 1.0, 0.0]), c.R.T @ -hmd.rot[:, 2]) if c.ready and hmd else None
            rec_event = st.recorder.tick(now, cal.ready)
            if rec_event == "started":
                st.recorder.calibration(now, cal)
                log(T("rec_started", n=round(st.recorder.stop_at - now)))
                beep_twice(1000)
            elif rec_event == "due":
                try:
                    path = st.recorder.save()
                    log(T("rec_saved", path=path) if path else T("rec_empty"))
                except Exception as e:
                    log(T("error", err=e))
                beep(1500, 300)
            if vr.headset_changed:
                vr.headset_changed = False
                st.headset_name = vr.headset["name"]
            if vr.just_connected:
                vr.just_connected = False
                st.headset_name = vr.headset["name"]
                if cal.ready and cfg["auto_calibrate"]:
                    # SteamVR was restarted: its tracking space may have moved, and recalibrating
                    # costs the player nothing more than standing still for a moment.
                    cal.reset()
                    log(T("calib_reset_vr"))
                if cfg["remember_calibration"] and not cal.ready:
                    loaded = cal.load(st.headset_name)
                    if loaded:
                        log(T("calib_loaded", cams=", ".join(map(str, loaded))))
                if not cal.ready:
                    log(T("calib_needed_auto" if cfg["auto_calibrate"] else "calib_needed"))
                    beep(500, 200)
            if vr.space_event:
                vr.space_event = False
                # Only a hint, at most every 5 minutes: Steam Link reports this on every wake from standby.
                if cal.ready and now - last_space_warning > 300:
                    last_space_warning = now
                    log(T("recenter_maybe"))
                    if ipc:
                        ipc.emit(type="recenter", corrected=False)
            if hands.update(now, hmd) and cfg["gesture_calibrate"] and cal.phase is None:
                log(T("calib_gesture"))
                auto_running = False
                cal.start(now)
            for action in hotkeys.pressed():
                if action == "calibrate":
                    auto_running = False
                    cal.start(now)
                elif action == "pause":
                    st.toggle_pause()

            if not new:
                time.sleep(0.002)
                if ipc and now - last_status >= 0.5:  # keep the UI informed while cameras are gone
                    last_status = now
                    emit_status(ipc, args, vr, st, driver, person=False, hmd=hmd is not None, sent=0, now=now)
                continue
            # Hands-free calibration: only while not calibrated, never over a working calibration.
            main = new.get(workers[0].index)
            if cfg["auto_calibrate"] and not cal.ready and cal.phase is None and main is not None:
                if now >= auto_wait_until and still.update(now, main.P, main.vis, hmd):
                    still.reset()
                    log(T("calib_auto"))
                    auto_running = True
                    cal.start(now, auto=True)
            for i, r in new.items():
                st.recorder.frame(i, r)
            results = cal.update(now, {i: (r.P, r.vis) for i, r in new.items()}, hmd)
            if results:
                ok = any(results.values())
                if ok:
                    st.save_calibration()
                    st.recorder.calibration(now, cal)
                elif auto_running:
                    auto_wait_until = now + 3.0
                if ipc:
                    ipc.emit(type="calibration", ok=ok, scale=cal.scale, auto=auto_running,
                             reason=None if ok else cal.failure, cameras={str(i): r for i, r in results.items()})
                auto_running = False

            # Fuse the latest view of every calibrated camera that is still fresh.
            latest = {w.index: w.latest()[0] for w in workers}
            views, newest = [], 0.0
            if hmd is not None:
                for i, r in latest.items():
                    c = cal.cals[i]
                    if c.ready and r is not None and r.P is not None and now - r.stamp < FRESH:
                        then = hist.at(r.stamp - cfg["camera_latency"])
                        views.append((tracked_points(r.P, r.Pu, r.vis, r.stamp, c, hmd, then, st.room_filters[i]),
                                      r.vis, c.R[:, 2]))
                        newest = max(newest, r.stamp)
            sent = 0
            if views and not st.paused:
                W, vis = fuse_views(views, hmd)
                poses = build_poses(W, vis, live_floor(hmd, cal.floor), cfg, st.enabled, st.last_poses, st.feet, newest,
                                    st.carry)
                if poses:
                    sender.send(poses)
                    sent = len(poses)
            problem = driver.update(vr, sent > 0, now)
            if problem:
                log(T(problem, port=cfg["udp_port"], have=driver.driver_protocol, want=PROTOCOL))
                if problem != "driver_ok":
                    beep_warning()

            person = any(r is not None and r.P is not None for r in latest.values())
            items = [(r.frame, r.landmarks) for r in latest.values() if r is not None]
            if ipc:
                if now - last_status >= 0.2:
                    last_status = now
                    emit_status(ipc, args, vr, st, driver, person=person, hmd=hmd is not None, sent=sent, now=now)
                if st.assistant.mode and now - last_assistant >= 0.2:
                    last_assistant = now
                    msg = (st.assistant.room_message(vr, hmd, cal, workers, now) if st.assistant.mode == "room"
                           else st.assistant.spot_message(workers))
                    ipc.emit(**msg)
                if st.preview and items and now - last_preview >= 1.0 / max(1, cfg["preview_fps"]):
                    last_preview = now
                    ok, buf = cv2.imencode(".jpg", compose_preview(items, cfg["mirror_preview"], []),
                                           [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if ok:
                        ipc.emit(type="frame", jpg=base64.b64encode(buf.tobytes()).decode("ascii"))
                continue

            if not items:
                continue
            cv2.imshow(win, compose_preview(items, cfg["mirror_preview"],
                                            overlay_lines(st, vr, args.no_vr, workers[0].live_fps, person, sent, now)))
            k = cv2.waitKey(1) & 0xFF
            if k in (27, ord("q")) or cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
                break
            if k == ord("c"):
                cal.start(now)
            elif k == ord("p"):
                st.toggle_pause()
            elif k in (ord("["), ord("]")):
                st.adjust_yaw(-2 if k == ord("[") else 2)
            elif k in (ord("-"), ord("=")):
                st.adjust_scale(0.98 if k == ord("-") else 1.02)
    finally:
        hotkeys.close()
        for w in workers:
            w.close()
        if not ipc:
            cv2.destroyAllWindows()
        if vr.connected:
            vr.disconnect()
        log(T("engine_stopped"))


if __name__ == "__main__":
    main()
