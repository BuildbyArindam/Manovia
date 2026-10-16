/**
 * Parsing raw heart-rate data into RR intervals (Day 42).
 *
 * Two sources feed the same pipeline:
 *
 * 1. **BLE Heart Rate Measurement** packets (GATT characteristic 0x2A37), as
 *    sent by chest straps and watches in broadcast mode. The packet carries
 *    the instantaneous heart rate and, when the sensor reports them, one or
 *    more beat-to-beat (RR) intervals in 1/1024 s units — see `parseHeartRateMeasurement`.
 * 2. **CSV imports** (no hardware path), parsed by `import/csvImport.ts`.
 *
 * Everything here is pure: bytes in, structured values out. The Web Bluetooth
 * plumbing lives in `ble/heartRateDevice.ts` and is tested against fakes.
 */

import type { RRSample } from "./types";

/** Bluetooth SIG "Heart Rate Measurement" characteristic. */
export const HEART_RATE_MEASUREMENT_UUID = "heart_rate_measurement";
/** Bluetooth SIG "Heart Rate" service. */
export const HEART_RATE_SERVICE_UUID = "heart_rate";

export type SensorContact = "detected" | "not-detected" | "unsupported";

export interface HeartRateMeasurement {
  /** Instantaneous heart rate in bpm (UINT8 or UINT16 per the flags). */
  bpm: number;
  /** Sensor contact status, when the sensor reports it. */
  contact: SensorContact;
  /** RR intervals in ms (converted from 1/1024 s units), in packet order. */
  rrIntervalsMs: number[];
  /** Cumulative energy expended in kilo-Joules, when present. */
  energyExpendedKj: number | null;
}

export class MalformedPacket extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MalformedPacket";
  }
}

/**
 * Parse one Heart Rate Measurement packet (Bluetooth GATT spec):
 *
 *   byte 0      flags
 *     bit 0     0 => heart rate is UINT8, 1 => UINT16 (little-endian)
 *     bit 1     sensor contact status   (meaningful when bit 2 is set)
 *     bit 2     sensor contact supported
 *     bit 3     energy expended present (then a UINT16 follows the HR value)
 *     bit 4     RR-interval present     (then N x UINT16 follow, 1/1024 s)
 *     bits 5-7  reserved
 *   next 1/2    heart rate value
 *   next 0/2    energy expended
 *   next 0..2N  RR intervals, 1/1024 s each, little-endian
 *
 * Trailing garbage is ignored (some stacks pad), but a packet that ends inside
 * a field is `MalformedPacket` — a half-read RR interval is worse than none.
 */
export function parseHeartRateMeasurement(bytes: Uint8Array): HeartRateMeasurement {
  if (bytes.length < 2) {
    throw new MalformedPacket(`heart-rate packet too short (${bytes.length} bytes)`);
  }
  const flags = bytes[0] ?? 0;
  const hrIsUint16 = (flags & 0x01) !== 0;
  const contactSupported = (flags & 0x04) !== 0;
  const contactDetected = (flags & 0x02) !== 0;
  const energyPresent = (flags & 0x08) !== 0;
  const rrPresent = (flags & 0x10) !== 0;

  let offset = 1;
  if (hrIsUint16) {
    if (bytes.length < offset + 2) {
      throw new MalformedPacket("packet ends inside the UINT16 heart-rate value");
    }
    // The DataView-free path keeps this usable on plain Uint8Arrays in tests.
    const bpm = (bytes[offset] ?? 0) | ((bytes[offset + 1] ?? 0) << 8);
    if (bpm === 0) {
      throw new MalformedPacket("heart-rate value is zero");
    }
    offset += 2;
    const rest = parseTail(bytes, offset, energyPresent, rrPresent);
    return {
      bpm,
      contact: contactStatus(contactSupported, contactDetected),
      ...rest,
    };
  }
  const bpm = bytes[offset] ?? 0;
  if (bpm === 0) {
    throw new MalformedPacket("heart-rate value is zero");
  }
  offset += 1;
  const rest = parseTail(bytes, offset, energyPresent, rrPresent);
  return {
    bpm,
    contact: contactStatus(contactSupported, contactDetected),
    ...rest,
  };
}

