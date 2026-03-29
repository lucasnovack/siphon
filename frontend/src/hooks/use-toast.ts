import { useState } from 'react'
import type { ToastProps } from '@/components/ui/toast'

interface ToastItem extends ToastProps {
  id: string
  title?: string
  description?: string
}

let toastIdCounter = 0

const listeners: Array<(toasts: ToastItem[]) => void> = []
let toasts: ToastItem[] = []

function dispatch(toast: ToastItem) {
  toasts = [...toasts, toast]
  listeners.forEach((l) => l(toasts))
  setTimeout(() => {
    toasts = toasts.filter((t) => t.id !== toast.id)
    listeners.forEach((l) => l(toasts))
  }, 5000)
}

export function toast(props: Omit<ToastItem, 'id'>) {
  dispatch({ ...props, id: String(++toastIdCounter) })
}

export function useToast() {
  const [items, setItems] = useState<ToastItem[]>(toasts)

  useState(() => {
    listeners.push(setItems)
    return () => {
      const idx = listeners.indexOf(setItems)
      if (idx > -1) listeners.splice(idx, 1)
    }
  })

  return { toasts: items, toast }
}
