/**
 * Web Bluetooth layer tests (Day 42): support detection, packet parsing from
 * notifications, reconnect with backoff, exhaustion, and the cancel path —
 * all against an in-memory fake GATT (no radio, fully offline).
 */

import { describe, expect, it, vi } from "vitest";

import {
  DEFAULT_RECONNECT_POLICY,
  NotSupportedError,
  UserCancelledError,
  isWebBluetoothSupported,
  webBluetoothSource,
  type BleCharacteristic,
  type BleDevice,
  type BleRemoteGattServer,
  type BleRemoteGattService,
  type ConnectionStatus,
  type HeartRateReading,
} from "./heartRateDevice";

/** A fake `heart_rate_measurement` characteristic that can push packets. */
class FakeCharacteristic implements BleCharacteristic {
  value: DataView | null = null;
  private readonly listeners = new Set<(event: unknown) => void>();
  notificationsStarted = false;

  async startNotifications(): Promise<unknown> {
    this.notificationsStarted = true;
    return this;
  }

  async stopNotifications(): Promise<unknown> {
    this.notificationsStarted = false;
    return this;
  }

  addEventListener(type: "characteristicvaluechanged", listener: (event: unknown) => void): void {
    if (type === "characteristicvaluechanged") {
      this.listeners.add(listener);
    }
  }

  removeEventListener(type: "characteristicvaluechanged", listener: (event: unknown) => void): void {
    if (type === "characteristicvaluechanged") {
      this.listeners.delete(listener);
    }
  }

  emit(bytes: number[]): void {
    const data = Uint8Array.from(bytes);
    this.value = new DataView(data.buffer, data.byteOffset, data.byteLength);
    for (const listener of this.listeners) {
      listener({ target: this });
    }
  }
}

class FakeService implements BleRemoteGattService {
  constructor(readonly characteristic: FakeCharacteristic) {}

  async getCharacteristic(uuid: string): Promise<BleCharacteristic> {
    if (uuid !== "heart_rate_measurement") {
      throw new Error(`unexpected characteristic ${uuid}`);
    }
    return this.characteristic;
  }
}

class FakeServer implements BleRemoteGattServer {
  connected = false;
  connectFailuresLeft = 0;
  readonly service: FakeService;

  constructor(characteristic: FakeCharacteristic) {
    this.service = new FakeService(characteristic);
  }

  async connect(): Promise<BleRemoteGattServer> {
    if (this.connectFailuresLeft > 0) {
      this.connectFailuresLeft -= 1;
      throw new Error("GATT connect failed");
    }
    this.connected = true;
    return this;
  }

  async getPrimaryService(uuid: string): Promise<BleRemoteGattService> {
    if (uuid !== "heart_rate") {
      throw new Error(`unexpected service ${uuid}`);
    }
    return this.service;
  }

  disconnect(): void {
    this.connected = false;
  }
}

class FakeDevice implements BleDevice {
  name = "Fake Strap";
  readonly characteristic = new FakeCharacteristic();
  readonly server = new FakeServer(this.characteristic);
  gatt: FakeServer | null = this.server;
  private readonly disconnectListeners = new Set<() => void>();

  addEventListener(type: "gattserverdisconnected", listener: () => void): void {
    if (type === "gattserverdisconnected") {
      this.disconnectListeners.add(listener);
    }
  }

  removeEventListener(type: "gattserverdisconnected", listener: () => void): void {
    if (type === "gattserverdisconnected") {
      this.disconnectListeners.delete(listener);
    }
  }

  dropConnection(): void {
    this.server.connected = false;
    for (const listener of this.disconnectListeners) {
      listener();
    }
  }
}

class FakeBluetooth {
  requestError: Error | null = null;
  constructor(readonly device: FakeDevice) {}

  async requestDevice(_options: unknown): Promise<BleDevice> {
    if (this.requestError !== null) {
      throw this.requestError;
    }
    return this.device;
  }
}

