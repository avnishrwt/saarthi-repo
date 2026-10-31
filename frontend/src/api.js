// Backend base URL: Vite dev server (5173) talks to FastAPI on :8000 of the same host,
// so a phone on the same Wi-Fi works with http://<laptop-ip>:5173. Built app served by
// FastAPI at /ui uses the same origin. Override with VITE_API.
import { useEffect, useRef } from 'react'

export const API =
  import.meta.env.VITE_API ||
  (location.port === '5173' ? `${location.protocol}//${location.hostname}:8000` : location.origin)

export async function api(path, opts = {}) {
  const isForm = opts.body instanceof FormData
  const res = await fetch(API + path, {
    method: opts.method || (opts.body ? 'POST' : 'GET'),
    headers: isForm ? undefined : { 'Content-Type': 'application/json' },
    body: opts.body && !isForm && typeof opts.body !== 'string' ? JSON.stringify(opts.body) : opts.body,
  })
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  return res.json()
}

// WebSocket /live: server says "something changed"; we refetch. Reconnects on drop.
export function useLive(onChange) {
  const cb = useRef(onChange)
  cb.current = onChange
  useEffect(() => {
    let ws, timer, ping, closed = false
    const connect = () => {
      ws = new WebSocket(API.replace(/^http/, 'ws') + '/live')
      ws.onmessage = () => cb.current()
      ws.onopen = () => { cb.current(); ping = setInterval(() => ws.readyState === 1 && ws.send('ping'), 20000) }
      ws.onclose = () => { clearInterval(ping); if (!closed) timer = setTimeout(connect, 2000) }
    }
    connect()
    const poll = setInterval(() => cb.current(), 15000) // safety net if the socket is blocked
    return () => { closed = true; clearTimeout(timer); clearInterval(ping); clearInterval(poll); ws && ws.close() }
  }, [])
}

export const BAND_COLOR = { CRIT: '#c62828', HIGH: '#e65100', MED: '#f9a825', LOW: '#78909c' }

export const ACTION_LABEL = {
  dispatch: 'Dispatch: operator approves the recommendation',
  standby_and_verify: 'Send standby team now + verify by callback in parallel',
  verify_first: 'Verify first: callback + operator review (no team recommended yet)',
  callback_review: 'Callback + operator review (downranked, never deleted)',
  normal_queue: 'Normal queue: assigned as teams free up',
  need_location: 'Waiting for location: follow-up question sent',
  closed: 'Closed',
}

export const fmtTime = (s) => (s ? new Date(s).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '')
