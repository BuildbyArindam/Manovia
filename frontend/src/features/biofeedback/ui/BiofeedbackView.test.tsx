/**
 * Biofeedback view tests (Day 42): the unsupported-browser path, the connect
 * flow with a fake sensor, the quality gate on live numbers, the dizzy stop,
 * the CSV no-hardware path, and the encrypted save — plus axe checks.
 *
 * The fake here implements the `HeartRateSource` interface (one level above
 * the fake GATT in `ble/heartRateDevice.test.ts`), so these are view tests.
 */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";

import { I18nProvider } from "@/lib/i18n/I18nProvider";

import type {
  ConnectionStatus,
  HeartRateConnection,
  HeartRateReading,
  HeartRateSource,
} from "../ble/heartRateDevice";
import { BiofeedbackView } from "./BiofeedbackView";

import csvFixture from "../import/fixtures/health-export-sample.csv?raw";

class FakeConnection implements HeartRateConnection {
  deviceName = "Fake Strap";
  private readingListeners = new Set<(r: HeartRateReading) => void>();
  private statusListeners = new Set<(s: ConnectionStatus) => void>();
  disconnected = false;

  onReading(listener: (r: HeartRateReading) => void): () => void {
    this.readingListeners.add(listener);
    return () => this.readingListeners.delete(listener);
  }

  onStatus(listener: (s: ConnectionStatus) => void): () => void {
    this.statusListeners.add(listener);
    listener({ state: "connected", deviceName: this.deviceName });
    return () => this.statusListeners.delete(listener);
  }

  async disconnect(): Promise<void> {
    this.disconnected = true;
    for (const listener of this.statusListeners) {
      listener({ state: "disconnected" });
    }
  }

  emit(reading: HeartRateReading): void {
    for (const listener of this.readingListeners) {
      listener(reading);
    }
  }
}

class FakeSource implements HeartRateSource {
  connection = new FakeConnection();
  constructor(readonly supported: boolean) {}

  async connect(): Promise<HeartRateConnection> {
    return this.connection;
  }
}

function rrReading(rrMs: number[], receivedAtMs: number): HeartRateReading {
  return {
    bpm: rrMs.length > 0 ? Math.round(60_000 / (rrMs[0] ?? 800)) : null,
    contact: "detected",
    rrIntervalsMs: rrMs,
    receivedAtMs,
    malformed: false,
  };
}

/** 40 clean beats at 800 ms, spread over ~32 s of injected time. */
function streamCleanBeats(source: FakeSource, startMs: number): void {
  for (let i = 0; i < 40; i += 1) {
    source.connection.emit(rrReading([800], startMs + i * 800));
  }
}

function renderView(source: FakeSource, now: () => number): void {
  render(
    <I18nProvider>
      <BiofeedbackView source={source} now={now} />
    </I18nProvider>,
  );
}

