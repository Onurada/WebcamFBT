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
import mediapipe as mp
import numpy as np
import openvr
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision

import messages
from messages import T, ascii_fold

HERE = os.path.dirname(os.path.abspath(__file__))
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
    "left_foot": [L_ANK, L_HEEL, L_TOE],
    "right_foot": [R_ANK, R_HEEL, R_TOE],
    "left_knee": [L_KNEE],
    "right_knee": [R_KNEE],
    "chest": [L_SH, R_SH],
    "left_elbow": [L_ELB],
    "right_elbow": [R_ELB],
}

# HMD origin -> midpoint between the ears, in HMD local space (x right, y up, +z backwards)
HMD_TO_EARS = np.array([0.0, -0.02, 0.09])



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
    "remember_calibration": True,
    "high_priority": True,
    "preview_fps": 20,
    "hotkey_calibrate": "F8",
    "hotkey_pause": "F9",
}
RANGES = {
    "camera_index": (0, 15), "camera_width": (160, 3840), "camera_height": (120, 2160), "camera_fps": (5, 120),
    "filter_min_cutoff": (0.05, 10.0), "filter_beta": (0.0, 20.0), "depth_smoothing": (0.0, 1.0),
    "min_visibility": (0.0, 1.0), "ankle_height": (0.0, 0.3), "udp_port": (1024, 65535), "preview_fps": (1, 60),
}
CHOICES = {"model": ("lite", "full", "heavy"), "language": (None,) + messages.LANGUAGES}

LOG_DIR = os.path.join(HERE, "..", "logs")
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
    """Grabs frames on its own thread and reconnects by itself if the camera drops out."""

    LOST_AFTER = 2.0  # s without frames before the camera counts as disconnected

    def __init__(self, index, width, height, fps):
        self.index, self.width, self.height, self.fps = index, width, height, fps
        self.cap = self._open()
        if self.cap is None:
            raise RuntimeError(T("camera_open_failed", i=index))
        self.frame, self.frame_id, self.stamp = None, 0, 0.0
        self.last_ok = time.monotonic()
        self.lost = False
        self.ever_ok = False  # no frame yet: "busy/blocked" rather than "disconnected"
        self.events = queue.Queue()  # message keys for the main thread to log
        self.lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

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
        if abs(cap.get(cv2.CAP_PROP_FPS) - self.fps) > 1:
            cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self):
        while self.running:
            ok, f = self.cap.read() if self.cap is not None else (False, None)
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
                if self.cap is not None:
                    self.cap.release()
                time.sleep(1.0)
                if self.running:
                    self.cap = self._open()
            else:
                time.sleep(0.01)

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
        if self.cap is not None:
            self.cap.release()


class HmdPose:
    def __init__(self, rot, pos, height, vel=None, ang_vel=None, t=0.0):
        self.rot, self.pos, self.height = rot, pos, height
        self.vel = np.zeros(3) if vel is None else vel
        self.ang_vel = np.zeros(3) if ang_vel is None else ang_vel
        self.t = t


def rotation_angle(R):
    return math.acos(float(np.clip((np.trace(R) - 1) / 2, -1.0, 1.0)))


def space_jump(prev, cur, max_gap=0.25, pos_tol=0.25, ang_tol=math.radians(30)):
    """If the headset's tracking space was reset (Quest recenter) between two poses, returns the
    transform (yaw rotation, translation) from the old space to the new one, else None.
    A real head movement is backed by the reported velocities; a space reset is not."""
    dt = cur.t - prev.t
    if dt <= 0 or dt > max_gap:
        return None
    expected = prev.pos + (prev.vel + cur.vel) / 2 * dt
    pos_err = np.linalg.norm(cur.pos - expected)
    turn = cur.rot @ prev.rot.T
    ang_err = rotation_angle(turn) - np.linalg.norm((prev.ang_vel + cur.ang_vel) / 2) * dt
    if pos_err < pos_tol and ang_err < ang_tol:
        return None
    yaw = math.atan2(turn[0, 2], turn[2, 2])  # keep the yaw part; a recenter never tilts the floor
    R = rot_y(yaw)
    return R, cur.pos - R @ prev.pos


