/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f0f5fa",
          100: "#dce6f2",
          200: "#b8cde5",
          300: "#8badd4",
          400: "#5e8dc3",
          500: "#1B3A5C",
          600: "#172f4b",
          700: "#12243a",
          800: "#0d1929",
          900: "#080e18",
        },
        sidebar: {
          DEFAULT: "#0F172A",
          hover: "rgba(255, 255, 255, 0.06)",
          active: "rgba(255, 255, 255, 0.10)",
          border: "rgba(255, 255, 255, 0.08)",
        },
        surface: {
          primary: "#ffffff",
          secondary: "#f8f9fb",
          tertiary: "#f1f3f7",
          border: "#e5e7eb",
          hover: "#f3f4f6",
        },
        text: {
          primary: "#111827",
          secondary: "#4b5563",
          tertiary: "#9ca3af",
          inverse: "#ffffff",
        },
        status: {
          success: "#059669",
          "success-light": "#ecfdf5",
          warning: "#d97706",
          "warning-light": "#fffbeb",
          danger: "#dc2626",
          "danger-light": "#fef2f2",
          info: "#2563eb",
          "info-light": "#eff6ff",
        },
        score: {
          excellent: "#059669",
          good: "#2563eb",
          fair: "#d97706",
          poor: "#dc2626",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
      },
      fontSize: {
        "2xs": ["0.625rem", { lineHeight: "0.875rem" }],
      },
      boxShadow: {
        "xs": "0 1px 2px rgba(0, 0, 0, 0.03)",
        "sm": "0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02)",
        "DEFAULT": "0 2px 8px rgba(0, 0, 0, 0.04), 0 1px 3px rgba(0, 0, 0, 0.03)",
        "md": "0 4px 16px rgba(0, 0, 0, 0.05), 0 2px 4px rgba(0, 0, 0, 0.03)",
        "lg": "0 8px 30px rgba(0, 0, 0, 0.06), 0 4px 8px rgba(0, 0, 0, 0.03)",
        "xl": "0 16px 50px rgba(0, 0, 0, 0.08)",
        glass: "0 4px 30px rgba(0, 0, 0, 0.04)",
        "glass-lg": "0 8px 40px rgba(0, 0, 0, 0.06)",
        "glass-xl": "0 12px 50px rgba(0, 0, 0, 0.08)",
        soft: "0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02)",
        card: "0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02)",
      },
      backdropBlur: {
        glass: "16px",
      },
      borderRadius: {
        "2xl": "1rem",
        "3xl": "1.5rem",
      },
      animation: {
        "fade-in": "fadeIn 0.3s ease-out",
        "fade-in-up": "fadeInUp 0.4s ease-out",
        "slide-up": "slideUp 0.3s ease-out",
        "slide-in-right": "slideInRight 0.3s ease-out",
        "pulse-slow": "pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "shimmer": "shimmer 2s linear infinite",
        "progress-stripe": "progressStripe 1s linear infinite",
        "bounce-in": "bounceIn 0.5s cubic-bezier(0.34, 1.56, 0.64, 1)",
        "score-fill": "scoreFill 1s ease-out",
        "spin-slow": "spin 2s linear infinite",
        "float": "float 3s ease-in-out infinite",
        "scale-in": "scaleIn 0.2s ease-out",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        fadeInUp: {
          "0%": { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        slideUp: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        slideInRight: {
          "0%": { opacity: "0", transform: "translateX(-12px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        progressStripe: {
          "0%": { backgroundPosition: "1rem 0" },
          "100%": { backgroundPosition: "0 0" },
        },
        bounceIn: {
          "0%": { opacity: "0", transform: "scale(0.3)" },
          "50%": { opacity: "1", transform: "scale(1.05)" },
          "70%": { transform: "scale(0.9)" },
          "100%": { transform: "scale(1)" },
        },
        scoreFill: {
          "0%": { width: "0%" },
          "100%": { width: "var(--target-width)" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0px)" },
          "50%": { transform: "translateY(-6px)" },
        },
        scaleIn: {
          "0%": { opacity: "0", transform: "scale(0.95)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
      },
    },
  },
  plugins: [],
};