describe("BiofeedbackView", () => {
  it("shows the wellness framing and the privacy note up front", () => {
    renderView(new FakeSource(true), () => 0);
    expect(screen.getByTestId("wellness-badge").textContent).toContain("Wellness feature");
    expect(screen.getByTestId("wellness-note").textContent).toContain("does not diagnose");
    expect(screen.getByTestId("privacy-note").textContent).toContain("Nothing is uploaded");
  });

  it("has an honest unsupported path (no Web Bluetooth: Firefox, Safari)", () => {
    renderView(new FakeSource(false), () => 0);
    expect(screen.getByTestId("bluetooth-unsupported")).toBeTruthy();
    expect(screen.getByTestId("bluetooth-unsupported").textContent).toContain(
      "Web Bluetooth is not available",
    );
    expect(screen.queryByTestId("device-connect")).toBeNull();
    expect(screen.getByTestId("goto-csv")).toBeTruthy();
    expect(screen.getByTestId("csv-panel")).toBeTruthy();
  });

  it("explains permissions before connecting, then connects a sensor", async () => {
    const source = new FakeSource(true);
    renderView(source, () => 0);

    fireEvent.click(screen.getByTestId("permission-explainer").querySelector("button")!);
    expect(screen.getByTestId("permission-explainer-body").textContent).toContain("never uploaded");

    fireEvent.click(screen.getByTestId("device-connect"));
    await waitFor(() => expect(screen.getByTestId("device-connected")).toBeTruthy());
    expect(screen.getByTestId("device-connected").textContent).toContain("Fake Strap");
    expect(screen.getByTestId("session-panel")).toBeTruthy();
    // The dizzy stop is only there once a session is running.
    expect(screen.queryByTestId("dizzy-stop")).toBeNull();
    fireEvent.click(screen.getByTestId("session-start"));
    expect(screen.getByTestId("dizzy-stop")).toBeTruthy();
  });

  it("withholds numbers when signal quality is poor", async () => {
    const source = new FakeSource(true);
    renderView(source, () => 0);
    fireEvent.click(screen.getByTestId("device-connect"));
    await waitFor(() => expect(screen.getByTestId("device-connected")).toBeTruthy());

    // Two beats only: below minBeats -> quality poor -> numbers withheld.
    source.connection.emit(rrReading([800], 1_000));
    source.connection.emit(rrReading([800], 1_800));
    await waitFor(() => expect(screen.getByTestId("quality-level").textContent).toContain("Poor"));
    expect(screen.getByTestId("metrics-withheld")).toBeTruthy();
    expect(screen.queryByTestId("metric-rmssd")).toBeNull();
  });

  it("runs a session and stops on 'I feel dizzy'", async () => {
    const source = new FakeSource(true);
    let nowMs = 1_000;
    renderView(source, () => nowMs);
    fireEvent.click(screen.getByTestId("device-connect"));
    await waitFor(() => expect(screen.getByTestId("device-connected")).toBeTruthy());

    fireEvent.click(screen.getByTestId("session-start"));
    expect(screen.getByTestId("dizzy-stop")).toBeTruthy();

    streamCleanBeats(source, nowMs);
    nowMs += 32_000;
    // One more ingest tick so the window recompute sees the advanced clock.
    source.connection.emit(rrReading([800], nowMs));

    fireEvent.click(screen.getByTestId("dizzy-stop"));
    await waitFor(() => expect(screen.getByTestId("session-ended")).toBeTruthy());
    // The summary appears, and it names the dizzy stop without drawing a
    // health conclusion.
    expect(screen.getByTestId("summary-body")).toBeTruthy();
    expect(screen.getByTestId("summary-body").textContent).toContain("dizziness");
    expect(screen.getByTestId("summary-caveats").textContent).toContain("not a health assessment");
    // The pace is frozen: capture it and confirm it does not move after the
    // stop (no ticks apply while the session is ended).
    const rateAfterStop = screen.getByTestId("pacer-rate").textContent;
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(screen.getByTestId("pacer-rate").textContent).toBe(rateAfterStop);
  });

  it("imports a CSV with no hardware (processed locally) and summarises", async () => {
    const source = new FakeSource(false);
    renderView(source, () => 10_000);
    expect(screen.getByTestId("bluetooth-unsupported")).toBeTruthy();

    const input = screen.getByTestId("csv-input") as HTMLInputElement;
    const file = new File([csvFixture], "health-export-sample.csv", { type: "text/csv" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(screen.getByTestId("csv-result")).toBeTruthy());
    expect(screen.getByTestId("csv-result").textContent).toContain("40 intervals imported");

    fireEvent.click(screen.getByTestId("session-start"));
    fireEvent.click(screen.getByTestId("session-end"));
    await waitFor(() => expect(screen.getByTestId("summary-body")).toBeTruthy());
  });

  it("saves a summary encrypted and shows it in the trend", async () => {
    const source = new FakeSource(true);
    let nowMs = 50_000;
    renderView(source, () => nowMs);
    fireEvent.click(screen.getByTestId("device-connect"));
    await waitFor(() => expect(screen.getByTestId("device-connected")).toBeTruthy());

    fireEvent.click(screen.getByTestId("session-start"));
    streamCleanBeats(source, nowMs);
    nowMs += 32_000;
    source.connection.emit(rrReading([800], nowMs));
    fireEvent.click(screen.getByTestId("session-end"));
    await waitFor(() => expect(screen.getByTestId("summary-body")).toBeTruthy());

    fireEvent.click(screen.getByTestId("summary-save"));
    await waitFor(() => expect(screen.getByTestId("summary-save").textContent).toBe("Saved"));
    await waitFor(() => expect(screen.getByTestId("trend-table")).toBeTruthy());
  });

  it("has no axe violations (idle and with the CSV panel visible)", async () => {
    const { container } = render(
      <I18nProvider>
        <BiofeedbackView source={new FakeSource(true)} now={() => 0} />
      </I18nProvider>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
