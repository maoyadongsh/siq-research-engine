import { createContext } from 'react'

export type ToastType = 'success' | 'error' | 'info' | 'warning'
export type ToastInput = { title: string; description?: string; type?: ToastType; duration?: number }
export type ToastItem = Required<Omit<ToastInput, 'duration'>> & { id: number }

export const ToastContext = createContext<{ toast: (input: ToastInput) => void } | null>(null)
