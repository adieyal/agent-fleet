"""Measure the concept images' projection and test candidate cameras against them (docs/design/art-direction.md,
"Camera").

    uv run --with opencv-python-headless --with scipy python art/scripts/measure_projection.py            # measure
    blender -b --factory-startup -P art/scripts/shoot_projection.py                                      # proxies
    uv run --with opencv-python-headless --with scipy python art/scripts/measure_projection.py --overlays OUT_DIR

Every number is size-free unless it says otherwise: line directions come from LSD segments grouped by screen angle
into the two ground axes (X descends to the right, Y rises to the right) and verticals. For each image it reports
the parallel fit (one direction per axis), the level two-point perspective fit (both vanishing points on one
horizontal horizon, verticals parallel) and the parallel projection equivalent at the image centre. Circles check
foreshortening independently: ground circles (a table top, pot soil) give tan(depression) as minor/major, and wall
circles tell an orthographic rotation (verticals x cos(pitch)) from an oblique projection (verticals full length).
The landmark fits (pixel picks, per-axis sizes free) feed shoot_projection.py's proxies; they depend on the picks,
so they are secondary evidence. Writes art/build/projection/measure.json.
"""
from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates
from scipy.optimize import least_squares

REPO = Path(__file__).resolve().parents[2]
CONCEPTS = REPO / 'docs' / 'images' / 'concept'
BUILD = REPO / 'art' / 'build' / 'projection'
W, H = 1672, 941
# screen-angle windows (degrees, up positive) of the X and Y line families; verticals are 80..100
FAMILIES = {'l0': ((-20, -5), (8, 22)), 'l1': ((-22, -8), (36, 54)), 'lobby': ((-22, -8), (34, 54)),
            'l2': ((-28, -12), (26, 46)), 'no vacancies': ((-20, -3), (8, 24))}
MIN_LEN = 60
RECOMMENDED = ('oblique', 30.0, math.degrees(math.atan(0.5)))
CANDIDATES = {'recommended': RECOMMENDED, 'world 28/33': ('rotation', 33.0, 28.0),
              'building 9/25.75': ('rotation', 25.75, 9.0)}


# --- line segments ----------------------------------------------------------------------------------------------

_segs: dict[str, np.ndarray] = {}


def segments(name: str) -> np.ndarray:
    """LSD segments longer than 50 px as rows (x1, y1, x2, y2, angle, length), left end first, angle up positive."""
    if name not in _segs:
        g = cv2.cvtColor(cv2.imread(str(CONCEPTS / f'{name}.png')), cv2.COLOR_BGR2GRAY)
        s = cv2.createLineSegmentDetector(cv2.LSD_REFINE_ADV).detect(g)[0].reshape(-1, 4)
        flip = s[:, 2] < s[:, 0]
        s[flip] = s[flip][:, [2, 3, 0, 1]]
        ang = np.degrees(np.arctan2(-(s[:, 3] - s[:, 1]), s[:, 2] - s[:, 0]))
        L = np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1])
        _segs[name] = np.c_[s, ang, L][L > 50]
    return _segs[name]


def family(name: str, lo: float, hi: float, vertical: bool = False) -> np.ndarray:
    a = segments(name).copy()
    if vertical:
        a[:, 4] = np.where(a[:, 4] < 0, a[:, 4] + 180, a[:, 4])
    return a[(a[:, 4] >= lo) & (a[:, 4] <= hi) & (a[:, 5] >= MIN_LEN)]


def wrap(d):
    return (d + 90) % 180 - 90


def wrms(r, w) -> float:
    return float(np.sqrt(np.average(np.asarray(r) ** 2, weights=w)))


def toward(f: np.ndarray, vp) -> np.ndarray:
    """Angle between each segment and the line from its midpoint to the vanishing point."""
    mid = np.c_[(f[:, 0] + f[:, 2]) / 2, (f[:, 1] + f[:, 3]) / 2]
    seg = np.c_[f[:, 2] - f[:, 0], f[:, 3] - f[:, 1]]
    d = np.asarray(vp)[None, :] - mid
    return wrap(np.degrees(np.arctan2(seg[:, 0] * d[:, 1] - seg[:, 1] * d[:, 0], (seg * d).sum(1))))


