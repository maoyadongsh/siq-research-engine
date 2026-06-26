import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { ToastProvider } from './components/ui'
import { installFetchAuth } from './lib/fetchWithAuth'

try {
  document.documentElement.classList.remove('dark')
  localStorage.setItem('theme', 'light')
} catch {
  // Keep startup resilient if storage is unavailable.
}

installFetchAuth()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </StrictMode>,
)
