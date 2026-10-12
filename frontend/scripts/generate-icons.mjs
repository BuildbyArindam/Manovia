/**
 * Generates the Manovia PWA icons (Day 25).
 *
 * Why a generator instead of committed bitmaps from a design tool: the mark is
 * the one already in `public/favicon.svg` (a leaf — pointed top, two cubic
 * sides, a semicircular base — with a smaller leaf cut out of it), and an icon
 * that drifts away from the favicon is worse than no icon. Rasterising the same
 * shape in code keeps the two in step, keeps the repository free of an
 * untraceable binary, and makes every size reproducible with one command:
 *
 *   node scripts/generate-icons.mjs
 *
 * There is no third-party artwork here and no image dependency: the shape is
 * rasterised analytically (signed-distance style inside/outside test over the
 * two cubic curves and the circular base) at 4x4 supersampling, and the PNG is
 * written with `node:zlib` — no sharp, no canvas, no network.
 *
 * Outputs (all under `public/icons/`):
 *   icon-192.png, icon-512.png   — purpose "any" (rounded-square background)
 *   maskable-512.png             — purpose "maskable" (full-bleed background)
 *   apple-touch-icon-180.png     — iOS home screen (opaque, full-bleed)
 */

import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(HERE, "..", "public", "icons");

/** Brand colours, from the design tokens in `src/index.css`. */
const GREEN = [0x2e, 0x6a, 0x4e]; // --manovia-accent-bg
const MINT = [0xe4, 0xef, 0xe8]; // --manovia-accent-soft

/** Supersampling factor per axis; 4 is plenty for a 512px icon. */
const SS = 4;

/**
 * One leaf, in the 32x32 coordinate space of `favicon.svg`.
 *
 * The shape: a point at (16, topY), two symmetric cubic curves out to
 * (16 +- dx, baseY), then a semicircle of radius `r` centred on (16, baseY).
 * `c1`/`c2` are the control points of the right-hand cubic, relative to the
 * top point — exactly the numbers in the favicon's `d` attribute.
 */
function makeLeaf(topY, baseY, r, c1, c2) {
  const p0 = { x: 16, y: topY };
  const p1 = { x: 16 + c1[0], y: topY + c1[1] };
  const p2 = { x: 16 + c2[0], y: topY + c2[1] };
  const p3 = { x: 16 + (baseY - topY === 0 ? 0 : c2[0] + (r - c2[0])), y: baseY };
  // The cubic ends where the semicircle begins: (16 + r, baseY).
  p3.x = 16 + r;

  const bezier = (t, a, b, c, d) => {
    const u = 1 - t;
    return u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * d;
  };

  /** y(t) is monotonic increasing on [0,1], so a bisection finds t for a y. */
  function tForY(y) {
    let lo = 0;
    let hi = 1;
    for (let i = 0; i < 40; i += 1) {
      const mid = (lo + hi) / 2;
      if (bezier(mid, p0.y, p1.y, p2.y, p3.y) < y) {
        lo = mid;
      } else {
        hi = mid;
      }
    }
    return (lo + hi) / 2;
  }

  return function inside(x, y) {
    if (y < topY || y > baseY + r) {
      return false;
    }
    if (y >= baseY) {
      const dx = x - 16;
      const dy = y - baseY;
      return dx * dx + dy * dy <= r * r;
    }
    const t = tForY(y);
    const edge = bezier(t, p0.x, p1.x, p2.x, p3.x);
    return Math.abs(x - 16) <= edge - 16;
  };
}

/** The outer leaf, and the smaller leaf cut out of it (the favicon's two paths). */
const OUTER = makeLeaf(7, 16, 5.6, [3.6, 3.2], [5.6, 6.1]);
const INNER = makeLeaf(12.5, 17.3, 2.9, [1.9, 1.9], [2.9, 3.4]);