# --- projections ----------------------------------------------------------------------------------------------------

def matrix(kind: str, yaw: float, angle: float) -> np.ndarray:
    """2x3: pixels (right, down) per unit along world X, Y, Z. `angle` is the pitch (rotation) or depression
    (oblique); an oblique projection keeps verticals full length, a rotation shortens them by cos(pitch)."""
    y, a = math.radians(yaw), math.radians(angle)
    s, cz = (math.tan(a), 1.0) if kind == 'oblique' else (math.sin(a), math.cos(a))
    return np.array([[math.cos(y), math.sin(y), 0.0], [math.sin(y) * s, -math.cos(y) * s, -cz]])


def line_rms(name: str, M: np.ndarray) -> dict:
    """Weighted RMS angle between the image's X and Y segments and M's X and Y screen directions."""
    out, tot, wt = {}, [], []
    for lab, (lo, hi), col in zip('XY', FAMILIES[name], (0, 1)):
        f = family(name, lo, hi)
        pred = math.degrees(math.atan2(-M[1, col], M[0, col]))
        r = wrap(f[:, 4] - pred)
        out[lab] = (pred, wrms(r, f[:, 5]))
        tot += list(r)
        wt += list(f[:, 5])
    out['all'] = wrms(tot, wt)
    return out


def best_parallel(names: list[str]) -> tuple[float, float, float]:
    """(rms, yaw, depression) of the parallel projection whose ground directions best fit these images' lines."""
    def cost(yaw, d):
        M = matrix('oblique', yaw, d)
        return math.sqrt(np.mean([line_rms(n, M)['all'] ** 2 for n in names]))
    return min((cost(y, d), y, d) for y in np.arange(10, 60, 0.25) for d in np.arange(4, 40, 0.25))


# --- per-image line analysis ---------------------------------------------------------------------------------------

def analyse(name: str) -> dict:
    (xl0, xh0), (yl0, yh0) = FAMILIES[name]
    X, Y, Z = family(name, xl0, xh0), family(name, yl0, yh0), family(name, 80, 100, vertical=True)
    wx, wy = np.sqrt(X[:, 5]), np.sqrt(Y[:, 5])
    par = least_squares(lambda q: np.r_[wrap(X[:, 4] - q[0]) * wx, wrap(Y[:, 4] - q[1]) * wy],
                        [np.mean(FAMILIES[name][0]), np.mean(FAMILIES[name][1])]).x
    per = least_squares(lambda q: np.r_[toward(X, (q[0], q[2])) * wx, toward(Y, (q[1], q[2])) * wy],
                        [-4000, 6000, -800], x_scale=[1000, 1000, 300])
    xl, xr, h = per.x
    J = per.jac
    sd = np.sqrt(np.diag(np.linalg.pinv(J.T @ J) * (per.fun ** 2).sum() / (len(per.fun) - 3)))
    cx = W / 2
    f = math.sqrt((cx - xl) * (xr - cx))
    tx, ty = (H / 2 - h) / (cx - xl), (H / 2 - h) / (xr - cx)
    thirds = {}
    for lab, fam in (('X', X), ('Y', Y)):
        my = (fam[:, 1] + fam[:, 3]) / 2
        thirds[lab] = [float(np.average(fam[m, 4], weights=fam[m, 5])) if (m := (my >= y0) & (my < y0 + H / 3)).sum() > 2
                       else None for y0 in (0, H / 3, 2 * H / 3)]
    return {
        'segments': [len(X), len(Y), len(Z)],
        'parallel': {'X': par[0], 'Y': par[1], 'rms': wrms(np.r_[wrap(X[:, 4] - par[0]), wrap(Y[:, 4] - par[1])],
                                                           np.r_[X[:, 5], Y[:, 5]])},
        'perspective': {'vp_x': [xl, h], 'vp_y': [xr, h], 'sd': sd.tolist(), 'f_px': f,
                        'rms': wrms(np.r_[toward(X, (xl, h)), toward(Y, (xr, h))], np.r_[X[:, 5], Y[:, 5]]),
                        'depression_top_mid_bottom': [math.degrees(math.atan((v - h) / f)) for v in (0, H / 2, H)]},
        'verticals': {'mean': float(np.average(Z[:, 4], weights=Z[:, 5])),
                      'rms': wrms(Z[:, 4] - np.average(Z[:, 4], weights=Z[:, 5]), Z[:, 5])},
        'thirds': thirds,
        'centre': {'X': -math.degrees(math.atan(tx)), 'Y': math.degrees(math.atan(ty)),
                   'yaw': math.degrees(math.atan(math.sqrt(tx / ty))), 'ground': math.sqrt(tx * ty)},
    }


