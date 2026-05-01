/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          dark: '#030b1c',
          blue: '#13358c',
          glow: '#00d2ff',
          yellow: '#ffcc00'
        }
      },
      fontFamily: {
        'display': ['"Arial Black"', 'Impact', 'sysetm-ui'],
      }
    },
  },
  plugins: [],
}