/**
 * Web Bluetooth integration for BLE Heart Rate devices (Day 42).
 *
 * Standard BLE Heart Rate Service devices — chest straps, many watches in
 * broadcast mode — are the sensor this feature is built for. The flow is the
 * one Web Bluetooth mandates:
 *
 *   1. feature-detect (`navigator.bluetooth` — Web Bluetooth is Chromium-only
 *      today; Firefox and Safari do not have it, and the UI says so honestly
 *      and offers the CSV import instead);
 *   2. an explicit user gesture calls `requestDevice` with a permission
 *      explainer shown *before* it (the browser's own chooser appears on top);
 *   3. connect → `heart_rate` service → `heart_rate_measurement`
 *      characteristic → notifications; each packet is parsed by
 *      `signal/rr.ts` (`parseHeartRateMeasurement`) into bpm, contact status
 *      and RR intervals;
 *   4. on `gattserverdisconnected` the connection retries with backoff
 *      (3 attempts: 1 s, 2 s, 4 s) and re-subscribes; after that it reports
 *      failure honestly and the UI offers manual reconnect.
 *
 * All of the browser API surface is behind tiny structural interfaces and the
 * source takes an injectable `bluetooth` object, so the reconnect and parsing
 * behaviour is fully unit-tested against fakes — no radio required
 * (`heartRateDevice.test.ts`). The manual device checklist lives in
 * docs/verification/day42-biofeedback-hrv.md.
 *
 * WELLNESS FEATURE. Heart-rate data from this path is used for breathing
 * biofeedback on this device only; it is not a medical monitor.
 */

import {
  HEART_RATE_MEASUREMENT_UUID,
  HEART_RATE_SERVICE_UUID,
  MalformedPacket,
  parseHeartRateMeasurement,
  type HeartRateMeasurement,
  type SensorContact,
} from "../signal/rr";

/** Minimal structural slice of Web Bluetooth we use (not in the TS DOM lib). */
export interface BleCharacteristic {
  startNotifications(): Promise<unknown>;
  stopNotifications(): Promise<unknown>;
  addEventListener(type: "characteristicvaluechanged", listener: (event: unknown) => void): void;
  removeEventListener(type: "characteristicvaluechanged", listener: (event: unknown) => void): void;
  value?: DataView | null;
}

export interface BleRemoteGattService {
  getCharacteristic(uuid: string): Promise<BleCharacteristic>;
}

export interface BleDevice {
  name?: string | null;
  gatt?: {
    connected: boolean;
    connect(): Promise<BleRemoteGattServer>;
    disconnect(): void;
  } | null;
  addEventListener(type: "gattserverdisconnected", listener: () => void): void;
  removeEventListener(type: "gattserverdisconnected", listener: () => void): void;
}

export interface BleRemoteGattServer {
  connected: boolean;
  getPrimaryService(uuid: string): Promise<BleRemoteGattService>;
  disconnect(): void;
}

export interface BleBluetooth {
  requestDevice(options: unknown): Promise<BleDevice>;
}

export interface HeartRateReading {
  /** Instantaneous bpm from the packet, when the packet carries it. */
  bpm: number | null;
  contact: SensorContact;
  /** RR intervals in ms, in packet order. */
  rrIntervalsMs: number[];
  /** Arrival clock, ms (`Date.now()` unless injected). */
  receivedAtMs: number;
  /** Set when the packet could not be parsed (counted, never shown as data). */
  malformed: boolean;
}

export type ConnectionStatus =
  | { state: "connected"; deviceName: string }
  | { state: "disconnected" }
  | { state: "reconnecting"; attempt: number; maxAttempts: number }
  | { state: "failed"; message: string };

export interface HeartRateConnection {
  readonly deviceName: string;
  /** Subscribe to parsed readings. Returns an unsubscribe function. */
  onReading(listener: (reading: HeartRateReading) => void): () => void;
  /** Connection lifecycle (connected / reconnecting / failed). */
  onStatus(listener: (status: ConnectionStatus) => void): () => void;
  /** User-initiated disconnect: no reconnect afterwards. */
  disconnect(): Promise<void>;
}

export class UserCancelledError extends Error {
  constructor() {
    super("the device chooser was dismissed");
    this.name = "UserCancelledError";
  }
}

export class NotSupportedError extends Error {
  constructor() {
    super("Web Bluetooth is not available in this browser");
    this.name = "NotSupportedError";
  }
}

export interface ReconnectPolicy {
  /** Attempts after a drop before giving up. */
  maxAttempts: number;
  /** Delay before attempt n (ms); last entry repeats. */
  backoffMs: readonly number[];
}

export const DEFAULT_RECONNECT_POLICY: ReconnectPolicy = {
  maxAttempts: 3,
  backoffMs: [1000, 2000, 4000],
};

/** Feature detection: Web Bluetooth, only inside a secure context. */
export function isWebBluetoothSupported(
  bluetooth?: unknown,
  secureContext = typeof window !== "undefined" ? window.isSecureContext : true,
): boolean {
  const api = bluetooth ?? (typeof navigator !== "undefined" ? navigator.bluetooth : undefined);
  return api !== undefined && api !== null && secureContext;
}

export interface HeartRateSource {
  readonly supported: boolean;
  /**
   * Open the browser device chooser and connect. Must run inside a user
   * gesture. Rejects with {@link UserCancelledError} when the person dismisses
   * the chooser, {@link NotSupportedError} where Web Bluetooth is missing.
   */
  connect(): Promise<HeartRateConnection>;
}

export interface WebBluetoothSourceOptions {
  /** Injectable for tests; defaults to `navigator.bluetooth`. */
  bluetooth?: BleBluetooth;
  clock?: () => number;
  /** Injectable for tests; defaults to real timers. */
  setTimer?: (fn: () => void, ms: number) => unknown;
  reconnect?: ReconnectPolicy;
}

