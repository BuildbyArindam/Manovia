import { describe, expect, it } from "vitest";

import { MockAudioContext } from "@/test/mockAudioContext";

import { SoundscapeEngine, LIMITER, MAX_MASTER_GAIN } from "./engine";
import type { SoundscapeContext } from "./engine";
import { brightnessForEnergy, clampPace, paletteById } from "./palettes";

function makeEngine(paletteId: string, context = new MockAudioContext()): SoundscapeEngine {
  return new SoundscapeEngine(
    () => context as unknown as SoundscapeContext,
    paletteById(paletteId),
  );
}

function startedEngine(paletteId = "warm-dawn"): { engine: SoundscapeEngine; context: MockAudioContext } {
  const context = new MockAudioContext();
  const engine = makeEngine(paletteId, context);
  engine.start();
  return { engine, context };
}

describe("soundscape engine (mocked AudioContext)", () => {
  it("builds the full procedural node graph: pad chord, texture, breath LFOs, limiter", () => {
    const { engine } = startedEngine("soft-rain"); // 3-note chord
    const graph = engine.describeGraph();

    const count = (type: string): number =>
      graph.nodes.filter((node) => node.type === type).length;
    // 3-note chord + sub-octave + breath LFO + swell LFO = 6 oscillators.
    expect(count("OscillatorNode")).toBe(6);
    // master, pad-gain, noise-gain, lfo-amp, swell-amp = 5 gain stages.
    expect(count("GainNode")).toBe(5);
    expect(count("BiquadFilterNode")).toBe(2);
    expect(count("DynamicsCompressorNode")).toBe(1);
    expect(count("AudioBufferSourceNode")).toBe(1);

    const byId = new Map(graph.nodes.map((node) => [node.id, node]));
    const breathLfo = byId.get("breath-lfo");
    // 6 breaths per minute is 0.1 Hz: the pacer's tempo IS the LFO rate.
    expect(breathLfo?.params.frequency).toBeCloseTo(0.1, 5);
    const swellLfo = byId.get("swell-lfo");
    expect(swellLfo?.params.frequency).toBeCloseTo(0.05, 5);
    expect(byId.get("noise-filter")?.params.frequency).toBe(2000);
    expect(byId.get("pad-filter")?.params.frequency).toBeGreaterThan(0);

    // The signal path ends at the limiter, in front of the destination.
    expect(graph.edges).toContainEqual(["master", "limiter"]);
    expect(graph.edges).toContainEqual(["limiter", "destination"]);
    expect(graph.edges).toContainEqual(["pad-gain", "master"]);
    expect(graph.edges).toContainEqual(["noise-gain", "master"]);

    // Every pad oscillator must go THROUGH the lowpass filter. The first
    // wiring sent them straight to the pad gain and left the filter
    // hanging; the brightness control (palette, energy adaptation,
    // reduced stimulation) would have done nothing audible. (Day 45
    // verification regression.)
    const padOscillators = graph.nodes
      .filter((node) => node.type === "OscillatorNode" && node.id.startsWith("osc-"))
      .map((node) => node.id);
    expect(padOscillators).toEqual(
      expect.arrayContaining(["osc-1", "osc-2", "osc-3", "osc-sub"]),
    );
    for (const oscId of padOscillators) {
      expect(graph.edges).toContainEqual([oscId, "pad-filter"]);
    }
    expect(graph.edges).toContainEqual(["pad-filter", "pad-gain"]);
    // The breath LFO modulates the filter's FREQUENCY PARAMETER, and the
    // swell LFO modulates the pad gain PARAMETER — node-to-node
    // connections there would be audio summed into the signal, not
    // modulation.
    expect(graph.edges).toContainEqual(["breath-lfo", "lfo-amp"]);
    expect(graph.edges).toContainEqual(["lfo-amp", "pad-filter.frequency"]);
    expect(graph.edges).toContainEqual(["swell-lfo", "swell-amp"]);
    expect(graph.edges).toContainEqual(["swell-amp", "pad-gain.gain"]);
    expect(graph.edges).not.toContainEqual(["lfo-amp", "pad-filter"]);
    expect(graph.edges).not.toContainEqual(["swell-amp", "pad-gain"]);
    void engine.dispose();
  });

  it("enforces the hard volume limit: no slider value can exceed MAX_MASTER_GAIN", () => {
    const { engine } = startedEngine();
    // The ceiling is the slider position times the engine cap — never more.
    engine.setVolume(5); // hostile input from a buggy UI
    expect(engine.masterCeiling()).toBe(MAX_MASTER_GAIN);
    const graph = engine.describeGraph();
    expect(graph.master.ceiling).toBe(MAX_MASTER_GAIN);
    // After the fade-in ramp the gain equals the (capped) ceiling.
    expect(graph.master.currentGain).toBeLessThanOrEqual(MAX_MASTER_GAIN);
    // And the soft ceiling in front of the destination is a high-ratio
    // limiter, so even a runaway sum cannot push past it.
    expect(graph.limiter.ratio).toBe(LIMITER.ratio);
    expect(graph.limiter.thresholdDb).toBe(LIMITER.thresholdDb);

    engine.setVolume(0.5);
    expect(engine.masterCeiling()).toBeCloseTo(0.5 * MAX_MASTER_GAIN, 10);
    engine.setVolume(-1);
    expect(engine.masterCeiling()).toBe(0);
    void engine.dispose();
  });

  it("fades in on start and fades out on stop", () => {
    const { engine } = startedEngine();
    engine.setVolume(0.8);
    expect(engine.describeGraph().master.currentGain).toBeCloseTo(0.8 * MAX_MASTER_GAIN, 10);
    expect(engine.isRunning).toBe(true);

    engine.stop();
    expect(engine.isRunning).toBe(false);
    expect(engine.describeGraph().master.currentGain).toBe(0);
    void engine.dispose();
  });

  it("adapts slowly and stays bounded: pace drifts inside [4, 12] bpm", () => {
    const { engine } = startedEngine("deep-forest"); // resting pace 5.5 bpm
    engine.setPace(12); // the fastest the engine will ever honour
    let previous = engine.describeGraph().pace.actualBpm;
    for (let i = 0; i < 60; i += 1) {
      engine.tick();
      const actual = engine.describeGraph().pace.actualBpm;
      expect(actual).toBeGreaterThanOrEqual(4);
      expect(actual).toBeLessThanOrEqual(12);
      expect(actual).toBeGreaterThanOrEqual(previous); // monotone drift
      previous = actual;
    }
    // 60 half-second ticks = 30 s of drift: it has moved, not jumped.
    expect(previous).toBeGreaterThan(5.5);
    expect(previous).toBeLessThan(12);
    void engine.dispose();
  });

  it("clamps hostile pace targets to the closed range", () => {
    const { engine } = startedEngine();
    engine.setPace(99);
    expect(engine.describeGraph().pace.targetBpm).toBe(12);
    expect(clampPace(99)).toBe(12);
    engine.setPace(-3);
    expect(engine.describeGraph().pace.targetBpm).toBe(4);
    expect(clampPace(-3)).toBe(4);
    void engine.dispose();
  });

  it("brightness follows self-reported energy only while adaptation is on", () => {
    const { engine } = startedEngine("warm-dawn"); // cutoff range 600-1800
    const mid = (600 + 1800) / 2;
    engine.setEnergy("high"); // adaptation off: the mood does not move
    expect(engine.describeGraph().brightness.targetHz).toBeCloseTo(mid, 5);

    engine.setAdaptation(true);
    engine.setEnergy("high");
    expect(engine.describeGraph().brightness.targetHz).toBeCloseTo(
      brightnessForEnergy([600, 1800], "high"),
      5,
    );

    for (let i = 0; i < 100; i += 1) {
      engine.tick();
      const actual = engine.describeGraph().brightness.actualHz;
      expect(actual).toBeGreaterThanOrEqual(600);
      expect(actual).toBeLessThanOrEqual(1800);
    }
    void engine.dispose();
  });

  it("reduced-stimulation mode cuts the texture, the breath depth, and the drift rate", () => {
    const a = startedEngine("soft-rain");
    const b = startedEngine("soft-rain");
    const baseline = a.engine.describeGraph();
    const noise = baseline.nodes.find((node) => node.id === "noise-gain")?.params.level;
    const lfo = baseline.nodes.find((node) => node.id === "lfo-amp")?.params.depth;

    b.engine.setReducedStimulation(true);
    const reduced = b.engine.describeGraph();
    const reducedNoise = reduced.nodes.find((node) => node.id === "noise-gain")?.params.level;
    const reducedLfo = reduced.nodes.find((node) => node.id === "lfo-amp")?.params.depth;
    expect(reducedNoise).toBeCloseTo(Number(noise ?? 0) * 0.4, 10);
    expect(reducedLfo).toBeCloseTo(Number(lfo ?? 0) * 0.5, 10);

    // Same target, same number of ticks: the reduced engine drifts less.
    a.engine.setPace(12);
    b.engine.setPace(12);
    for (let i = 0; i < 20; i += 1) {
      a.engine.tick();
      b.engine.tick();
    }
    expect(a.engine.describeGraph().pace.actualBpm).toBeGreaterThan(
      b.engine.describeGraph().pace.actualBpm,
    );
    void a.engine.dispose();
    void b.engine.dispose();
  });

  it("keeps the texture procedural: a looping 2-second generated buffer, no assets", () => {
    const { engine, context } = startedEngine("night-calm");
    const noise = engine.describeGraph().nodes.find((node) => node.id === "noise-source");
    expect(noise?.params.loop).toBe(1);
    // Exactly one buffer was asked of the context: 2 s at 22.05 kHz.
    expect(context.buffers).toEqual([[2 * 22050, 22050]]);
    void engine.dispose();
  });
});
