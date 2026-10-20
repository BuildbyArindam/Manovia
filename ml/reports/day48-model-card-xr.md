# Model card — XR calm space + sensory

| | |
| --- | --- |
| Card version | 1.0 (2026-10-20) |
| Status | wellness feature — not medical device |
| Owner | Manovia maintainers |

## 1. What it is

2D canvas primary (no WebGL needed) + optional immersive-vr local/seated. 36-triangle WebGL renderer, procedural audio (3 sines), breath 4s/6s. Sensory profile (motion/sound/density/colours/text) via html attributes.

## 2. Evaluation

Frame time 2D fallback p50 0.004 ms (CPU draw calls, not FPS). Bundle 5.8 KB gzip, precache 674 KB. Comfort checklist in verification doc.

## 3. Limits

No headset/GPU/audio in sandbox; comfort claims unverified on device. 2D fallback is the supported path.

## 4. Kill switch

`xr=false` → 2D only; Enter VR hidden. Sensory profile remains.