/**
 * The real source. Kept free of any UI concern; the connect button lives in
 * `ui/DevicePanel.tsx`.
 */
export function webBluetoothSource(options: WebBluetoothSourceOptions = {}): HeartRateSource {
  const clock = options.clock ?? (() => Date.now());
  const setTimer = options.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const policy = options.reconnect ?? DEFAULT_RECONNECT_POLICY;

  return {
    get supported(): boolean {
      return isWebBluetoothSupported(options.bluetooth ?? navigator.bluetooth);
    },

    async connect(): Promise<HeartRateConnection> {
      const bluetooth = options.bluetooth ?? (navigator.bluetooth as BleBluetooth | undefined);
      if (bluetooth === undefined || bluetooth === null) {
        throw new NotSupportedError();
      }
      let device: BleDevice;
      try {
        device = await bluetooth.requestDevice({
          filters: [{ services: [HEART_RATE_SERVICE_UUID] }],
          optionalServices: ["battery_service"],
        });
      } catch (error) {
        if (error instanceof Error && error.name === "NotFoundError") {
          throw new UserCancelledError();
        }
        throw error;
      }
      return openConnection(device, { clock, setTimer, policy });
    },
  };
}

interface ConnectionDeps {
  clock: () => number;
  setTimer: (fn: () => void, ms: number) => unknown;
  policy: ReconnectPolicy;
}

async function openConnection(device: BleDevice, deps: ConnectionDeps): Promise<HeartRateConnection> {
  const readingListeners = new Set<(reading: HeartRateReading) => void>();
  const statusListeners = new Set<(status: ConnectionStatus) => void>();
  let characteristic: BleCharacteristic | null = null;
  let closing = false;
  let reconnectAttempt = 0;
  let disposed = false;
  let currentStatus: ConnectionStatus = { state: "disconnected" };

  const emitStatus = (status: ConnectionStatus): void => {
    currentStatus = status;
    for (const listener of statusListeners) {
      listener(status);
    }
  };

  const emitReading = (reading: HeartRateReading): void => {
    for (const listener of readingListeners) {
      listener(reading);
    }
  };

  const onCharacteristicValue = (event: unknown): void => {
    const target = (event as { target?: { value?: DataView | null } }).target;
    const value = target?.value;
    if (value === undefined || value === null) {
      return;
    }
    const bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    let parsed: HeartRateMeasurement | null = null;
    try {
      parsed = parseHeartRateMeasurement(bytes);
    } catch (error) {
      if (!(error instanceof MalformedPacket)) {
        throw error;
      }
    }
    emitReading({
      bpm: parsed?.bpm ?? null,
      contact: parsed?.contact ?? "unsupported",
      rrIntervalsMs: parsed?.rrIntervalsMs ?? [],
      receivedAtMs: deps.clock(),
      malformed: parsed === null,
    });
  };

  const subscribe = async (): Promise<void> => {
    const server = device.gatt;
    if (server === undefined || server === null) {
      throw new Error("device has no GATT server");
    }
    const connected = await server.connect();
    const service = await connected.getPrimaryService(HEART_RATE_SERVICE_UUID);
    const next = await service.getCharacteristic(HEART_RATE_MEASUREMENT_UUID);
    if (characteristic !== null) {
      characteristic.removeEventListener("characteristicvaluechanged", onCharacteristicValue);
    }
    characteristic = next;
    next.addEventListener("characteristicvaluechanged", onCharacteristicValue);
    await next.startNotifications();
  };

  const scheduleReconnect = (): void => {
    if (closing || disposed) {
      return;
    }
    if (reconnectAttempt >= deps.policy.maxAttempts) {
      emitStatus({
        state: "failed",
        message: "the sensor disconnected and could not be reached again",
      });
      return;
    }
    reconnectAttempt += 1;
    emitStatus({
      state: "reconnecting",
      attempt: reconnectAttempt,
      maxAttempts: deps.policy.maxAttempts,
    });
    const delays = deps.policy.backoffMs;
    const delay = delays[Math.min(reconnectAttempt - 1, delays.length - 1)] ?? 1000;
    deps.setTimer(() => {
      void (async () => {
        try {
          await subscribe();
          reconnectAttempt = 0;
          emitStatus({ state: "connected", deviceName: device.name ?? "heart-rate sensor" });
        } catch {
          scheduleReconnect();
        }
      })();
    }, delay);
  };

  const onDisconnected = (): void => {
    if (closing) {
      return;
    }
    emitStatus({ state: "disconnected" });
    scheduleReconnect();
  };

  device.addEventListener("gattserverdisconnected", onDisconnected);
  await subscribe();
  emitStatus({ state: "connected", deviceName: device.name ?? "heart-rate sensor" });

  return {
    deviceName: device.name ?? "heart-rate sensor",
    onReading(listener) {
      readingListeners.add(listener);
      return () => readingListeners.delete(listener);
    },
    onStatus(listener) {
      statusListeners.add(listener);
      // Replay the current state: a subscriber after `connect()` must still
      // know the connection is up (the UI panel mounts later).
      listener(currentStatus);
      return () => statusListeners.delete(listener);
    },
    async disconnect() {
      closing = true;
      disposed = true;
      if (characteristic !== null) {
        characteristic.removeEventListener("characteristicvaluechanged", onCharacteristicValue);
        await characteristic.stopNotifications().catch(() => undefined);
      }
      device.removeEventListener("gattserverdisconnected", onDisconnected);
      device.gatt?.disconnect();
      emitStatus({ state: "disconnected" });
    },
  };
}
