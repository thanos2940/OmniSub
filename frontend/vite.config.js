import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
    plugins: [react()],
    // Relative asset URLs: the backend injects <base href> into index.html, so the app also works under a
    // reverse-proxy path (Caddy example.com/ombisub) without a rebuild.
    base: './',
    build: {
        rollupOptions: {
            output: {
                // Split big, rarely-changing vendor libs into their own chunks so they
                // cache across deploys and don't bloat the main bundle.
                manualChunks: {
                    'react-vendor': ['react', 'react-dom', 'react-router-dom'],
                    'ui-vendor': ['framer-motion', 'lucide-react'],
                },
            },
        },
        chunkSizeWarningLimit: 800,
    },
})
