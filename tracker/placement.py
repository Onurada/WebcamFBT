"""Camera placement help (the setup assistant).

Room mode: from the SteamVR play area and/or a walk along the room's walls with the headset on,
recommend where 1-3 webcams should stand and where they should look, and map how well every
spot of the room is covered. Spot mode: score one camera's current view of the player.

Everything here is plain geometry on the floor plane (x, z in SteamVR standing space, metres;
-z is the play area's front), so it can be tested without cameras or a headset.
"""

import math

import numpy as np

# MediaPipe BlazePose landmark indices used here
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 2, 5, 7, 8
L_SH, R_SH, L_HIP, R_HIP = 11, 12, 23, 24
L_ANK, R_ANK, L_HEEL, R_HEEL, L_TOE, R_TOE = 27, 28, 29, 30, 31, 32
FEET = (L_HEEL, R_HEEL, L_TOE, R_TOE)

MOUNT_HEIGHT = 1.0  # m: camera lens at about waist height, looking level
HEAD_ROOM = 0.25  # m above the eyes that must stay in the picture
WALL_GAP = 0.15  # m: a camera stands at least this far inside the walls
BODY_HALF_WIDTH = 0.45  # m: arms/elbows sideways need this much room inside the picture
MIN_BODY_FRACTION = 0.45  # player smaller than this share of the picture height = too far for good tracking

# Where cameras go around the play area, as angles from its front (0 = in front, + = to the right).
LAYOUTS = {1: (0.0,), 2: (-45.0, 45.0), 3: (-60.0, 60.0, 180.0)}


def vertical_fov(hfov_deg, aspect=4 / 3):
    return 2 * math.atan(math.tan(math.radians(hfov_deg) / 2) / aspect)


def min_distance(eye_height, hfov_deg, aspect=4 / 3, mount=MOUNT_HEIGHT):
    """Closest a standing player may get to a level camera and still be seen head to toe."""
    t = math.tan(vertical_fov(hfov_deg, aspect) / 2)
    return max(eye_height + HEAD_ROOM - mount, mount) / t


def max_distance(eye_height, hfov_deg, aspect=4 / 3):
    """Farthest the player may be before they become too small in the picture to track well."""
    t = math.tan(vertical_fov(hfov_deg, aspect) / 2)
    return (eye_height + HEAD_ROOM) / (2 * t * MIN_BODY_FRACTION)


def direction(angle_deg):
    """Unit vector on the floor for an angle measured from the front (-z), clockwise seen from above."""
    a = math.radians(angle_deg)
    return np.array([math.sin(a), -math.cos(a)])


def heading(vec):
    """Inverse of direction(): angle in degrees of a floor vector."""
    return math.degrees(math.atan2(vec[0], -vec[1]))


def reach(half_w, half_d, v):
    """Distance from a rectangle's centre to its edge along unit vector v."""
    tx = half_w / abs(v[0]) if abs(v[0]) > 1e-9 else math.inf
    tz = half_d / abs(v[1]) if abs(v[1]) > 1e-9 else math.inf
    return min(tx, tz)


def point_in_polygon(p, poly):
    x, z = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, z1 = poly[i]
        x2, z2 = poly[(i + 1) % n]
        if (z1 > z) != (z2 > z) and x < (x2 - x1) * (z - z1) / (z2 - z1) + x1:
            inside = not inside
    return inside


def distance_to_polygon_edge(p, poly):
    best = math.inf
    p = np.asarray(p, dtype=float)
    for i in range(len(poly)):
        a, b = np.asarray(poly[i], float), np.asarray(poly[(i + 1) % len(poly)], float)
        ab = b - a
        t = 0.0 if not ab.any() else float(np.clip(np.dot(p - a, ab) / np.dot(ab, ab), 0, 1))
        best = min(best, float(np.linalg.norm(p - (a + t * ab))))
    return best


def simplify_walk(points, step=0.25):
    """Keeps walk samples at least `step` metres apart (the outline of the room)."""
    out = []
    for p in points:
        if not out or math.dist(out[-1], p) >= step:
            out.append((float(p[0]), float(p[1])))
    return out


