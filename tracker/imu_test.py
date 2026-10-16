"""Checks an IMU tracker (imu_tracker/imu_tracker.ino) before it is used for tracking.

    .venv\\Scripts\\python.exe tracker\\imu_test.py

Answers the trackers' "hello" broadcasts so they start streaming to this PC, then prints twice a
second per tracker: samples per second, lost packets, gravity as measured (should be ~1.00 g while
the tracker lies still), gyro bias, tilt from gravity, and how far the yaw from the gyro alone drifts
(the engine will correct that drift with the camera). Stop the engine first: both use UDP 39580.
"""

import argparse
import math
import socket
import struct
import time

import numpy as np

PORT = 39580
MAGIC = 0x554D4957
VERSION = 1
TYPE_SAMPLE, TYPE_HELLO, TYPE_ACK = 1, 2, 3
PACKET = struct.Struct("<IBBHII3h3hH")
ACK = struct.Struct("<IBBH")
ACCEL_LSB_PER_G = 2048.0
GYRO_LSB_PER_DPS = 16.4


def parse(data):
    """(type, seq, tracker id, t_us, accel g (3), gyro deg/s (3), battery mV) or None."""
    if len(data) < PACKET.size:
        return None
    magic, version, kind, seq, tid, t_us, ax, ay, az, gx, gy, gz, batt = PACKET.unpack_from(data)
    if magic != MAGIC or version != VERSION:
        return None
    accel = np.array([ax, ay, az]) / ACCEL_LSB_PER_G
    gyro = np.array([gx, gy, gz]) / GYRO_LSB_PER_DPS
    return kind, seq, tid, t_us, accel, gyro, batt


def ack():
    return ACK.pack(MAGIC, VERSION, TYPE_ACK, 0)


class Stats:
    def __init__(self):
        self.count = self.lost = 0
        self.last_seq = None
        self.last_t = None
        self.accel = []
        self.gyro = []
        self.bias = None
        self.yaw = 0.0

    def add(self, seq, t_us, accel, gyro):
        if self.last_seq is not None:
            self.lost += (seq - self.last_seq - 1) % 65536
        self.last_seq = seq
        self.count += 1
        self.accel.append(accel)
        self.gyro.append(gyro)
        if self.last_t is not None and self.bias is not None:
            dt = ((t_us - self.last_t) % 2 ** 32) / 1e6
            if dt < 0.1:
                self.yaw += (gyro[2] - self.bias[2]) * dt
        self.last_t = t_us


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long (0 = until Ctrl+C)")
    args = ap.parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", PORT))
    sock.settimeout(0.1)
    print(f"Listening on UDP {PORT}. Power the tracker on; keep it still for the first seconds.")
    stats, acked, start = {}, {}, time.monotonic()
    next_print = start + 0.5
    while not args.seconds or time.monotonic() - start < args.seconds:
        try:
            data, addr = sock.recvfrom(256)
        except socket.timeout:
            data = None
        except ConnectionResetError:  # Windows reports an unreachable earlier send this way
            data = None
        now = time.monotonic()
        if data:
            p = parse(data)
            if p:
                kind, seq, tid, t_us, accel, gyro, batt = p
                if now - acked.get(addr, 0) > 1.0:  # tell the tracker this PC is listening
                    sock.sendto(ack(), addr)
                    acked[addr] = now
                    if kind == TYPE_HELLO:
                        print(f"tracker {tid:08X} at {addr[0]} said hello")
                if kind == TYPE_SAMPLE:
                    stats.setdefault(tid, Stats()).add(seq, t_us, accel, gyro)
        if now >= next_print:
            for tid, s in stats.items():
                if not s.accel:
                    continue
                a, g = np.array(s.accel), np.array(s.gyro)
                still = np.linalg.norm(g, axis=1).max() < 3.0
                if still and s.bias is None and s.count > 100:
                    s.bias = g.mean(axis=0)
                    print(f"tracker {tid:08X}: gyro bias {s.bias.round(2)} deg/s (yaw drift is counted from now)")
                am = a.mean(axis=0)
                pitch = math.degrees(math.atan2(-am[0], math.hypot(am[1], am[2])))
                roll = math.degrees(math.atan2(am[1], am[2]))
                rate = len(s.accel) / (now - next_print + 0.5)
                print(f"{tid:08X}  {rate:5.0f}/s  lost {s.lost:4d}  gravity {np.linalg.norm(am):.3f} g  "
                      f"pitch {pitch:6.1f}  roll {roll:6.1f}  gyro {np.linalg.norm(g.mean(axis=0)):6.2f} deg/s  "
                      f"{'still' if still else 'moving'}  yaw(gyro only) {s.yaw:7.1f}")
                s.accel, s.gyro = [], []
            next_print = now + 0.5


if __name__ == "__main__":
    main()
