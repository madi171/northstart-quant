import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({ plugins: [react()], publicDir: 'frontend/public', server: { proxy: { '/api': 'http://127.0.0.1:8000' } }, build: { outDir: 'dist' } });