function contactStatus(supported: boolean, detected: boolean): SensorContact {
  if (!supported) {
    return "unsupported";
  }
  return detected ? "detected" : "not-detected";
}

function parseTail(
  bytes: Uint8Array,
  offset: number,
  energyPresent: boolean,
  rrPresent: boolean,
): Pick<HeartRateMeasurement, "rrIntervalsMs" | "energyExpendedKj"> {
  let cursor = offset;
  let energyExpendedKj: number | null = null;
  if (energyPresent) {
    if (bytes.length < cursor + 2) {
      throw new MalformedPacket("packet ends inside the energy-expended field");
    }
    energyExpendedKj = (bytes[cursor] ?? 0) | ((bytes[cursor + 1] ?? 0) << 8);
    cursor += 2;
  }
  const rrIntervalsMs: number[] = [];
  if (rrPresent) {
    // Whatever remains after the fixed fields is RR pairs; odd trailing bytes
    // are a truncated pair and are refused rather than half-parsed.
    const remaining = bytes.length - cursor;
    if (remaining % 2 !== 0) {
      throw new MalformedPacket("packet ends inside an RR interval");
    }
    for (let i = 0; i < remaining; i += 2) {
      const raw = (bytes[cursor + i] ?? 0) | ((bytes[cursor + i + 1] ?? 0) << 8);
      // 1/1024 s -> ms. 1024 raw == exactly 1000 ms; keep the fraction so
      // sub-ms precision from the sensor is not silently thrown away.
      rrIntervalsMs.push((raw * 1000) / 1024);
    }
  }
  return { rrIntervalsMs, energyExpendedKj };
}

/**
 * Turn a sequence of RR interval lengths into timestamped samples.
 *
 * `startMs` is the time of the beat that *ends* the first interval; each later
 * interval ends one RR duration later. This is the fixture/CSV path; the live
 * BLE accumulator uses {@link RRStreamAccumulator} so packet loss is visible.
 */
export function rrSamplesFromIntervals(intervalsMs: readonly number[], startMs = 0): RRSample[] {
  const out: RRSample[] = [];
  let t = startMs;
  for (const rrMs of intervalsMs) {
    t += rrMs;
    out.push({ rrMs, tMs: t });
  }
  return out;
}

/**
 * Live accumulator for a BLE stream.
 *
 * RR intervals arrive in bursts, without timestamps. The accumulator gives
 * them one cumulative beat timeline (`tMs`), and watches **notification
 * silence**: if no packet arrives for more than `silenceToleranceMs`, the
 * notifications stopped (sensor out of range, radio drop, app backgrounded)
 * and beats were lost or invented across that span. The next interval is
 * flagged `breakBefore` so downstream windows and RMSSD pairs will not
 * straddle it. This is honest about what a packet stream can actually tell:
 * silence, not beat counting against a wall clock the sensor never shared.
 */
export class RRStreamAccumulator {
  private readonly samples: RRSample[] = [];
  private lastArrivalMs: number | null = null;
  private nextBeatMs = 0;
  private droppedSpans = 0;

  constructor(private readonly silenceToleranceMs = 3000) {}

  /**
   * Add the RR intervals of one packet. `arrivalMs` is `Date.now()` (or an
   * injected clock) when the packet was received. Returns the new samples.
   */
  push(rrIntervalsMs: readonly number[], arrivalMs: number): RRSample[] {
    let breakBefore = false;
    if (this.lastArrivalMs !== null) {
      const silence = arrivalMs - this.lastArrivalMs;
      if (silence > this.silenceToleranceMs) {
        this.droppedSpans += 1;
        breakBefore = true;
      }
    }
    this.lastArrivalMs = arrivalMs;

    const added: RRSample[] = [];
    for (const rrMs of rrIntervalsMs) {
      this.nextBeatMs += rrMs;
      const sample: RRSample =
        breakBefore && added.length === 0
          ? { rrMs, tMs: this.nextBeatMs, breakBefore: true }
          : { rrMs, tMs: this.nextBeatMs };
      this.samples.push(sample);
      added.push(sample);
    }
    return added;
  }

  /** The accumulated, timestamped series (a copy; push returns new samples). */
  series(): RRSample[] {
    return [...this.samples];
  }

  /** How many notification-silence spans were detected. */
  get dropouts(): number {
    return this.droppedSpans;
  }
}
