import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api/chat': {
        target: 'http://127.0.0.1:10081',
        changeOrigin: true,
      },
      '/api/wiki': {
        target: 'http://127.0.0.1:10081',
        changeOrigin: true,
      },
      '/api/analysis': {
        target: 'http://127.0.0.1:10081',
        changeOrigin: true,
      },
      '/api/factchecker': {
        target: 'http://127.0.0.1:10081',
        changeOrigin: true,
      },
      '/api/tracking': {
        target: 'http://127.0.0.1:10081',
        changeOrigin: true,
      },
      '/api/legal': {
        target: 'http://127.0.0.1:10081',
        changeOrigin: true,
      },
      '/api/settings': {
        target: 'http://127.0.0.1:10081',
        changeOrigin: true,
      },
      '/api/system': {
        target: 'http://127.0.0.1:10081',
        changeOrigin: true,
      },
      '/api/downloads': {
        target: 'http://127.0.0.1:10081',
        changeOrigin: true,
      },
      '/api/workflow': {
        target: 'http://127.0.0.1:10081',
        changeOrigin: true,
      },
      '/api/pdf_page': {
        target: 'http://127.0.0.1:10081',
        changeOrigin: true,
      },
      '/api/source': {
        target: 'http://127.0.0.1:10081',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/pdfapi': {
        target: 'http://127.0.0.1:5000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/pdfapi/, '/api'),
      },
    },
  },
})