def sees_full_body(cam_pos, cam_facing_deg, point, hfov_deg, d_min, d_max):
    """True if a camera at cam_pos looking along cam_facing sees a player standing at point head to toe."""
    v = np.asarray(point, float) - np.asarray(cam_pos, float)
    dist = float(np.linalg.norm(v))
    if dist < d_min or dist > d_max:
        return False
    off = abs((heading(v) - cam_facing_deg + 180) % 360 - 180)
    margin = math.degrees(math.asin(min(1.0, BODY_HALF_WIDTH / dist)))
    return off <= hfov_deg / 2 - margin


def recommend(n, hfov_deg, eye_height, play_area=None, room=None):
    """Recommended camera spots.

    play_area: (cx, cz, width, depth) rectangle the player moves in (SteamVR play area), or None.
    room: outline of the walls as [(x, z), ...] from walking the room, or None.
    Returns (spots, area) where spots = [{x, z, facing, distance, angle, too_close}] (facing = the
    direction the camera must look, in degrees; angle = where around the area it stands) and
    area = the play rectangle actually used (shrunk to fit the room when needed)."""
    n = max(1, min(3, int(n)))
    d_min = min_distance(eye_height, hfov_deg)
    if play_area is None and room:
        poly = np.asarray(room, float)
        cx, cz = poly.mean(axis=0)
        play_area = (float(cx), float(cz), 2.0, 2.0)
    if play_area is None:
        play_area = (0.0, 0.0, 2.0, 2.0)
    cx, cz, w, d = play_area
    center = np.array([cx, cz])

    def place(half_w, half_d):
        spots = []
        for angle in LAYOUTS[n]:
            v = direction(angle)
            r = reach(half_w, half_d, v) + d_min + 0.1
            pos = center + v * r
            too_close = False
            if room and not point_in_polygon(pos, room):
                # Slide towards the centre until the camera is inside the room, off the wall.
                for k in np.linspace(r, 0.5, 60):
                    pos = center + v * k
                    if point_in_polygon(pos, room) and distance_to_polygon_edge(pos, room) >= WALL_GAP:
                        break
                too_close = float(np.linalg.norm(pos - center)) < reach(half_w, half_d, v) + d_min
            spots.append({"x": float(pos[0]), "z": float(pos[1]), "facing": heading(center - pos),
                          "distance": float(np.linalg.norm(pos - center)), "angle": angle, "too_close": too_close})
        return spots

    half_w, half_d = w / 2, d / 2
    spots = place(half_w, half_d)
    # In a small room, shrink the play area (down to 0.6 x 0.6 m) until every camera fits.
    while room and any(s["too_close"] for s in spots) and min(half_w, half_d) > 0.3:
        half_w, half_d = max(0.3, half_w - 0.1), max(0.3, half_d - 0.1)
        spots = place(half_w, half_d)
    return spots, (cx, cz, half_w * 2, half_d * 2)


def coverage(cameras, hfov_deg, eye_height, bounds, cell=0.2):
    """How many cameras see a player head to toe at each cell of bounds = (x0, z0, x1, z1).
    cameras: [{x, z, facing}]. Returns {x0, z0, cell, cols, rows, counts: [row-major ints]}."""
    d_min, d_max = min_distance(eye_height, hfov_deg), max_distance(eye_height, hfov_deg)
    x0, z0, x1, z1 = bounds
    cols = max(1, int(math.ceil((x1 - x0) / cell)))
    rows = max(1, int(math.ceil((z1 - z0) / cell)))
    counts = []
    for r in range(rows):
        for c in range(cols):
            p = (x0 + (c + 0.5) * cell, z0 + (r + 0.5) * cell)
            counts.append(sum(sees_full_body((cam["x"], cam["z"]), cam["facing"], p, hfov_deg, d_min, d_max)
                              for cam in cameras))
    return {"x0": x0, "z0": z0, "cell": cell, "cols": cols, "rows": rows, "counts": counts}


