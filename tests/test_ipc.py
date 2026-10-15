"""Engine <-> app protocol test. Needs the webcam (someone in view for 'person'), not SteamVR.
Run:  .venv\\Scripts\\python.exe tests\\test_ipc.py
"""

import collections
import json
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

p = subprocess.Popen([PY, "-u", os.path.join(ROOT, "tracker", "webcam_fbt.py"), "--ipc", "--no-vr"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
counts, last, errors = collections.Counter(), {}, []


def read_events():
    for line in p.stdout:
        m = json.loads(line)
        counts[m["type"]] += 1
        last[m["type"]] = m


threading.Thread(target=read_events, daemon=True).start()
threading.Thread(target=lambda: errors.extend(l for l in p.stderr if "Traceback" in l), daemon=True).start()


def send(cmd):
    p.stdin.write(json.dumps(cmd) + "\n")
    p.stdin.flush()


t = time.time()
while "status" not in last and time.time() - t < 30:
    time.sleep(0.1)
assert "status" in last, "engine sent no status within 30 s"
print(f"first status after {time.time() - t:.1f} s:", last["status"])

send({"cmd": "preview", "value": False})
time.sleep(0.5)
before = counts["frame"]
time.sleep(1.5)
assert counts["frame"] == before, "frames kept coming with preview off"
send({"cmd": "preview", "value": True})
send({"cmd": "set", "key": "language", "value": "en"})
time.sleep(1)
assert counts["frame"] > before, "no frames after preview on"

send({"cmd": "quit"})
p.wait(10)
assert p.returncode == 0, f"exit code {p.returncode}"
assert not errors, errors
print("ok", dict(counts))
sys.exit(0)
