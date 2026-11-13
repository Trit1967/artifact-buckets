import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

const api = 'http://127.0.0.1:8080';
const proxy = Object.fromEntries(['/api', '/oauth', '/mcp', '/preview', '/auth/oidc', '/.well-known', '/healthz'].map((p) => [p, api]));

export default defineConfig({
  plugins: [svelte()],
  server: { port: 5173, proxy },
  build: { outDir: 'dist', assetsInlineLimit: 0, sourcemap: false },
});
