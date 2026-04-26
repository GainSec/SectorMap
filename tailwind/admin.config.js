const forms = require("@tailwindcss/forms");
const containerQueries = require("@tailwindcss/container-queries");

module.exports = {
  darkMode: "class",
  content: ["./app/static/AdminDashboard.html"],
  theme: {
    extend: {
      colors: {
        primary: "#00E5FF",
        "background-dark": "#050914",
        surface: "rgba(16, 28, 48, 0.72)",
        "text-main": "#E0F7FA",
        muted: "#4A6B8C",
        accent: "#FF2A6D"
      },
      fontFamily: {
        display: ['"Space Grotesk"', "sans-serif"],
        heading: ['"Rajdhani"', "sans-serif"],
        mono: ['"Space Mono"', "monospace"]
      },
      boxShadow: {
        glow: "0 0 20px rgba(0, 229, 255, 0.18)"
      }
    }
  },
  plugins: [forms, containerQueries]
};
