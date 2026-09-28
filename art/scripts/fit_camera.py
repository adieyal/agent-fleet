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


def main() -> None:
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
