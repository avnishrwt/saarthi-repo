// Backend base URL: Vite dev server (5173) talks to FastAPI on :8000 of the same host,
// so a phone on the same Wi-Fi works with http://<laptop-ip>:5173. Built app served by
// FastAPI at /ui uses the same origin. Override with VITE_API.
import { useEffect, useRef } from 'react'

export const API =
  import.meta.env.VITE_API ||
  (location.port === '5173' ? `${location.protocol}//${location.hostname}:8000` : location.origin)

export async function api(path, opts = {}) {
  const isForm = opts.body instanceof FormData
  let res
  try {
    res = await fetch(API + path, {
      method: opts.method || (opts.body ? 'POST' : 'GET'),
      headers: isForm ? undefined : { 'Content-Type': 'application/json' },
      body: opts.body && !isForm && typeof opts.body !== 'string' ? JSON.stringify(opts.body) : opts.body,
    })
  } catch {
    throw new Error("Can't reach the RAHAT server. Is the backend running?")
  }
  if (!res.ok) {
    // FastAPI errors look like {"detail": "..."}; show that sentence, not the raw JSON
    const txt = await res.text()
    let msg = txt
    try { const j = JSON.parse(txt); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j) } catch { /* plain text */ }
    throw new Error(`${msg || 'Request failed'} (${res.status})`)
  }
  return res.json()
}

// Small pop-up notifications. Any screen calls toast('text'); <Toasts/> shows them.
export function toast(text, kind = 'info', onClick) {
  window.dispatchEvent(new CustomEvent('rahat-toast', { detail: { text, kind, onClick } }))
}
export const errText = (e) => String(e?.message || e).replace(/^Error: /, '')

// WebSocket /live: server says "something changed"; we refetch. Reconnects on drop.
export function useLive(onChange, onStatus) {
  const cb = useRef(onChange)
  const st = useRef(onStatus)
  cb.current = onChange
  st.current = onStatus
  useEffect(() => {
    let ws, timer, ping, closed = false
    const connect = () => {
      ws = new WebSocket(API.replace(/^http/, 'ws') + '/live')
      ws.onmessage = () => cb.current()
      ws.onopen = () => { st.current?.(true); cb.current(); ping = setInterval(() => ws.readyState === 1 && ws.send('ping'), 20000) }
      ws.onclose = () => { st.current?.(false); clearInterval(ping); if (!closed) timer = setTimeout(connect, 2000) }
    }
    connect()
    const poll = setInterval(() => cb.current(), 15000) // safety net if the socket is blocked
    return () => { closed = true; clearTimeout(timer); clearInterval(ping); clearInterval(poll); ws && ws.close() }
  }, [])
}

export const BAND_COLOR = { CRIT: '#dc2626', HIGH: '#ea580c', MED: '#d97706', LOW: '#64748b' }

export const BAND_NAME = { CRIT: 'Critical', HIGH: 'High', MED: 'Medium', LOW: 'Low' }

// Plain-language labels: the dashboard must be readable by someone seeing it for the first time.
export const ACTION_LABEL = {
  dispatch: 'Send a team: approve the suggested team',
  standby_and_verify: 'Send a team now and confirm by phone at the same time',
  verify_first: 'Check first: call the sender before sending a team (looks suspicious)',
  callback_review: 'Low trust: call back and review (kept, never deleted)',
  normal_queue: 'In queue: a team is assigned as soon as one is free',
  need_location: 'Location unknown: we asked the sender where they are',
  closed: 'Closed',
}

const FLAG_TEXT = {
  copy_paste_burst: 'Same text sent from many numbers (copy-paste)',
  out_of_area: 'Location is outside the flood area',
  low_reputation_sender: 'Sender had false reports before',
  serial_reporter: 'Same number reported many different emergencies',
}
export const flagText = (f) => FLAG_TEXT[f.split(':')[0]] || f

export const CALLBACK_TEXT = {
  none: 'not needed', not_needed: 'not needed (confirmed by several people)', pending: 'call pending',
  confirmed: 'caller confirmed', denied: 'caller says no help needed', no_answer: 'no answer',
}

export const ASSIGN_TEXT = { recommended: 'suggested', approved: 'approved', en_route: 'on the way', on_scene: 'reached', done: 'done' }
export const TEAM_ICON = { boat: '⛵', medical: '✚', relief: '🚚', fire: '🔥', search_rescue: '⛑' }
export const NEED_TEXT = {
  rescue: 'rescue', medical: 'medical', food: 'food', water: 'drinking water', medicine: 'medicine',
  shelter: 'shelter', evacuation: 'evacuation', welfare_check: 'welfare check', information: 'information',
}
export const VULN_TEXT = { elderly: 'elderly', child: 'child', disabled: 'disabled', pregnant: 'pregnant', alone: 'alone' }

export const ago = (s) => {
  if (!s) return ''
  const m = Math.round((Date.now() - new Date(s).getTime()) / 60000)
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : `${Math.floor(m / 60)} h ${m % 60} min ago`
}

export const fmtTime = (s) => (s ? new Date(s).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '')

// One emoji per case type, used on the map and the cards. Most specific danger first.
export const KIND = [
  ['fire', '🔥', 'Fire'],
  ['landslide', '⛰️', 'Landslide'],
  ['collapse', '🏚️', 'Building collapse'],
  ['medical', '🚑', 'Medical emergency'],
  ['flood', '🌊', 'Flood / water'],
  ['rescue', '🆘', 'Rescue'],
  ['medicine', '💊', 'Medicine'],
  ['food', '🍲', 'Food'],
  ['water', '💧', 'Drinking water'],
  ['shelter', '⛺', 'Shelter'],
  ['check', '📞', 'Welfare check'],
  ['other', '❗', 'Other help'],
]
export function caseKind(i) {
  const n = new Set(i.needs || [])
  if (i.hazard === 'fire') return KIND[0]
  if (i.hazard === 'landslide') return KIND[1]
  if (i.hazard === 'collapse') return KIND[2]
  if (i.medical === 'serious' || i.medical === 'life_threatening') return KIND[3]
  if (i.hazard === 'water' || (n.has('rescue') && i.location?.flooded)) return KIND[4]
  if (n.has('rescue') || n.has('evacuation')) return KIND[5]
  if (n.has('medical')) return KIND[3]
  if (n.has('medicine')) return KIND[6]
  if (n.has('food')) return KIND[7]
  if (n.has('water')) return KIND[8]
  if (n.has('shelter')) return KIND[9]
  if (n.has('welfare_check')) return KIND[10]
  return KIND[11]
}
