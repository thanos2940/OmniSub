/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            fontFamily: {
                sans: ['Quicksand', 'sans-serif'],
            },
            colors: {
                gray: {
                    150: '#ECEEF1',
                    250: '#D6DAE0',
                    450: '#828B98',
                    650: '#46505E',
                    750: '#374151',
                    850: '#18212F',
                    950: '#0B1120',
                },
                rose: {
                    450: '#F43F5E',
                }
            }
        },
    },
    plugins: [],
}
