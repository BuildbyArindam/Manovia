/**
 * Tailwind configuration.
 *
 * Every colour is a CSS variable (see `src/index.css`), never a literal: the
 * same utility class (`bg-surface`, `text-ink`) renders correctly in both
 * themes because the variable changes, not the markup. Warm neutrals, a sage
 * accent and a terracotta danger tone — deliberately not clinical blue.
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  darkMode: ["selector", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--manovia-bg)",
        surface: "var(--manovia-surface)",
        "surface-muted": "var(--manovia-surface-muted)",
        border: "var(--manovia-border)",
        "border-strong": "var(--manovia-border-strong)",
        ink: "var(--manovia-text)",
        "ink-muted": "var(--manovia-text-muted)",
        accent: {
          DEFAULT: "var(--manovia-accent-text)",
          bg: "var(--manovia-accent-bg)",
          fg: "var(--manovia-accent-fg)",
          soft: "var(--manovia-accent-soft)",
        },
        danger: {
          DEFAULT: "var(--manovia-danger-text)",
          bg: "var(--manovia-danger-bg)",
          fg: "var(--manovia-danger-fg)",
          soft: "var(--manovia-danger-soft)",
        },
        focus: "var(--manovia-focus)",
      },
      fontFamily: {
        sans: "var(--manovia-font-sans)",
        serif: "var(--manovia-font-serif)",
      },
      fontSize: {
        // Generous body copy: 16px minimum with a relaxed 1.75 line height.
        base: ["1rem", { lineHeight: "1.75" }],
        lg: ["1.125rem", { lineHeight: "1.75" }],
      },
      borderRadius: {
        lg: "0.625rem",
        xl: "0.875rem",
        "2xl": "1.25rem",
      },
      boxShadow: {
        soft: "0 1px 2px rgb(42 37 33 / 0.05), 0 12px 32px -18px rgb(42 37 33 / 0.35)",
      },
      maxWidth: {
        prose: "68ch",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "rise-in": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 220ms ease-out both",
        "rise-in": "rise-in 220ms ease-out both",
      },
    },
  },
  plugins: [],
};
