import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'
import { createViteProxy } from './scripts/proxy-config.js'

const defaultAllowedHost = 'arthurmao.synology.me'
const publicHost = process.env.SIQ_PUBLIC_HOST || ''
const publicHmrProtocol = process.env.SIQ_PUBLIC_HMR_PROTOCOL || 'wss'
const publicHmrClientPort = Number(process.env.SIQ_PUBLIC_HMR_CLIENT_PORT || 0) || undefined
const devPort = Number(process.env.SIQ_FRONTEND_PORT || process.env.PORT || 15173)
const enablePublicHmr = Boolean(publicHost && publicHmrClientPort)
const allowedHosts = Array.from(new Set([defaultAllowedHost, publicHost].filter(Boolean)))

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: devPort,
    strictPort: true,
    allowedHosts,
    hmr: enablePublicHmr
      ? {
          protocol: publicHmrProtocol,
          host: publicHost,
          clientPort: publicHmrClientPort,
        }
      : undefined,
    proxy: createViteProxy({ includeAuth: true, includeEval: true }),
  },
})
