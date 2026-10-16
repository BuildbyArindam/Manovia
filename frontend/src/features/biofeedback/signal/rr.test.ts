/**
 * RR parsing tests (Day 42): BLE Heart Rate Measurement packets, the fixture
 * series builder, and the live stream accumulator's dropout handling.
 */

import { describe, expect, it } from "vitest";

import {
  MalformedPacket,
  RRStreamAccumulator,
  parseHeartRateMeasurement,
  rrSamplesFromIntervals,
} from "./rr";

/** Build a little-endian packet. */
function packet(...bytes: number[]): Uint8Array {
  return Uint8Array.from(bytes);
}

describe("parseHeartRateMeasurement", () => {
  it("parses a UINT8 heart rate with no extras", () => {
    const m = parseHeartRateMeasurement(packet(0x00, 72));
    expect(m.bpm).toBe(72);
    expect(m.rrIntervalsMs).toEqual([]);
    expect(m.energyExpendedKj).toBeNull();
    expect(m.contact).toBe("unsupported");
  });

  it("parses a UINT16 heart rate (flag bit 0)", () => {
    const m = parseHeartRateMeasurement(packet(0x01, 0x2c, 0x01)); // 300 bpm
    expect(m.bpm).toBe(300);
  });

  it("reports sensor contact when supported (bits 1-2)", () => {
    expect(parseHeartRateMeasurement(packet(0x04, 70)).contact).toBe("not-detected");
    expect(parseHeartRateMeasurement(packet(0x06, 70)).contact).toBe("detected");
  });

  it("converts RR intervals from 1/1024 s to ms", () => {
    // flags RR present; HR 70; RR raw values 1024 (=1000 ms) and 819.2 (=800 ms)
    const m = parseHeartRateMeasurement(packet(0x10, 70, 0x00, 0x04, 0x00, 0x03));
    expect(m.rrIntervalsMs).toEqual([1000, 750]);
  });

  it("parses energy expended before the RR intervals", () => {
    // flags: energy + RR; HR 70; energy 5 kJ; one RR of 1024 -> 1000 ms
    const m = parseHeartRateMeasurement(packet(0x18, 70, 0x05, 0x00, 0x00, 0x04));
    expect(m.energyExpendedKj).toBe(5);
    expect(m.rrIntervalsMs).toEqual([1000]);
  });

  it("accepts several RR intervals in one packet", () => {
    const m = parseHeartRateMeasurement(
      packet(0x10, 70, 0x00, 0x04, 0x00, 0x04, 0x00, 0x04),
    );
    expect(m.rrIntervalsMs).toEqual([1000, 1000, 1000]);
  });

  it("refuses truncated packets instead of half-parsing them", () => {
    expect(() => parseHeartRateMeasurement(packet(0x00))).toThrow(MalformedPacket);
    expect(() => parseHeartRateMeasurement(packet(0x01, 0x2c))).toThrow(MalformedPacket);
    expect(() => parseHeartRateMeasurement(packet(0x10, 70, 0x00))).toThrow(MalformedPacket);
    expect(() => parseHeartRateMeasurement(packet(0x18, 70, 0x05))).toThrow(MalformedPacket);
  });

  it("refuses a zero heart-rate value", () => {
    expect(() => parseHeartRateMeasurement(packet(0x00, 0))).toThrow(MalformedPacket);
  });
});

describe("rrSamplesFromIntervals", () => {
  it("stamps each interval at the beat that ends it", () => {
    const samples = rrSamplesFromIntervals([800, 820, 780], 1000);
    expect(samples).toEqual([
      { rrMs: 800, tMs: 1800 },
      { rrMs: 820, tMs: 2620 },
      { rrMs: 780, tMs: 3400 },
    ]);
  });
});

describe("RRStreamAccumulator", () => {
  it("keeps one cumulative beat timeline across packets", () => {
    const acc = new RRStreamAccumulator();
    const a = acc.push([800, 800], 1_000);
    expect(a.map((s) => s.tMs)).toEqual([800, 1600]);
    const b = acc.push([800], 2_100);
    expect(b.map((s) => s.tMs)).toEqual([2400]);
    expect(acc.dropouts).toBe(0);
  });

  it("marks one break after a long notification silence", () => {
    const acc = new RRStreamAccumulator();
    acc.push([800, 800], 1_000);
    // Nothing arrives for 6 s (default tolerance 3 s): beats were lost.
    const b = acc.push([800], 7_000);
    expect(b[0]?.breakBefore).toBe(true);
    expect(acc.dropouts).toBe(1);
    // The timeline stays cumulative — only the break is marked.
    expect(b[0]?.tMs).toBe(2400);
    // The next packet on time is not marked again.
    const c = acc.push([800], 7_900);
    expect(c[0]?.breakBefore).toBeUndefined();
    expect(acc.dropouts).toBe(1);
  });

  it("does not flag the very first packet as a dropout", () => {
    const acc = new RRStreamAccumulator();
    const a = acc.push([800], 10_000);
    expect(a[0]?.breakBefore).toBeUndefined();
    expect(acc.dropouts).toBe(0);
  });

  it("does not flag a brief silence inside the tolerance", () => {
    const acc = new RRStreamAccumulator();
    acc.push([800], 1_000);
    const b = acc.push([800], 3_500); // 2.5 s silence < 3 s
    expect(b[0]?.breakBefore).toBeUndefined();
    expect(acc.dropouts).toBe(0);
  });
});
