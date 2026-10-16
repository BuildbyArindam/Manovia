/**
 * The wearable panel (Day 42): permission explainer, connect, status,
 * reconnect, and the honest "not supported on this browser" path.
 *
 * Web Bluetooth exists in Chromium browsers over HTTPS (or localhost) only.
 * Firefox and Safari get a plain explanation and the CSV import instead —
 * never a broken button. The feature check is `navigator.bluetooth`, not
 * user-agent sniffing.
 *
 * WELLNESS FEATURE. Not a medical device.
 */

import { useState, type ReactElement } from "react";

import { t } from "@/lib/i18n/i18n";

import type { ConnectionStatus, HeartRateReading } from "../ble/heartRateDevice";

export interface DevicePanelProps {
  supported: boolean;
  status: ConnectionStatus | null;
  lastReading: HeartRateReading | null;
  busy: boolean;
  error: string | null;
  onConnect: () => void;
  onDisconnect: () => void;
  onGoToCsv: () => void;
}

function contactLine(reading: HeartRateReading | null): string | null {
  if (reading === null) {
    return null;
  }
  if (reading.contact === "detected") {
    return t("biofeedback.device.contactDetected");
  }
  if (reading.contact === "not-detected") {
    return t("biofeedback.device.contactNotDetected");
  }
  return null;
}

export function DevicePanel({
  supported,
  status,
  lastReading,
  busy,
  error,
  onConnect,
  onDisconnect,
  onGoToCsv,
}: DevicePanelProps): ReactElement {
  const [explainerOpen, setExplainerOpen] = useState(false);
  const connected = status?.state === "connected";

  return (
    <section
      className="space-y-4 rounded-2xl border border-border bg-surface p-6"
      aria-labelledby="biofeedback-device-heading"
      data-testid="device-panel"
    >
      <h2 id="biofeedback-device-heading" className="text-xl">
        {t("biofeedback.device.heading")}
      </h2>

      {!supported ? (
        <div className="space-y-3" data-testid="bluetooth-unsupported">
          <p className="font-semibold">{t("biofeedback.device.unsupportedTitle")}</p>
          <p className="text-ink-muted">{t("biofeedback.device.unsupportedBody")}</p>
          <button
            type="button"
            className="rounded-xl border border-border-strong px-4 py-2 font-semibold"
            data-testid="goto-csv"
            onClick={onGoToCsv}
          >
            {t("biofeedback.csv.goToImport")}
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-ink-muted">{t("biofeedback.device.supportedIntro")}</p>
          <div data-testid="permission-explainer">
            <button
              type="button"
              className="rounded-xl border border-border px-4 py-2 font-semibold"
              aria-expanded={explainerOpen}
              onClick={() => setExplainerOpen((open) => !open)}
            >
              {t("biofeedback.device.permissionTitle")}
            </button>
            {explainerOpen ? (
              <ul
                className="mt-3 list-disc space-y-1 pl-5 text-sm text-ink-muted"
                data-testid="permission-explainer-body"
              >
                <li>{t("biofeedback.device.permission1")}</li>
                <li>{t("biofeedback.device.permission2")}</li>
                <li>{t("biofeedback.device.permission3")}</li>
              </ul>
            ) : null}
          </div>

          {connected ? (
            <div className="space-y-2">
              <p data-testid="device-connected" className="font-semibold">
                {t("biofeedback.device.connected", {
                  name: status.state === "connected" ? status.deviceName : "",
                })}
              </p>
              {contactLine(lastReading) !== null ? (
                <p className="text-sm text-ink-muted" data-testid="device-contact">
                  {contactLine(lastReading)}
                </p>
              ) : null}
              <button
                type="button"
                className="rounded-xl border border-border-strong px-4 py-2 font-semibold"
                data-testid="device-disconnect"
                onClick={onDisconnect}
              >
                {t("biofeedback.device.disconnect")}
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="rounded-xl bg-[var(--manovia-accent)] px-4 py-2 font-semibold text-white disabled:opacity-60"
              data-testid="device-connect"
              disabled={busy}
              onClick={onConnect}
            >
              {busy ? t("biofeedback.device.connecting") : t("biofeedback.device.connect")}
            </button>
          )}

          {status?.state === "reconnecting" ? (
            <p role="status" data-testid="device-reconnecting">
              {t("biofeedback.device.reconnecting", {
                attempt: status.attempt,
                max: status.maxAttempts,
              })}
            </p>
          ) : null}
          {status?.state === "failed" ? (
            <p role="status" data-testid="device-failed" className="text-ink-muted">
              {t("biofeedback.device.reconnectFailed")}
            </p>
          ) : null}
          {error !== null ? (
            <p role="alert" data-testid="device-error" className="text-ink-muted">
              {error}
            </p>
          ) : null}
        </div>
      )}
    </section>
  );
}
