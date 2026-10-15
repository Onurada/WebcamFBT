"""Tests for the setup assistant's geometry and spot scoring (no camera or headset needed).
Run:  .venv\\Scripts\\python.exe tests\\test_placement.py
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tracker"))
import placement as pl  # noqa: E402

EYE = 1.62
HFOV = 60.0


def test_distances_are_sensible():
    d_min, d_max = pl.min_distance(EYE, HFOV), pl.max_distance(EYE, HFOV)
    assert 2.0 < d_min < 2.6, d_min  # typical webcam needs ~2.3 m for head to toe
    assert d_max > d_min + 1.0
    assert pl.min_distance(EYE, 90.0) < d_min  # a wider lens can stand closer


def test_one_camera_in_front_looking_at_center():
    spots, area = pl.recommend(1, HFOV, EYE, play_area=(0.0, 0.0, 2.0, 2.0))
    s = spots[0]
    assert abs(s["x"]) < 1e-6 and s["z"] < -2.0  # in front (-z), beyond the play area
    assert abs(s["facing"] - 180.0) < 1e-6 or abs(s["facing"] + 180.0) < 1e-6  # looks back towards +z
    cov = pl.coverage(spots, HFOV, EYE, (-1.0, -1.0, 1.0, 1.0), cell=0.25)
    assert min(cov["counts"]) >= 1, "the whole play area must be seen head to toe"


def test_two_cameras_at_right_angles():
    spots, _ = pl.recommend(2, HFOV, EYE, play_area=(0.0, 0.0, 2.0, 2.0))
    a, b = (np.array([s["x"], s["z"]]) for s in spots)
    angle = math.degrees(math.acos(np.dot(a, b) / np.linalg.norm(a) / np.linalg.norm(b)))
    assert abs(angle - 90) < 1
    cov = pl.coverage(spots, HFOV, EYE, (-0.5, -0.5, 0.5, 0.5), cell=0.25)
    assert min(cov["counts"]) == 2, "the middle of the play area must be seen by both cameras"


def test_small_room_pulls_cameras_inside_and_shrinks_area():
    room = [(-2.0, -2.0), (2.0, -2.0), (2.0, 2.0), (-2.0, 2.0)]  # 4 x 4 m room
    spots, area = pl.recommend(2, HFOV, EYE, room=room)
    for s in spots:
        assert pl.point_in_polygon((s["x"], s["z"]), room)
        assert pl.distance_to_polygon_edge((s["x"], s["z"]), room) >= pl.WALL_GAP - 1e-6
    assert area[2] <= 2.0 and area[3] <= 2.0


def test_estimate_camera_position():
    # A camera 3 m in front of the player looking at them; the player's eyes-to-heels span then
    # fills f * eye_height / 3 of the picture.
    f = 0.5 / math.tan(pl.vertical_fov(HFOV) / 2)
    span = f * EYE / 3.0
    est = pl.estimate_camera(cam_forward=(0.0, 1.0), player_pos=(0.0, 0.0), eye_span=span, eye_height=EYE, hfov_deg=HFOV)
    assert abs(est["x"]) < 1e-6 and abs(est["z"] + 3.0) < 1e-6 and abs(est["distance"] - 3.0) < 1e-6


def body(top=0.1, bottom=0.9, cx=0.5):
    """33 normalized points of a standing person filling top..bottom of the picture."""
    pts = np.tile([cx, (top + bottom) / 2], (33, 1))
    for i in (pl.NOSE, pl.L_EYE, pl.R_EYE):
        pts[i] = [cx, top]
    for i in pl.FEET:
        pts[i] = [cx, bottom]
    for i, dx in ((pl.L_SH, 0.05), (pl.R_SH, -0.05), (pl.L_HIP, 0.04), (pl.R_HIP, -0.04)):
        pts[i] = [cx + dx, (top + bottom) / 2]
    return pts


def test_spot_scores():
    good, hints = pl.spot_check(body(), np.ones(33), 140, 25, 5)
    assert good >= 90 and hints == [], (good, hints)
    s, hints = pl.spot_check(body(top=0.2, bottom=1.0), np.ones(33), 140, 25, 5)
    assert "spot_feet_cut" in hints and s < good
    s, hints = pl.spot_check(body(top=0.4, bottom=0.65), np.ones(33), 140, 25, 5)
    assert "spot_too_far" in hints
    s, hints = pl.spot_check(body(cx=0.2), np.ones(33), 140, 25, 5)
    assert "spot_off_center" in hints
    s, hints = pl.spot_check(None, None, 40, 12, 0)
    assert hints[0] == "spot_no_person" and "spot_dark" in hints and s < 30


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} tests passed")
