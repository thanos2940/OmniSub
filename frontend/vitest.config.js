import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Frontend unit tests: `npm test` (vitest, jsdom). Network-free — every test mocks `src/api.js`.
export default defineConfig({
    plugins: [react()],
    test: {
        environment: 'jsdom',
        globals: true,
        include: ['src/**/*.test.{js,jsx}'],
        setupFiles: ['./src/__tests__/setup.js'],
    },
});
