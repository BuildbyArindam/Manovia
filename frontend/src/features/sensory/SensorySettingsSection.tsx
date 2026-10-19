/**
 * Settings → Sensory profile (Day 46).
 *
 * Five small choices, applied immediately, reversible with one button, stored
 * only in this browser. Each option says what it actually does rather than
 * using a word like "calm" on its own — the person should not have to toggle
 * something to find out what it meant.
 */

import type { ReactElement } from "react";
import { Link } from "react-router-dom";

import { t } from "@/lib/i18n/i18n";

import { useSensory } from "./SensoryProvider";
import {
  COLOUR_OPTIONS,
  DEFAULT_SENSORY_PROFILE,
  DENSITY_OPTIONS,
  FONT_OPTIONS,
  MOTION_OPTIONS,
  SOUND_OPTIONS,
  type Option,
  type SensoryProfile,
} from "./types";

interface FieldProps<K extends keyof SensoryProfile> {
  name: K;
  legendKey: string;
  options: ReadonlyArray<Option<SensoryProfile[K] & string>>;
  value: SensoryProfile[K];
  onChange: (value: SensoryProfile[K]) => void;
}

function OptionField<K extends keyof SensoryProfile>({
  name,
  legendKey,
  options,
  value,
  onChange,
}: FieldProps<K>): ReactElement {
  return (
    <fieldset className="mt-5" data-testid={`sensory-${name}`}>
      <legend className="text-sm font-semibold text-ink">{t(legendKey)}</legend>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        {options.map((option) => (
          <label
            key={option.value}
            className="flex min-h-11 cursor-pointer items-start gap-3 rounded-xl border border-border-strong px-3 py-2"
          >
            <input
              type="radio"
              name={`sensory-${name}`}
              value={option.value}
              checked={value === option.value}
              onChange={() => {
                onChange(option.value);
              }}
              className="mt-1 h-4 w-4 accent-accent-bg"
            />
            <span>
              <span className="block font-semibold">{t(option.labelKey)}</span>
              <span className="block text-sm text-ink-muted">{t(option.hintKey)}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function SensorySettingsSection(): ReactElement {
  const { profile, setOption, setProfile, motion, sound, density } = useSensory();

  return (
    <section
      aria-labelledby="sensory-heading"
      data-testid="sensory-settings"
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
    >
      <h2 id="sensory-heading" className="text-lg">
        {t("sensory.settings.heading")}
      </h2>
      <p className="mt-1 text-sm text-ink-muted">{t("sensory.settings.lede")}</p>

      <OptionField
        name="motion"
        legendKey="sensory.settings.motionLegend"
        options={MOTION_OPTIONS}
        value={profile.motion}
        onChange={(value) => {
          setOption("motion", value);
        }}
      />
      <OptionField
        name="sound"
        legendKey="sensory.settings.soundLegend"
        options={SOUND_OPTIONS}
        value={profile.sound}
        onChange={(value) => {
          setOption("sound", value);
        }}
      />
      <OptionField
        name="density"
        legendKey="sensory.settings.densityLegend"
        options={DENSITY_OPTIONS}
        value={profile.density}
        onChange={(value) => {
          setOption("density", value);
        }}
      />
      <OptionField
        name="colours"
        legendKey="sensory.settings.coloursLegend"
        options={COLOUR_OPTIONS}
        value={profile.colours}
        onChange={(value) => {
          setOption("colours", value);
        }}
      />
      <OptionField
        name="fontSize"
        legendKey="sensory.settings.fontLegend"
        options={FONT_OPTIONS}
        value={profile.fontSize}
        onChange={(value) => {
          setOption("fontSize", value);
        }}
      />

      <p className="mt-4 text-sm text-ink-muted" data-testid="sensory-effective">
        {t("sensory.settings.effective", { motion, sound, density })}
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Link
          to="/calm"
          className="inline-flex min-h-11 items-center rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg"
        >
          {t("sensory.settings.openCalm")}
        </Link>
        <button
          type="button"
          onClick={() => {
            setProfile({ ...DEFAULT_SENSORY_PROFILE });
          }}
          className="inline-flex min-h-11 items-center rounded-xl border border-border-strong px-4 py-2 text-sm font-semibold text-ink"
        >
          {t("sensory.settings.reset")}
        </button>
      </div>

      <p className="mt-3 text-sm text-ink-muted">{t("sensory.settings.privacy")}</p>
    </section>
  );
}
