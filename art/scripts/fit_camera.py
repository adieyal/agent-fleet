"""Fit the prototype's orthographic camera to l2.png from landmarks.

    python3 art/scripts/fit_camera.py

Each landmark pairs a point in the workbench scene (Blender coordinates, metres) with where it sits in
l2.png (pixels, 1672 x 941). For every pitch and yaw on a grid the best scale and offset follow by linear
least squares; the pose with the smallest residual is printed as the page's VIEW constant.
"""
import numpy as np

W, H = 1672, 941
LANDMARKS = [  # (scene point, l2 pixel, weight); the bench is the subject, so it counts most
    ((3.8, 3.95, 0.74), (385, 551), 3),     # bench: near-left corner of the desk top
    ((9.2, 3.95, 0.74), (1305, 712), 3),    # bench: near-right corner
    ((5.16, 5.9, 2.78), (765, 131), 1),     # plan wall: frame top-left
    ((8.08, 5.9, 2.78), (1211, 258), 1),    # plan wall: frame top-right
    ((8.08, 5.9, 0.76), (1213, 531), 1),    # plan wall: frame bottom-right
    ((3.45, 5.53, 1.95), (520, 140), 1),    # lantern
    ((3.0, 5.22, 0.755), (418, 238), 1),    # question desk: near-left corner of its top
]


def axes(pitch: float, yaw: float):
    p, y = np.radians(pitch), np.radians(yaw)
    back = np.array([np.sin(y) * np.cos(p), -np.cos(y) * np.cos(p), np.sin(p)])  # target -> camera
    fwd = -back
    right = np.cross(fwd, [0, 0, 1])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    return right, up


def fit(pitch: float, yaw: float):
    right, up = axes(pitch, yaw)
    P = np.array([p for p, _, _ in LANDMARKS], float)
    S = np.array([s for _, s, _ in LANDMARKS], float)
    wt = np.repeat(np.sqrt([w for _, _, w in LANDMARKS]), 2)
    # x = ox + s * (P.right);  y = oy - s * (P.up)
    A = np.zeros((2 * len(P), 3))
    b = np.zeros(2 * len(P))
    A[0::2, 0], A[0::2, 2], b[0::2] = P @ right, 1, S[:, 0]
    A[1::2, 0], A[1::2, 1], b[1::2] = -(P @ up), 1, S[:, 1]
    (s, oy, ox), *_ = np.linalg.lstsq(A * wt[:, None], b * wt, rcond=None)
    err = np.sqrt(np.mean(((A @ np.array([s, oy, ox]) - b) * wt) ** 2))
    return err, s, ox, oy, right, up


# l0 (the building, L0): the building's size is unknown too, so a landmark is (x, y, z) in units of its width, depth
# and storey height (front-left corner of the North slab's top edge next to the spine at x = 0, the lift at x = 1;
# floors counted up from the lobby), with the l0 pixel. l0 is painted, not projected: its slabs descend 3-5 deg
# (more lower down) and its roof sides rise 12-20 deg, so the slab lines weigh most and the roof least.
L0_LANDMARKS = [
    ((0, 0, 6), (440, 193), 3), ((1, 0, 6), (1147, 232), 3),    # North slab top, spine end and lift end
    ((0, 0, 5), (440, 293), 3), ((1, 0, 5), (1147, 336), 3),    # Atlas
    ((0, 0, 4), (440, 389), 3), ((1, 0, 4), (1147, 445), 3),    # Lab
    ((0, 0, 3), (440, 477), 3), ((1, 0, 3), (1147, 540), 3),    # Harbor
    ((0, 0, 2), (440, 568), 3), ((1, 0, 2), (1147, 635), 3),    # Delta
    ((0, 1, 7), (655, 25), 1), ((1, 1, 7), (1270, 100), 1),     # roof: back-left and back-right corners
    ((0, 0, 7), (420, 108), 1), ((1, 0, 7), (1140, 128), 1),    # roof: front corners
]


def fit_l0(pitch: float, yaw: float):
    """x = ox + a x (e_x.right) + b y (e_y.right) + c z (e_z.right), likewise y with -up: a, b, c are the width, depth
    and storey height in pixels, linear given the pose."""
    right, up = axes(pitch, yaw)
    P = np.array([p for p, _, _ in L0_LANDMARKS], float)
    S = np.array([s for _, s, _ in L0_LANDMARKS], float)
    wt = np.repeat(np.sqrt([w for _, _, w in L0_LANDMARKS]), 2)
    A = np.zeros((2 * len(P), 5))
    b = np.zeros(2 * len(P))
    for k in range(3):
        A[0::2, k] = P[:, k] * right[k]
        A[1::2, k] = -P[:, k] * up[k]
    A[0::2, 3], A[1::2, 4] = 1, 1
    b[0::2], b[1::2] = S[:, 0], S[:, 1]
    sol, *_ = np.linalg.lstsq(A * wt[:, None], b * wt, rcond=None)
    err = np.sqrt(np.mean(((A @ sol - b) * wt) ** 2))
    return err, sol


def main_l0() -> None:
    err, sol, pitch, yaw = min((fit_l0(p, y) + (p, y) for p in np.arange(2, 60, 0.25) for y in np.arange(0, 45, 0.25)),
                               key=lambda r: r[0])
    width, depth, storey = sol[:3]
    s = storey / 4.0   # px per metre, with a 4 m storey
    print(f'rms error {err:.1f} px; pitch {pitch:.2f}, yaw {yaw:.2f}')
    print(f'storey {storey:.1f} px; with a 4 m storey: {s:.2f} px/m, width {width / s:.1f} m, depth {depth / s:.1f} m')


def main() -> None:
    import sys
    if '--l0' in sys.argv:
        return main_l0()
    best = min((fit(p, y) + (p, y) for p in np.arange(20, 55, 0.25) for y in np.arange(5, 40, 0.25)),
               key=lambda r: r[0])
    err, s, ox, oy, right, up, pitch, yaw = best
    # the target is the scene point that lands in the middle of the frame
    target = right * (W / 2 - ox) / s + up * (oy - H / 2) / s
    print(f'rms error {err:.1f} px; pitch {pitch:.2f}, yaw {yaw:.2f}, height {H / s:.3f} m')
    print(f"const VIEW = {{ target: [{target[0]:.3f}, {target[1]:.3f}, {target[2]:.3f}], "
          f"pitch: {pitch:.2f}, yaw: {yaw:.2f}, height: {H / s:.3f} }};")


if __name__ == '__main__':
    main()
