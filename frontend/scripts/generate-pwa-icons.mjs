/**
 * Generates the Manovia PWA icons.
 *
 * Everything here is drawn from scratch — there is no source image, no icon
 * font and no third-party artwork. The mark is three concentric rings around a
 * solid centre: the breath expanding and settling, which is what the breathing
 * exercise draws and what the whole app is named for. It is rendered
 * analytically (a signed-distance field per shape, supersampled 3x3) and
 * encoded as PNG by hand, so the repository carries a ~40 kB script instead of
 * an image-editing dependency and the artwork can be regenerated, reviewed and
 * diffed as code.
 *
 * Run: npm run icons
 */

import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(HERE, "../public/icons");

/** Warm-paper mark on the sage accent, matching the palette in index.css. */
const BACKGROUND_TOP = [0x2e, 0x6a, 0x4e];
const BACKGROUND_BOTTOM = [0x1f, 0x4d, 0x38];
const MARK = [0xfa, 0xf6, 0xef];

const SS = 3; // supersampling factor per axis
const RING_RADII = [0.36, 0.28, 0.2]; // fraction of the icon size
const RING_WIDTH = 0.055;
const CENTRE_RADIUS = 0.085;

/** Coverage of a ring of radius `r` and stroke `w`, centred at (cx, cy). */
function ringCoverage(dx, dy, r, w) {
  const d = Math.hypot(dx, dy);
  return clamp(w / 2 - Math.abs(d - r) + 0.5, 0, 1);
}

/** Coverage of a filled disc. */
function discCoverage(dx, dy, r) {
  return clamp(r - Math.hypot(dx, dy) + 0.5, 0, 1);
}

/** Coverage of a rounded rectangle (used for the icon's own plate). */
function roundedSquareCoverage(x, y, size, radiusRatio) {
  const half = size / 2;
  const r = Math.min(size * radiusRatio, half);
  const ax = Math.abs(x - half);
  const ay = Math.abs(y - half);
  const dx = ax - (half - r);
  const dy = ay - (half - r);
  if (dx <= 0 || dy <= 0) {
    return ax <= half && ay <= half ? 1 : 0;
  }
  return clamp(r - Math.hypot(dx, dy) + 0.5, 0, 1);
}

function clamp(value, min, max) {
  return value < min ? min : value > max ? max : value;
}

/**
 * Render one icon.
 *
 * @param {number} size edge length in pixels
 * @param {object} [options]
 * @param {boolean} [options.maskable] full-bleed plate with a smaller mark
 *   (Android applies its own mask, so the mark must sit inside the safe zone)
 */
function renderIcon(size, { maskable = false } = {}) {
  const pixels = Buffer.alloc(size * size * 4);
  const plateRatio = 0.22;
  const markScale = maskable ? 0.62 : 1;

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      let bgAlpha = 0;
      let markAlpha = 0;

      for (let sy = 0; sy < SS; sy += 1) {
        for (let sx = 0; sx < SS; sx += 1) {
          const px = x + (sx + 0.5) / SS;
          const py = y + (sy + 0.5) / SS;
          const plate = maskable ? 1 : roundedSquareCoverage(px, py, size, plateRatio);
          bgAlpha += plate;

          const cx = size / 2;
          const cy = size / 2;
          const dx = (px - cx) / markScale;
          const dy = (py - cy) / markScale;
          let coverage = 0;
          for (const radius of RING_RADII) {
            coverage = Math.max(
              coverage,
              ringCoverage(dx, dy, size * radius, size * RING_WIDTH),
            );
          }
          coverage = Math.max(coverage, discCoverage(dx, dy, size * CENTRE_RADIUS));
          markAlpha += coverage * plate;
        }
      }

      const samples = SS * SS;
      const bg = bgAlpha / samples;
      const mark = markAlpha / samples;
      const t = y / Math.max(1, size - 1);

      const offset = (y * size + x) * 4;
      for (let channel = 0; channel < 3; channel += 1) {
        const backdrop =
          BACKGROUND_TOP[channel] * (1 - t) + BACKGROUND_BOTTOM[channel] * t;
        pixels[offset + channel] = Math.round(backdrop * (1 - mark) + MARK[channel] * mark);
      }
      pixels[offset + 3] = Math.round(255 * bg);
    }
  }

  return encodePng(size, size, pixels);
}

/** CRC-32 (PNG chunk checksum) with the standard reflected table. */
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
  let c = 0xffffffff;
  for (const byte of buffer) {
    c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([length, body, crc]);
}

/** Minimal 8-bit RGBA PNG encoder. One IDAT, filter type 0 per scanline. */
function encodePng(width, height, rgba) {
  const raw = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y += 1) {
    raw[y * (width * 4 + 1)] = 0;
    rgba.copy(raw, y * (width * 4 + 1) + 1, y * width * 4, (y + 1) * width * 4);
  }

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // colour type: truecolour with alpha
  ihdr[10] = 0; // deflate
  ihdr[11] = 0; // adaptive filtering
  ihdr[12] = 0; // no interlace

  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const TARGETS = [
  { file: "pwa-192x192.png", size: 192, maskable: false },
  { file: "pwa-512x512.png", size: 512, maskable: false },
  { file: "pwa-512x512-maskable.png", size: 512, maskable: true },
  { file: "apple-touch-icon.png", size: 180, maskable: false },
];

mkdirSync(OUT_DIR, { recursive: true });
for (const target of TARGETS) {
  const png = renderIcon(target.size, { maskable: target.maskable });
  writeFileSync(resolve(OUT_DIR, target.file), png);
  console.log(`${target.file}  ${target.size}x${target.size}  ${(png.length / 1024).toFixed(1)} kB`);
}
