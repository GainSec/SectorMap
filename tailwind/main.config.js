const forms = require("@tailwindcss/forms");
const containerQueries = require("@tailwindcss/container-queries");

module.exports = {
  darkMode: "class",
  content: ["./app/static/MainOperations.html"],
  theme: {
    extend: {
      colors: {
        primary: "#FF2A2A",
        wood: "#5C3A21",
        hardware: "#111111",
        "crt-bg": "#051A05",
        "crt-text": "#33FF33",
        "duct-tape": "#9CA3AF",
        caution: "#FFD700",
        "background-light": "#f8f6f6",
        "background-dark": "#221010"
      },
      fontFamily: {
        marker: ['"Permanent Marker"', "cursive"],
        crt: ['"VT323"', "monospace"],
        punch: ['"Bangers"', "cursive"],
        display: ['"Space Grotesk"', "sans-serif"]
      },
      boxShadow: {
        hardware: "8px 8px 0px #000000",
        "crt-glow": "0 0 15px rgba(51, 255, 51, 0.2)"
      },
      borderWidth: {
        heavy: "6px"
      },
      borderColor: {
        heavy: "#000000"
      },
      borderRadius: {
        chunky: "12px",
        DEFAULT: "0.5rem",
        lg: "1rem",
        xl: "1.5rem",
        full: "9999px"
      },
      animation: {
        blink: "blink 1s step-end infinite",
        shake: "shake 0.5s cubic-bezier(.36,.07,.19,.97) both"
      },
      keyframes: {
        blink: {
          "0%, 100%": { opacity: 1 },
          "50%": { opacity: 0 }
        },
        shake: {
          "10%, 90%": { transform: "translate3d(-1px, 0, 0)" },
          "20%, 80%": { transform: "translate3d(2px, 0, 0)" },
          "30%, 50%, 70%": { transform: "translate3d(-4px, 0, 0)" },
          "40%, 60%": { transform: "translate3d(4px, 0, 0)" }
        }
      }
    }
  },
  plugins: [forms, containerQueries]
};