def local(res: dict, u: float, v: float) -> tuple[float, float]:
    """(yaw, ground factor) of the parallel projection equivalent to the perspective fit at pixel (u, v)."""
    (xl, h), (xr, _) = res['perspective']['vp_x'], res['perspective']['vp_y']
    tx, ty = (v - h) / (u - xl), (v - h) / (xr - u)
    return math.atan(math.sqrt(tx / ty)), math.sqrt(tx * ty)


# --- circles -------------------------------------------------------------------------------------------------------

def gray(name: str) -> np.ndarray:
    return cv2.cvtColor(cv2.imread(str(CONCEPTS / f'{name}.png')), cv2.COLOR_BGR2GRAY).astype(float)


def rim_points(name: str, c, rmin: float, rmax: float, n: int = 120) -> np.ndarray:
    """Strongest radial edge along rays from c, to a quarter pixel."""
    g = gaussian_filter(gray(name), 0.8)
    pts = []
    for t in np.linspace(0, 2 * np.pi, n, endpoint=False):
        r = np.arange(rmin, rmax, 0.25)
        v = map_coordinates(g, [c[1] + r * np.sin(t), c[0] + r * np.cos(t)], order=1)
        dv = np.abs(np.gradient(v))
        i = int(np.argmax(dv))
        if 0 < i < len(r) - 1:
            a, b, e = dv[i - 1], dv[i], dv[i + 1]
            rr = rmin + (i + 0.5 * (a - e) / (a - 2 * b + e)) * 0.25
            pts.append((c[0] + rr * np.cos(t), c[1] + rr * np.sin(t)))
    return np.array(pts, np.float32)


def ellipse(pts: np.ndarray) -> dict:
    """Fit with two rounds of outlier rejection; extents, axis ratio and the major axis' tilt (image, y down)."""
    keep = np.ones(len(pts), bool)
    for _ in range(3):
        (cx, cy), (A, B), ang = cv2.fitEllipse(pts[keep])
        th = math.radians(ang)
        q = (pts - [cx, cy]) @ np.array([[math.cos(th), math.sin(th)], [-math.sin(th), math.cos(th)]]).T
        res = (np.sqrt((q[:, 0] / (A / 2)) ** 2 + (q[:, 1] / (B / 2)) ** 2) - 1) * (A + B) / 4
        keep = np.abs(res) < max(1.0, 2.5 * np.std(res[keep]))
    hx = math.hypot(A / 2 * math.cos(th), B / 2 * math.sin(th))
    hy = math.hypot(A / 2 * math.sin(th), B / 2 * math.cos(th))
    tilt = wrap(ang if A >= B else ang - 90)
    return {'centre': (cx, cy), 'h_over_w': hy / hx, 'ratio': min(A, B) / max(A, B), 'tilt': float(tilt),
            'rms': float(np.std(res[keep]))}


def wall_circle(yaw: float, ground: float, kind: str) -> dict:
    """Predicted shape of a circle on a wall along world X, given the local yaw and ground factor."""
    cz = math.sqrt(1 - ground ** 2) if kind == 'rotation' else 1.0
    M = np.array([[math.cos(yaw), 0], [math.sin(yaw) * ground, -cz]])
    U, S, _ = np.linalg.svd(M)
    return {'h_over_w': float(np.hypot(*M[1]) / np.hypot(*M[0])), 'ratio': float(S[1] / S[0]),
            'tilt': float(wrap(math.degrees(math.atan2(U[1, 0], U[0, 0]))))}