def map_bounds(play_area, room, spots, pad=0.5):
    xs, zs = [], []
    if play_area:
        cx, cz, w, d = play_area
        xs += [cx - w / 2, cx + w / 2]
        zs += [cz - d / 2, cz + d / 2]
    for x, z in room or []:
        xs.append(x)
        zs.append(z)
    for s in spots:
        xs.append(s["x"])
        zs.append(s["z"])
    if not xs:
        return (-2.0, -2.0, 2.0, 2.0)
    return (min(xs) - pad, min(zs) - pad, max(xs) + pad, max(zs) + pad)


def estimate_camera(cam_forward, player_pos, eye_span, eye_height, hfov_deg, aspect=4 / 3):
    """Where a calibrated camera roughly stands, from how tall the player looks in its picture.

    cam_forward: floor direction the camera looks (from its calibration), player_pos: player's floor
    position, eye_span: eyes-to-heels height in the picture as a share of its height (0..1).
    Returns {x, z, facing, distance} or None. Assumes the given field of view."""
    if eye_span <= 0.05:
        return None
    f = 0.5 / math.tan(vertical_fov(hfov_deg, aspect) / 2)
    dist = f * eye_height / eye_span
    v = np.asarray(cam_forward, float)
    if np.linalg.norm(v) < 1e-6:
        return None
    v = v / np.linalg.norm(v)
    pos = np.asarray(player_pos, float) - v * dist
    return {"x": float(pos[0]), "z": float(pos[1]), "facing": heading(v), "distance": float(dist)}


def spot_check(points, vis, brightness, fps, tilt_deg):
    """Scores one camera's view of the player for the spot check.

    points: 33x2 normalized image coordinates (x right, y down, 0..1) or None if nobody is seen,
    vis: 33 visibilities, brightness: mean frame brightness 0..255, fps: camera frames per second,
    tilt_deg: how far the camera looks down (+) or up (-) from level.
    Returns (score 0..100, [hint keys]) with the most important hints first."""
    hints = []
    score = 100.0
    if brightness < 60:
        hints.append("spot_dark")
        score -= 25
    elif brightness < 90:
        hints.append("spot_dim")
        score -= 10
    if fps and fps < 18:
        hints.append("spot_slow")
        score -= 10
    if points is None:
        return max(0, int(score) - 60), ["spot_no_person"] + hints
    pts = np.asarray(points, float)
    vis = np.asarray(vis, float)
    head_y = min(pts[i][1] for i in (NOSE, L_EYE, R_EYE))
    feet_seen = [i for i in FEET if vis[i] > 0.5 and pts[i][1] < 0.98]
    feet_y = max(pts[i][1] for i in FEET)
    issues = []
    if len(feet_seen) < 2 or feet_y > 0.97:
        issues.append(("spot_feet_cut", 35))
    if head_y < 0.04:  # works without the head (the shoulders anchor the body); with it, a bit better
        shoulders_y = min(pts[L_SH][1], pts[R_SH][1])
        issues.append(("spot_head_cut", 5) if shoulders_y > 0.03 and min(vis[L_SH], vis[R_SH]) > 0.5
                      else ("spot_shoulders_cut", 30))
    body = feet_y - head_y
    if body > 0.93 and not issues:
        issues.append(("spot_too_close", 20))
    elif body < 0.45:
        issues.append(("spot_too_far", 20 if body > 0.3 else 35))
    xs = pts[[L_SH, R_SH, L_HIP, R_HIP], 0]
    center_x = float(xs.mean())
    if center_x < 0.3 or center_x > 0.7:
        issues.append(("spot_off_center", 15))
    elif center_x < 0.38 or center_x > 0.62:
        issues.append(("spot_side_room", 5))
    if tilt_deg > 25:
        issues.append(("spot_too_high", 10))
    elif tilt_deg < -15:
        issues.append(("spot_too_low", 10))
    for key, cost in issues:
        hints.append(key)
        score -= cost
    return int(max(0, min(100, score))), sorted(hints, key=ORDER.index)


ORDER = ["spot_no_person", "spot_feet_cut", "spot_shoulders_cut", "spot_head_cut", "spot_too_close", "spot_too_far", "spot_off_center",
         "spot_too_high", "spot_too_low", "spot_dark", "spot_dim", "spot_slow", "spot_side_room"]
