import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { ToastProvider } from './components/ui'

try {
  document.documentElement.classList.remove('dark')
  localStorage.setItem('theme', 'light')
} catch {
  // Keep startup resilient if storage is unavailable.
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </StrictMode>,
)