def axis_ellipse(pts) -> dict:
    """Ellipse with a horizontal major axis (any ground circle's image) from rim picks."""
    x, y = np.asarray(pts, float).T
    a, b, c, d = np.linalg.lstsq(np.c_[x ** 2, y ** 2, x, y], np.ones(len(x)), rcond=None)[0]
    cx, cy = -c / (2 * a), -d / (2 * b)
    k = 1 + a * cx ** 2 + b * cy ** 2
    rx, ry = math.sqrt(k / a), math.sqrt(k / b)
    res = (np.sqrt(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2) - 1) * (rx + ry) / 2
    return {'centre': (cx, cy), 'ratio': ry / rx, 'rms': float(np.std(res))}


# lobby's round side table: its top rim picked by eye at 5x (the lamp hides the back of the rim)
SIDE_TABLE = [(1418.6, 595.0), (1424.0, 588.0), (1472.4, 593.0), (1466.0, 587.0), (1458.0, 583.4), (1430.0, 603.0),
              (1445.6, 605.0), (1460.0, 602.6), (1469.0, 598.0)]
L2_WALL_LIGHTS = [(869, 129.3), (922.1, 144.4), (975.4, 160.0)]   # lit discs on the plan wall
L0_RINGS = [(375, 178), (375, 354), (375, 443), (375, 628)]      # progress rings on the spine's front face
POTS = {  # boxes around a pot's soil; kept only if the soil blob's major axis is horizontal (a ground circle)
    'l0': [(1090, 690, 1128, 710), (1102, 180, 1140, 200)],
    'l2': [(580, 218, 630, 240), (655, 312, 700, 340)],
    'lobby': [(1580, 486, 1628, 512)],
    'l1': [(88, 438, 128, 466)],
}


def soil(name: str, box) -> dict | None:
    im = cv2.imread(str(CONCEPTS / f'{name}.png')).astype(int)
    x0, y0, x1, y1 = box
    c = im[y0:y1, x0:x1]
    m = (c.max(2) < 80) & (c[:, :, 2] >= c[:, :, 1] - 4)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8))
    if n < 2:
        return None
    ys, xs = np.nonzero(lab == 1 + np.argmax(st[1:, cv2.CC_STAT_AREA]))
    ev, evec = np.linalg.eigh(np.cov(np.c_[xs, ys].T))
    return {'ratio': float(math.sqrt(ev[0] / ev[1])), 'width': float(4 * math.sqrt(ev[1])),
            'tilt': float(wrap(math.degrees(math.atan2(evec[1, 1], evec[0, 1])))), 'area': len(xs)}


def circles(res: dict) -> dict:
    out = {'side_table': axis_ellipse(SIDE_TABLE)}
    for key, name, cs, rr in (('l2_wall_lights', 'l2', L2_WALL_LIGHTS, (10, 21)), ('l0_rings', 'l0', L0_RINGS, (8, 26))):
        rows = []
        for c in cs:
            e = ellipse(rim_points(name, c, *rr))
            yaw, ground = local(res[name], *e['centre'])
            rows.append({**e, 'rotation': wall_circle(yaw, ground, 'rotation'),
                         'oblique': wall_circle(yaw, ground, 'oblique')})
        out[key] = rows
    out['pots'] = {n: [s | {'box': b} for b in boxes if (s := soil(n, b)) and abs(s['tilt']) < 12]
                   for n, boxes in POTS.items()}
    return out


# --- landmark fits for the proxies ---------------------------------------------------------------------------------

