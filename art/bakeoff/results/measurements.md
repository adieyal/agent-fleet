Viewport 1672x941, device pixel ratio 1, frame rate uncapped; median of 3 runs of 4 s, each in a fresh browser.

- no GPU (SwiftShader): `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)`
- GPU (ANGLE GL): `ANGLE (Intel, Mesa Intel(R) Graphics (MTL), OpenGL 4.6)`

| Variant | Mode | Zoom | Frame ms (mean) | p95 | fps | Draw call ms | Assets KB | Code KB |
|---|---|---|---|---|---|---|---|---|
| A | no GPU (SwiftShader) | l2 | 173.36 | 197.8 | 5.8 | 3.135 | 4014 | 2256 |
| A | no GPU (SwiftShader) | floor | 133.52 | 156.3 | 7.5 | 2.734 | 4014 | 2256 |
| B1 | no GPU (SwiftShader) | l2 | 5.88 | 6.5 | 170.0 | 0.073 | 912 | 22 |
| B1 | no GPU (SwiftShader) | floor | 2.73 | 4 | 366.5 | 0.072 | 912 | 22 |
| B2 | no GPU (SwiftShader) | l2 | 4.25 | 4.7 | 235.5 | 0.073 | 502 | 22 |
| B2 | no GPU (SwiftShader) | floor | 2.59 | 3.9 | 385.5 | 0.083 | 502 | 22 |
| B2mix | no GPU (SwiftShader) | l2 | 10.38 | 13.8 | 96.3 | 0.201 | 305 | 22 |
| B2mix | no GPU (SwiftShader) | floor | 5.84 | 9.8 | 171.2 | 0.195 | 305 | 22 |
| A | GPU (ANGLE GL) | l2 | 7.21 | 13.8 | 138.7 | 6.095 | 4014 | 2256 |
| A | GPU (ANGLE GL) | floor | 6.6 | 14.4 | 151.4 | 5.657 | 4014 | 2256 |
| B1 | GPU (ANGLE GL) | l2 | 0.51 | 1.2 | 1973.0 | 0.098 | 912 | 22 |
| B1 | GPU (ANGLE GL) | floor | 0.37 | 0.9 | 2717.8 | 0.069 | 912 | 22 |
| B2 | GPU (ANGLE GL) | l2 | 3.09 | 7.4 | 323.5 | 0.504 | 502 | 22 |
| B2 | GPU (ANGLE GL) | floor | 1.68 | 4.1 | 593.5 | 0.31 | 502 | 22 |
| B2mix | GPU (ANGLE GL) | l2 | 3.69 | 2.4 | 271.3 | 0.221 | 305 | 22 |
| B2mix | GPU (ANGLE GL) | floor | 2.52 | 6.8 | 396.5 | 0.374 | 305 | 22 |
