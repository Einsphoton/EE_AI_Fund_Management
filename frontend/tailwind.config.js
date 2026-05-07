/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: { DEFAULT: "#0a0e1a", soft: "#0f1424", card: "#121a2e" },
        line: "#1f2a44",
        accent: { DEFAULT: "#7c5cff", soft: "#9d85ff" },
        emerald2: "#10b981",
        rose2: "#ef4444",
        amber2: "#f59e0b",
        muted: "#7587a8",
      },
      fontFamily: {
        sans: [
          "Inter", "ui-sans-serif", "system-ui", "-apple-system",
          "PingFang SC", "Microsoft YaHei", "sans-serif",
        ],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(124,92,255,0.25), 0 10px 40px -10px rgba(124,92,255,0.45)",
        card: "0 1px 0 rgba(255,255,255,0.04) inset, 0 10px 40px -20px rgba(0,0,0,0.6)",
      },
      backgroundImage: {
        "grid-fade":
          "radial-gradient(ellipse at top, rgba(124,92,255,0.18), transparent 50%), radial-gradient(ellipse at bottom right, rgba(16,185,129,0.10), transparent 50%)",
      },
    },
  },
  plugins: [],
};