# (label, unit coordinates (numbers, or names of free coordinates), pixel)
LANDMARKS = {
    'l0': [  # x: slab widths (0 at the spine's right face), y: depths (0 front), z: storeys (North slab top 6, roof 7)
        ('North slab front-left', (0, 0, 6), (440, 193)), ('North slab front-right', (1, 0, 6), (962, 272)),
        ('Atlas slab front-left', (0, 0, 5), (440, 293)), ('Atlas slab front-right', (1, 0, 5), (961, 379)),
        ('Lab slab front-left', (0, 0, 4), (440, 389)), ('Lab slab front-right', ('xg', 0, 4), (939, 499)),
        ('Harbor slab front-left', (0, 0, 3), (440, 477)), ('Harbor slab front-right', ('xg', 0, 3), (939, 583)),
        ('Delta slab front-left', (0, 0, 2), (440, 568)), ('Delta slab front-right', ('xg', 0, 2), (939, 686)),
        ('North slab side meets lift', (1, 'yl', 6), (1148, 232)), ('Atlas slab side meets lift', (1, 'yl', 5), (1147, 336)),
        ('Harbor slab side meets lift', (1, 'yl', 3), (1147, 540)), ('Delta slab side meets lift', (1, 'yl', 2), (1147, 635)),
        ('spine top front-left', ('xs', 0, 7), (266, 95)), ('roof back-left', ('xs', 1, 7), (645, 25)),
        ('roof back-right', ('xr', 1, 7), (1272, 100)),
        ('plinth left corner', ('px0', 'py0', 'pz'), (40, 681)), ('plinth front corner', ('px1', 'py0', 'pz'), (941, 889)),
        ('plinth right corner', ('px1', 'py1', 'pz'), (1640, 690)),
    ],
    'l1': [  # x: the plate's front-left edge, y: its right edge, z: wall height; plate top z = 0
        ('plate left corner', (0, 0, 0), (26, 507)), ('plate front corner', (1, 0, 0), (1289, 886)),
        ('plate right corner', (1, 1, 0), (1647, 458)), ('left wall, front top', (0, 0, 1), (32, 390)),
        ('back-left wall top', (0, 1, 1), (310, 73)),
    ],
    'l2': [  # the workbench scene's metres (fit_camera.py), per-axis scale free
        ('bench top near-left', (3.8, 3.95, 0.74), (382, 548)), ('bench top near-right', (9.2, 3.95, 0.74), (1307, 707)),
        ('calendar frame top-left', (5.16, 5.9, 2.78), (765, 131)), ('calendar frame top-right', (8.08, 5.9, 2.78), (1211, 258)),
        ('calendar frame bottom-right', (8.08, 5.9, 0.76), (1213, 531)),
        ('question desk near-left top', (3.0, 5.22, 0.755), (418, 238)),
    ],
}


def landmark_fit(name: str, M: np.ndarray) -> dict:
    """Per-axis sizes (px per unit), origin and free coordinates by linear least squares, given the projection."""
    lms = LANDMARKS[name]
    free = sorted({c for _, P, _ in lms for c in P if isinstance(c, str)})
    axis_of = {c: k for _, P, _ in lms for k, c in enumerate(P) if isinstance(c, str)}
    A, b = [], []
    for _, P, q in lms:
        for r in range(2):
            row = np.zeros(5 + len(free))
            for k, c in enumerate(P):
                if isinstance(c, str):
                    row[5 + free.index(c)] = M[r, k]
                else:
                    row[k] = c * M[r, k]
            row[3 + r] = 1
            A.append(row)
            b.append(q[r])
    A, b = np.array(A), np.array(b)
    sol = np.linalg.lstsq(A, b, rcond=None)[0]
    per = np.hypot(*(A @ sol - b).reshape(-1, 2).T)
    return {'scale': sol[:3].tolist(), 'origin': sol[3:5].tolist(),
            'free': {c: sol[5 + i] / sol[axis_of[c]] for i, c in enumerate(free)},
            'rms': float(np.sqrt(np.mean(per ** 2))), 'per': per.tolist()}


def fits() -> dict:
    out = {}
    for name in LANDMARKS:
        _, yaw, d = best_parallel([name])
        cands = dict(CANDIDATES) | {f'{name} own best': ('oblique', float(yaw), float(d))}
        out[name] = {'landmarks': LANDMARKS[name], 'fits': {}}
        for cn, (kind, yaw, ang) in cands.items():
            M = matrix(kind, yaw, ang)
            out[name]['fits'][cn] = {'kind': kind, 'yaw': yaw, 'angle': ang, 'M': M.tolist(),
                                     **landmark_fit(name, M), 'lines': line_rms(name, M)}
    return out


# --- overlays ------------------------------------------------------------------------------------------------------

def project(f: dict, P) -> np.ndarray:
    v = [f['free'][c] if isinstance(c, str) else c for c in P]
    return np.array(f['origin']) + np.array(f['M']) @ (np.array(v) * np.array(f['scale']))