def apply_space_jump(cal, jump):
    """Moves a calibration into the headset's new tracking space."""
    R, t = jump
    cal.R = R @ cal.R
    cal.floor += float(t[1])


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
        self.prev = None
        self.jump = None  # set by poll() when the tracking space jumped; read and cleared by the caller
        self.space_event = False  # SteamVR announced a recenter / chaperone change

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
        self.prev = None

    def poll(self):
        if not self.enabled:
            return None
        if self.sys is None:
            if time.time() < self.next_try:
                return None
            self.next_try = time.time() + 3
            try:
                self.sys = openvr.init(openvr.VRApplication_Background)
                log(T("vr_connected"))
            except Exception:
                return None
        try:
            ev = openvr.VREvent_t()
            while self.sys.pollNextEvent(ev):
                if ev.eventType == openvr.VREvent_Quit:
                    self.sys.acknowledgeQuit_Exiting()
                    self.disconnect()
                    log(T("vr_closed"))
                    return None
                if ev.eventType in self.SPACE_EVENTS:
                    self.space_event = True
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
        v, w = raw[0].vVelocity, raw[0].vAngularVelocity
        pose = HmdPose(m[:, :3], m[:, 3], std[0].mDeviceToAbsoluteTracking[1][3],
                       np.array([v[0], v[1], v[2]]), np.array([w[0], w[1], w[2]]), time.monotonic())
        if self.prev is not None:
            jump = space_jump(self.prev, pose)
            if jump is not None:
                self.jump = jump
        self.prev = pose
        return pose


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
    COUNTDOWN = 3.0
    COLLECT = 2.0

    def __init__(self):
        self.ready = False
        self.from_file = False
        self.R = np.eye(3)  # camera space -> raw VR space
        self.scale = 1.0
        self.floor = 0.0  # floor height in raw VR space
        self.phase = None
        self.t_phase = 0.0
        self.samples = []

    def start(self, now):
        self.phase, self.t_phase, self.samples = "countdown", now, []
        beep(660, 150)

    def remaining(self, now):
        total = self.COUNTDOWN if self.phase == "countdown" else self.COLLECT
        return max(0.0, total - (now - self.t_phase)) if self.phase else 0.0

    def update(self, now, P, vis, hmd):
        """Returns True/False when a calibration finishes (success/failure), otherwise None."""
        if self.phase == "countdown":
            if now - self.t_phase >= self.COUNTDOWN:
                self.phase, self.t_phase = "collect", now
                beep(880, 150)
            return None
        if self.phase != "collect":
            return None
        key = [L_EYE, R_EYE, L_SH, R_SH, L_HIP, R_HIP, L_ANK, R_ANK]
        if P is not None and hmd is not None and vis[key].min() > 0.5:
            self.samples.append((P.copy(), hmd))
        if now - self.t_phase < self.COLLECT:
            return None
        self.phase = None
        if len(self.samples) < 10:
            log(T("calib_failed"))
            beep(300, 600)
            return False
        self._solve()
        beep(1200, 300)
        return True

    def _solve(self):
        # 1) Camera tilt: the person stands upright, so ankles->ears (plumb line) must map to +Y.
        ups = [(P[L_EAR] + P[R_EAR]) / 2 - (P[L_ANK] + P[R_ANK]) / 2 for P, _ in self.samples]
        r_tilt = rot_between(np.mean(ups, axis=0), np.array([0.0, 1.0, 0.0]))

        # 2) Camera yaw: body facing direction must match where the headset looks.
        sin_sum = cos_sum = 0.0
        eye_heights, hmd_heights, floors = [], [], []
        for P, hmd in self.samples:
            Q = P @ r_tilt.T
            right = (Q[R_HIP] - Q[L_HIP]) + (Q[R_SH] - Q[L_SH])
            right[1] = 0
            body_fwd = np.cross([0.0, 1.0, 0.0], right)
            hmd_fwd = -hmd.rot[:, 2]
            d = yaw_of(hmd_fwd) - yaw_of(body_fwd)
            sin_sum += math.sin(d)
            cos_sum += math.cos(d)
            ground = min(Q[i][1] for i in (L_HEEL, R_HEEL, L_TOE, R_TOE))
            eye_heights.append((Q[L_EYE][1] + Q[R_EYE][1]) / 2 - ground)
            hmd_heights.append(hmd.height)
            floors.append(hmd.pos[1] - hmd.height)
        yaw = math.atan2(sin_sum, cos_sum)

        self.R = rot_y(yaw) @ r_tilt
        self.scale = float(np.clip(np.mean(hmd_heights) / max(np.mean(eye_heights), 0.3), 0.6, 1.6))
        self.floor = float(np.mean(floors))
        self.ready = True
        self.from_file = False
        log(T("calib_ok", yaw=math.degrees(yaw), scale=self.scale, floor=self.floor, n=len(self.samples)))

    # The camera -> SteamVR space relation stays valid until the camera moves or the
    # headset's tracking space is reset, so it is kept between sessions per camera.
    def save(self, camera_index):
        data = {"camera_index": camera_index, "R": self.R.tolist(), "scale": self.scale, "floor": self.floor,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            with open(CALIBRATION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            log(T("error", err=e))

    def load(self, camera_index):
        try:
            with open(CALIBRATION_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if data["camera_index"] != camera_index:
                return False
            self.R = np.array(data["R"], dtype=float).reshape(3, 3)
            self.scale = float(data["scale"])
            self.floor = float(data["floor"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        self.ready = self.from_file = True
        return True


# --------------------------------------------------------------------------- poses

def compute_poses(P, vis, hmd, cal, cfg, enabled, last, feet=None, now=0.0):
    """P: 33x3 landmarks in camera space (y up). Returns [(id, pos, quat)] in raw VR space."""
    ears = (P[L_EAR] + P[R_EAR]) / 2
    anchor = hmd.pos + hmd.rot @ HMD_TO_EARS
    W = anchor + (P - ears) @ (cal.scale * cal.R).T

    min_ankle = cal.floor + cfg["ankle_height"]
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

    out = []
    for tid, name in enumerate(TRACKERS):
        if not enabled[name]:
            continue
        if vis[REQUIRED[name]].min() >= cfg["min_visibility"]:
            pos, rot = builders[name]()
            last[name] = (tid, pos.copy(), quat_from_matrix(rot))
        if name in last:
            out.append(last[name])
    return out


# --------------------------------------------------------------------------- ui

def draw(frame, landmarks, mirror, lines):
    h, w = frame.shape[:2]
    if mirror:
        frame = cv2.flip(frame, 1)
    if landmarks:
        pts = [(int(((1 - l.x) if mirror else l.x) * w), int(l.y * h)) for l in landmarks]
        for c in vision.PoseLandmarksConnections.POSE_LANDMARKS:
            cv2.line(frame, pts[c.start], pts[c.end], (0, 255, 0), 2)
        for p in pts:
            cv2.circle(frame, p, 3, (0, 0, 255), -1)
    y = 28
    for text, color in lines:
        text = ascii_fold(text)
        cv2.putText(frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)
        y += 28
    return frame


def list_cameras():
    found = []
    for i in range(6):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            ok, f = cap.read()
            if ok:
                found.append({"index": i, "width": int(f.shape[1]), "height": int(f.shape[0])})
                log(T("camera_ok", i=i, w=f.shape[1], h=f.shape[0]))
            else:
                log(T("camera_no_image", i=i))
        cap.release()
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


# --------------------------------------------------------------------------- main

LIVE_SETTINGS = {"filter_min_cutoff", "filter_beta", "depth_smoothing", "min_visibility", "floor_lock", "foot_lock",
                 "ankle_height", "mirror_preview", "remember_calibration", "preview_fps", "language"}


class State:
    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = {n: bool(cfg["trackers"].get(n, False)) for n in TRACKERS}
        self.filt = OneEuro(*filter_params(cfg))
        self.feet = FootLock()
        self.cal = Calibration()
        self.last_poses = {}
        self.paused = False
        self.preview = True
        self.quit = False

    def toggle_pause(self):
        self.paused = not self.paused
        beep(500 if self.paused else 1000, 150)

    def save_calibration(self):
        if self.cfg["remember_calibration"] and self.cal.ready:
            self.cal.save(self.cfg["camera_index"])

    def adjust_yaw(self, deg):
        if self.cal.ready:
            self.cal.R = rot_y(math.radians(deg)) @ self.cal.R
            self.save_calibration()

    def adjust_scale(self, factor):
        if self.cal.ready:
            self.cal.scale *= factor
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
        elif cmd == "set":
            key = c.get("key")
            if key not in LIVE_SETTINGS:
                return
            checked, problems = validate_config({**self.cfg, key: c.get("value")})
            for name, kw in problems:
                log(T(name, **kw))
            self.cfg[key] = checked[key]
            self.filt.min_cutoff, self.filt.beta = filter_params(self.cfg)
            if key == "language":
                messages.set_language(self.cfg["language"] or system_language())
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


def emit_status(ipc, args, vr, cam, cal, st, driver, *, fps, person, hmd, sent, infer_ms, now):
    ipc.emit(type="status", fps=round(fps, 1), person=person,
             vr="off" if args.no_vr else ("connected" if vr.connected else "waiting"),
             hmd=hmd, calibrated=cal.ready, calib_saved=cal.from_file,
             calib_phase=cal.phase, calib_remaining=round(cal.remaining(now), 1),
             scale=round(cal.scale, 3), sent=sent, paused=st.paused, infer_ms=round(infer_ms, 1),
             camera_ok=not cam.lost, camera_state=cam.state, driver=driver.state)


def run(args, cfg, ipc):
    if args.list_cameras:
        cams = list_cameras()
        if ipc:
            ipc.emit(type="cameras", list=cams)
        return

    if args.camera is not None:
        cfg["camera_index"] = args.camera
    if args.model:
        cfg["model"] = args.model
    if cfg["high_priority"]:
        raise_priority()

    try:
        landmarker = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=os.path.join(HERE, f"pose_landmarker_{cfg['model']}.task")),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        ))
        cam = Camera(cfg["camera_index"], cfg["camera_width"], cfg["camera_height"], cfg["camera_fps"])
    except Exception as e:
        log(T("error", err=e))
        if ipc:
            ipc.emit(type="error", msg=str(e))
        sys.exit(2)

    st = State(cfg)
    cal = st.cal
    vr = VRLink(not args.no_vr)
    sender = Sender(cfg["udp_port"])
    driver = DriverWatch(cfg["udp_port"])
    hotkeys = Hotkeys({"calibrate": cfg["hotkey_calibrate"], "pause": cfg["hotkey_pause"]})
    for action, key in hotkeys.failed:
        log(T("hotkey_failed", key=key))
    recenter_warn_at = None  # a SteamVR space event without a measurable jump: warn a bit later
    last_id = 0
    t0 = time.monotonic()
    last_ts = -1
    fps, fps_t, fps_n = 0.0, time.monotonic(), 0
    infer_ms = 0.0
    last_status = last_preview = 0.0
    win = "WebcamFBT"
    if ipc:
        ipc.emit(type="started", camera=cfg["camera_index"], model=cfg["model"])
    else:
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        log(T("help"))
    log(T("engine_started", cam=cfg["camera_index"], model=cfg["model"]))
    if cfg["remember_calibration"] and cal.load(cfg["camera_index"]):
        log(T("calib_loaded"))

    try:
        while not st.quit:
            if ipc:
                if ipc.closed:
                    break
                while not ipc.commands.empty():
                    st.apply(ipc.commands.get_nowait(), time.monotonic())
            while not cam.events.empty():
                event = cam.events.get_nowait()
                log(T(event, i=cfg["camera_index"]))
                if event != "camera_back":
                    beep_warning()

            frame, fid, stamp = cam.latest()
            if frame is None or fid == last_id:
                time.sleep(0.002)
                if ipc and time.monotonic() - last_status >= 0.5:  # keep the UI informed while the camera is gone
                    last_status = time.monotonic()
                    emit_status(ipc, args, vr, cam, cal, st, driver, fps=0.0, person=False, hmd=False, sent=0,
                                infer_ms=0.0, now=last_status)
                continue
            last_id = fid
            now = time.monotonic()

            ts = int((stamp - t0) * 1000)
            ts = max(ts, last_ts + 1)
            last_ts = ts
            t_infer = time.perf_counter()
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
            infer_ms = 0.9 * infer_ms + 0.1 * (time.perf_counter() - t_infer) * 1000 if infer_ms else \
                (time.perf_counter() - t_infer) * 1000

            P = vis = None
            if res.pose_world_landmarks:
                vis = np.array([l.visibility or 0.0 for l in res.pose_landmarks[0]])
                raw = np.array([[l.x, -l.y, -l.z] for l in res.pose_world_landmarks[0]])  # camera space, y up
                P = st.filt(raw, stamp, visibility_weight(vis))

            hmd = vr.poll()

            # Quest recenter / tracking space reset: move the calibration along with the space.
            if vr.jump is not None:
                jump, vr.jump = vr.jump, None
                recenter_warn_at = None
                if cal.ready and cal.phase is None:
                    apply_space_jump(cal, jump)
                    st.feet = FootLock()
                    st.last_poses.clear()
                    st.save_calibration()
                    log(T("recenter_fixed"))
                    beep_warning()
                    if ipc:
                        ipc.emit(type="recenter", corrected=True)
            if vr.space_event:
                vr.space_event = False
                recenter_warn_at = now + 1.0
            if recenter_warn_at is not None and now >= recenter_warn_at:
                recenter_warn_at = None
                if cal.ready:
                    log(T("recenter_maybe"))
                    beep_warning()
                    if ipc:
                        ipc.emit(type="recenter", corrected=False)

            for action in hotkeys.pressed():
                if action == "calibrate":
                    cal.start(now)
                elif action == "pause":
                    st.toggle_pause()
            result = cal.update(now, P, vis, hmd)
            if result:
                st.save_calibration()
            if ipc and result is not None:
                ipc.emit(type="calibration", ok=result, scale=cal.scale)

            sent = 0
            if P is not None and hmd is not None and cal.ready and not st.paused:
                poses = compute_poses(P, vis, hmd, cal, cfg, st.enabled, st.last_poses, st.feet, stamp)
                if poses:
                    sender.send(poses)
                    sent = len(poses)
            problem = driver.update(vr, sent > 0, now)
            if problem:
                log(T(problem, port=cfg["udp_port"], have=driver.driver_protocol, want=PROTOCOL))
                if problem != "driver_ok":
                    beep_warning()

            fps_n += 1
            if now - fps_t >= 1.0:
                fps, fps_t, fps_n = fps_n / (now - fps_t), now, 0

            landmarks = res.pose_landmarks[0] if res.pose_landmarks else None
            if ipc:
                if now - last_status >= 0.2:
                    last_status = now
                    emit_status(ipc, args, vr, cam, cal, st, driver, fps=fps, person=P is not None,
                                hmd=hmd is not None, sent=sent, infer_ms=infer_ms, now=now)
                if st.preview and now - last_preview >= 1.0 / max(1, cfg["preview_fps"]):
                    last_preview = now
                    img = draw(frame, landmarks, cfg["mirror_preview"], [])
                    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if ok:
                        ipc.emit(type="frame", jpg=base64.b64encode(buf.tobytes()).decode("ascii"))
                continue

            cv2.imshow(win, draw(frame, landmarks, cfg["mirror_preview"],
                                 overlay_lines(st, vr, args.no_vr, fps, P is not None, sent, now)))
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
        cam.close()
        landmarker.close()
        if not ipc:
            cv2.destroyAllWindows()
        if vr.connected:
            vr.disconnect()
        log(T("engine_stopped"))


if __name__ == "__main__":
    main()
