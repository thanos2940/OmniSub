import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
    plugins: [react()],
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