def overlays(out_dir: Path) -> None:
    """Each proxy render at 50% over its concept, with the picks (green) joined to the fit's projection (red)."""
    R = json.loads((BUILD / 'measure.json').read_text())['fits']
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, r in R.items():
        for cn, f in r['fits'].items():
            tag = f"{name}_{cn.split()[0]}"
            base = cv2.imread(str(CONCEPTS / f'{name}.png')).astype(float)
            ren = cv2.imread(str(BUILD / 'render' / f'{tag}.png'), cv2.IMREAD_UNCHANGED).astype(float)
            a = ren[:, :, 3:4] / 255 * 0.5
            img = (base * (1 - a) + ren[:, :, :3] * a).clip(0, 255).astype(np.uint8)
            for _, P, q in r['landmarks']:
                p = tuple(map(int, project(f, P)))
                cv2.line(img, tuple(map(int, q)), p, (0, 0, 255), 2)
                cv2.circle(img, tuple(map(int, q)), 6, (0, 170, 0), 2)
                cv2.drawMarker(img, p, (0, 0, 255), cv2.MARKER_CROSS, 12, 2)
            kind = (f"oblique yaw {f['yaw']:.2f}, depression {f['angle']:.2f}" if f['kind'] == 'oblique'
                    else f"orthographic yaw {f['yaw']:.2f}, pitch {f['angle']:.2f}")
            ln = f['lines']
            cv2.rectangle(img, (0, 0), (W, 64), (255, 255, 255), -1)
            for i, t in enumerate((f'{name}: {cn} ({kind})', f"landmark rms {f['rms']:.0f} px   line-angle rms "
                                   f"{ln['all']:.1f} deg (X {ln['X'][1]:.1f}, Y {ln['Y'][1]:.1f})")):
                cv2.putText(img, t, (12, 26 + 28 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (20, 20, 20), 2)
            cv2.imwrite(str(out_dir / f'overlay-{tag}.png'), img)


def shadows(out_dir: Path) -> dict:
    """shoot_projection.py's shadow scene: the canonical camera's render against the orthographic render stretched
    by 1/cos(d), and the ground shadow against the analytic one (sun rays projected with the canonical matrix)."""
    info = json.loads((BUILD / 'shadow.json').read_text())
    a = cv2.imread(str(BUILD / 'shadow_oblique.png')).astype(float)
    b = cv2.resize(cv2.imread(str(BUILD / 'shadow_ortho.png')), (a.shape[1], a.shape[0]),
                   interpolation=cv2.INTER_LINEAR).astype(float)
    d = np.abs(a - b).max(2)
    M = matrix(*RECOMMENDED) * info['px_per_m']
    c, tgt, sun = np.array(info['centre']), np.array(info['target']), np.array(info['sun'])
    proj = lambda P: c + M @ (np.array(P, float) - tgt)  # noqa: E731
    shape = a.shape[:2]
    cast = np.zeros(shape, np.uint8)
    for lo, hi in info['boxes']:
        pts = [proj(np.array(P) + np.array(P)[2] / -sun[2] * sun) for P in itertools.product(*zip(lo, hi))]
        cv2.fillPoly(cast, [cv2.convexHull(np.array(pts, np.float32)).astype(np.int32)], 1)
    plane = np.zeros(shape, np.uint8)
    e = info['ground_half'] - 0.2
    cv2.fillPoly(plane, [cv2.convexHull(np.array([proj((x, y, 0)) for x in (-e, e) for y in (-e, e)],
                                                  np.float32)).astype(np.int32)], 1)
    hsv = cv2.cvtColor(a.astype(np.uint8), cv2.COLOR_BGR2HSV)
    ground = ~((hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 60)) & (plane > 0)
    dark, pred = ground & (hsv[:, :, 2] < 90), ground & (cast > 0)
    vis = a.astype(np.uint8)
    cv2.drawContours(vis, cv2.findContours(cast, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0], -1, (255, 0, 255), 1)
    side = np.vstack([np.full((34, 2 * a.shape[1] + 8, 3), 255, np.uint8),
                      np.hstack([b.astype(np.uint8), np.full((a.shape[0], 8, 3), 255, np.uint8), vis])])
    cv2.putText(side, 'orthographic, pitch 26.57, image stretched 1/cos', (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, 0, 1)
    cv2.putText(side, 'canonical_camera; magenta = analytic shadow', (a.shape[1] + 18, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, 0, 1)
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / 'shadow-proof.png'), side)
    return {'mean_abs_diff': float(d.mean()), 'p99_diff': float(np.percentile(d, 99)),
            'shadow_iou': float((dark & pred).sum() / (dark | pred).sum()),
            'rendered_outside_analytic_px': int((dark & ~pred).sum()), 'analytic_not_dark_px': int((pred & ~dark).sum())}


# --- report --------------------------------------------------------------------------------------------------------

def main() -> None:
    if '--overlays' in sys.argv:
        out = Path(sys.argv[sys.argv.index('--overlays') + 1])
        overlays(out)
        print(json.dumps(shadows(out), indent=1))
        return
    res = {n: analyse(n) for n in FAMILIES}
    for n, r in res.items():
        p, c, v = r['perspective'], r['centre'], r['verticals']
        dep = p['depression_top_mid_bottom']
        print(f"{n:13s} X/Y/Z segments {r['segments']}; parallel rms {r['parallel']['rms']:.2f} deg | level two-point "
              f"perspective rms {p['rms']:.2f} deg, VPx {p['vp_x'][0]:.0f}, VPy {p['vp_y'][0]:.0f}, horizon "
              f"{p['vp_x'][1]:.0f} (sd {p['sd'][2]:.0f}), f {p['f_px']:.0f} px, depression {dep[0]:.1f}/{dep[1]:.1f}/"
              f"{dep[2]:.1f} top/mid/bottom | centre: X {c['X']:+.1f}, Y {c['Y']:+.1f} -> yaw {c['yaw']:.1f}, "
              f"ground {c['ground']:.3f} (oblique depression {math.degrees(math.atan(c['ground'])):.1f}, rotation "
              f"pitch {math.degrees(math.asin(c['ground'])):.1f}) | verticals {v['mean']:.2f} rms {v['rms']:.2f} | "
              f"X by thirds {['%+.1f' % t if t is not None else '-' for t in r['thirds']['X']]}, "
              f"Y {['%+.1f' % t if t is not None else '-' for t in r['thirds']['Y']]}")
    circ = circles(res)
    st = circ['side_table']
    print(f"lobby side table (ground circle): minor/major {st['ratio']:.3f} -> depression "
          f"{math.degrees(math.atan(st['ratio'])):.1f}, fit rms {st['rms']:.2f} px")
    for key in ('l2_wall_lights', 'l0_rings'):
        for e in circ[key]:
            print(f"{key} at ({e['centre'][0]:.0f},{e['centre'][1]:.0f}): h/w {e['h_over_w']:.3f}, tilt {e['tilt']:+.1f} | "
                  f"rotation predicts {e['rotation']['h_over_w']:.3f}, {e['rotation']['tilt']:+.1f}; oblique predicts "
                  f"{e['oblique']['h_over_w']:.3f}, {e['oblique']['tilt']:+.1f}")
    for n, rows in circ['pots'].items():
        for s in rows:
            print(f"pot soil {n} {s['box']}: minor/major {s['ratio']:.3f} -> depression "
                  f"{math.degrees(math.atan(s['ratio'])):.1f} (width {s['width']:.0f} px, tilt {s['tilt']:+.1f})")
    groups = {}
    for names in (['l1', 'lobby', 'l2'], ['l0', 'no vacancies'], list(FAMILIES)):
        r, yaw, d = best_parallel(names)
        groups['+'.join(names)] = {'rms': r, 'yaw': yaw, 'depression': d}
        print(f"best parallel for {'+'.join(names)}: yaw {yaw:.2f}, depression {d:.2f} (tan {math.tan(math.radians(d)):.3f}), rms {r:.2f} deg")
    F = fits()
    for n, r in F.items():
        for cn, f in r['fits'].items():
            print(f"{n} {cn:18s} {f['kind']:8s} yaw {f['yaw']:5.2f} angle {f['angle']:5.2f} | landmark rms {f['rms']:5.1f} px | "
                  f"line rms {f['lines']['all']:5.2f} deg (X {f['lines']['X'][1]:.2f}, Y {f['lines']['Y'][1]:.2f})")
    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / 'measure.json').write_text(json.dumps({'images': res, 'circles': circ, 'groups': groups, 'fits': F},
                                                   indent=1, default=float))


if __name__ == '__main__':
    main()