/** Timer queue the tests drain by hand, so reconnects are deterministic. */
function manualTimers(): { setTimer: (fn: () => void, ms: number) => unknown; drain: () => Promise<void>; delays: number[] } {
  const queue: Array<() => void> = [];
  const delays: number[] = [];
  return {
    setTimer(fn, ms) {
      queue.push(fn);
      delays.push(ms);
      return queue.length;
    },
    async drain() {
      while (queue.length > 0) {
        const fn = queue.shift();
        fn?.();
        // Flush every pending microtask (the timer body is async) before the
        // next timer runs: a macrotask boundary does that in one hop.
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    },
    delays,
  };
}

describe("isWebBluetoothSupported", () => {
  it("is false where navigator.bluetooth is missing (Firefox, Safari, http)", () => {
    expect(isWebBluetoothSupported(undefined, true)).toBe(false);
    expect(isWebBluetoothSupported({}, false)).toBe(false);
  });

  it("is true with the API in a secure context", () => {
    expect(isWebBluetoothSupported({}, true)).toBe(true);
  });
});

describe("webBluetoothSource", () => {
  it("refuses politely where Web Bluetooth is missing", async () => {
    const source = webBluetoothSource({
      bluetooth: undefined,
      setTimer: () => 0,
    });
    Object.defineProperty(source, "supported", { get: () => false });
    await expect(source.connect()).rejects.toThrow(NotSupportedError);
  });

  it("maps a dismissed device chooser to UserCancelledError", async () => {
    const device = new FakeDevice();
    const bluetooth = new FakeBluetooth(device);
    const notFound = new Error("cancelled");
    notFound.name = "NotFoundError";
    bluetooth.requestError = notFound;
    const source = webBluetoothSource({ bluetooth, setTimer: () => 0 });
    await expect(source.connect()).rejects.toThrow(UserCancelledError);
  });

  it("connects, subscribes, and parses readings from notifications", async () => {
    const device = new FakeDevice();
    const source = webBluetoothSource({ bluetooth: new FakeBluetooth(device), setTimer: () => 0 });
    const connection = await source.connect();
    expect(connection.deviceName).toBe("Fake Strap");
    expect(device.characteristic.notificationsStarted).toBe(true);

    const readings: HeartRateReading[] = [];
    const statuses: ConnectionStatus[] = [];
    connection.onReading((r) => readings.push(r));
    connection.onStatus((s) => statuses.push(s));

    // flags 0x10 (RR present), bpm 72, one RR of 1024 (= 1000 ms)
    device.characteristic.emit([0x10, 72, 0x00, 0x04]);
    expect(readings.length).toBe(1);
    expect(readings[0]?.bpm).toBe(72);
    expect(readings[0]?.rrIntervalsMs).toEqual([1000]);
    expect(readings[0]?.malformed).toBe(false);
    expect(statuses[0]).toEqual({ state: "connected", deviceName: "Fake Strap" });

    // A truncated packet is reported as malformed, never as data.
    device.characteristic.emit([0x10, 72, 0x00]);
    expect(readings[1]?.malformed).toBe(true);
    expect(readings[1]?.rrIntervalsMs).toEqual([]);
  });

  it("reconnects with backoff after a drop and re-subscribes", async () => {
    const device = new FakeDevice();
    const timers = manualTimers();
    const source = webBluetoothSource({ bluetooth: new FakeBluetooth(device), setTimer: timers.setTimer });
    const connection = await source.connect();
    const statuses: ConnectionStatus[] = [];
    connection.onStatus((s) => statuses.push(s));

    device.dropConnection();
    await timers.drain();

    expect(statuses.map((s) => s.state)).toEqual([
      "connected",
      "disconnected",
      "reconnecting",
      "connected",
    ]);
    expect(timers.delays).toEqual([DEFAULT_RECONNECT_POLICY.backoffMs[0]]);
    expect(device.characteristic.notificationsStarted).toBe(true);
  });

  it("gives up after the configured attempts and says so", async () => {
    const device = new FakeDevice();
    const timers = manualTimers();
    const source = webBluetoothSource({ bluetooth: new FakeBluetooth(device), setTimer: timers.setTimer });
    const connection = await source.connect();
    // From here on, every reconnect attempt fails.
    device.server.connectFailuresLeft = 99;
    const statuses: ConnectionStatus[] = [];
    connection.onStatus((s) => statuses.push(s));

    device.dropConnection();
    await timers.drain();

    const states = statuses.map((s) => s.state);
    expect(states).toEqual([
      "connected",
      "disconnected",
      "reconnecting",
      "reconnecting",
      "reconnecting",
      "failed",
    ]);
    expect(timers.delays.length).toBe(DEFAULT_RECONNECT_POLICY.maxAttempts);
    const failed = statuses[statuses.length - 1];
    expect(failed?.state === "failed" ? failed.message : "").toContain("could not be reached");
  });

  it("does not reconnect after a deliberate disconnect", async () => {
    const device = new FakeDevice();
    const timers = manualTimers();
    const source = webBluetoothSource({ bluetooth: new FakeBluetooth(device), setTimer: timers.setTimer });
    const connection = await source.connect();
    const statuses: ConnectionStatus[] = [];
    connection.onStatus((s) => statuses.push(s));

    await connection.disconnect();
    device.dropConnection(); // the radio also notices; nothing may happen
    await timers.drain();

    expect(statuses.map((s) => s.state)).toEqual(["connected", "disconnected"]);
    expect(timers.delays).toEqual([]);
    expect(device.server.connected).toBe(false);
    expect(device.characteristic.notificationsStarted).toBe(false);
  });

  it("unsubscribes reading listeners", async () => {
    const device = new FakeDevice();
    const source = webBluetoothSource({ bluetooth: new FakeBluetooth(device), setTimer: () => 0 });
    const connection = await source.connect();
    const listener = vi.fn();
    const off = connection.onReading(listener);
    device.characteristic.emit([0x10, 70, 0x00, 0x04]);
    off();
    device.characteristic.emit([0x10, 70, 0x00, 0x04]);
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
