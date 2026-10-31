import { useEffect, useState } from 'react'

// Listens for toast() events (see api.js) and shows them bottom-right for a few seconds.
export default function Toasts() {
  const [items, setItems] = useState([])
  useEffect(() => {
    let n = 0
    const add = (e) => {
      const id = ++n + Date.now()
      setItems((xs) => [...xs.slice(-3), { id, ...e.detail }])
      setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), e.detail.kind === 'error' ? 8000 : 5000)
    }
    window.addEventListener('rahat-toast', add)
    return () => window.removeEventListener('rahat-toast', add)
  }, [])
  return (
    <div className="toasts" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind} ${t.onClick ? 'clickable' : ''}`}
          onClick={() => { t.onClick?.(); setItems((xs) => xs.filter((x) => x.id !== t.id)) }}>
          {t.text}
        </div>
      ))}
    </div>
  )
}
