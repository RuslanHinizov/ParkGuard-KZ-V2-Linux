/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // ParkGuard brand palette
        brand: {
          50:  "#f0f4ff",
          100: "#dce6ff",
          500: "#3b6bff",
          600: "#2952e3",
          700: "#1e3fbf",
          900: "#0f1f66",
        },
        violation: {
          pending:  "#f59e0b",
          approved: "#10b981",
          disputed: "#ef4444",
          card_issued: "#6366f1",
        },
      },
    },
  },
  plugins: [],
};