/** Inside a rounded rectangle of `radius` (in 0..1 units), 0 = square. */
function insideRoundedRect(px, py, radius) {
  if (radius <= 0) {
    return true;
  }
  const r = radius;
  const x = Math.min(px, 1 - px);
  const y = Math.min(py, 1 - py);
  if (x >= r || y >= r) {
    return true;
  }
  const dx = r - x;
  const dy = r - y;
  return dx * dx + dy * dy <= r * r;
}

/**
 * Render one icon.
 *
 * `markScale` shrinks the mark about the centre (maskable icons need the mark
 * inside the platform's 80% safe circle); `corner` is the background radius.
 */
function renderIcon(size, { markScale = 1, corner = 0.25 } = {}) {
  const rgba = Buffer.alloc(size * size * 4);
  const total = SS * SS;

  for (let py = 0; py < size; py += 1) {
    for (let px = 0; px < size; px += 1) {
      let bgHits = 0;
      let outerHits = 0;
      let innerHits = 0;

      for (let sy = 0; sy < SS; sy += 1) {
        for (let sx = 0; sx < SS; sx += 1) {
          // Sample centre of each sub-pixel.
          const fx = (px + (sx + 0.5) / SS) / size;
          const fy = (py + (sy + 0.5) / SS) / size;
          if (!insideRoundedRect(fx, fy, corner)) {
            continue;
          }
          bgHits += 1;

          // Map into the 32-unit mark space, scaled about the centre.
          const mx = 16 + ((fx - 0.5) * 32) / markScale;
          const my = 16 + ((fy - 0.5) * 32) / markScale;
          if (OUTER(mx, my)) {
            if (INNER(mx, my)) {
              innerHits += 1;
            } else {
              outerHits += 1;
            }
          }
        }
      }

      if (bgHits === 0) {
        continue; // transparent outside the rounded square
      }

      // Composite mark over background: inner (green) inside outer (mint) on green.
      const greenWeight = bgHits - outerHits; // background shows through
      const mintWeight = outerHits - innerHits;
      const innerWeight = innerHits;

      const mix = (channel) =>
        (GREEN[channel] * greenWeight + MINT[channel] * mintWeight + GREEN[channel] * innerWeight) /
        (greenWeight + mintWeight + innerWeight);

      const alpha = bgHits / total;
      const offset = (py * size + px) * 4;
      // Un-premultiply: canvas colour over the background's own alpha.
      rgba[offset] = Math.round(mix(0));
      rgba[offset + 1] = Math.round(mix(1));
      rgba[offset + 2] = Math.round(mix(2));
      rgba[offset + 3] = Math.round(alpha * 255);
    }
  }

  return rgba;
}

/** CRC32 (PNG chunk checksums). */
const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c;
  }
  return table;
})();

function crc32(buffer) {
  let c = -1;
  for (let i = 0; i < buffer.length; i += 1) {
    c = CRC_TABLE[(c ^ buffer[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ -1) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([length, body, crc]);
}

/** Encode 8-bit RGBA as a PNG (filter type 0 on every scanline). */
function encodePng(size, rgba) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // colour type: truecolour + alpha
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;

  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y += 1) {
    const lineStart = y * (size * 4 + 1);
    raw[lineStart] = 0; // filter: none
    rgba.copy(raw, lineStart + 1, y * size * 4, (y + 1) * size * 4);
  }

  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const TARGETS = [
  { file: "icon-192.png", size: 192, options: {} },
  { file: "icon-512.png", size: 512, options: {} },
  // Maskable: full-bleed background, mark inside the platform's safe circle.
  { file: "maskable-512.png", size: 512, options: { corner: 0, markScale: 0.82 } },
  // iOS: opaque, no transparency, no radius (Apple masks it itself).
  { file: "apple-touch-icon-180.png", size: 180, options: { corner: 0 } },
];

mkdirSync(OUT_DIR, { recursive: true });
for (const target of TARGETS) {
  const png = encodePng(target.size, renderIcon(target.size, target.options));
  writeFileSync(join(OUT_DIR, target.file), png);
  console.log(`${target.file}\t${target.size}x${target.size}\t${png.length} bytes`);
}
