const forms = require("@tailwindcss/forms");
const containerQueries = require("@tailwindcss/container-queries");

module.exports = {
  darkMode: "class",
  content: ["./app/static/GalaxyViewMain.html"],
  theme: {
    extend: {
      colors: {
        primary: "#7BE7FF",
        background: "#020611",
        surface: "rgba(6, 16, 35, 0.72)",
        muted: "#5B7EA3",
        accent: "#FF5DB1",
        warm: "#FFD56A"
      },
      fontFamily: {
        display: ['"Space Grotesk"', "sans-serif"],
        heading: ['"Rajdhani"', "sans-serif"],
        mono: ['"Space Mono"', "monospace"]
      },
      boxShadow: {
        glow: "0 0 24px rgba(123, 231, 255, 0.18)",
        body: "0 0 32px rgba(123, 231, 255, 0.35)"
      }
    }
  },
  plugins: [forms, containerQueries]
};
