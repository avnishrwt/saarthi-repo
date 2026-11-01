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
  joke_marker: 'Message reads like a joke (laughing emoji)',
  implausible_count: 'Huge number of people from a single sender',
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

// Disaster type(s) as plain words: a case can report more than one ("aag lagi" + "baadh aa gayi")
const HAZARD_WORD = { water: 'Flood', fire: 'Fire', landslide: 'Landslide', collapse: 'Building collapse' }
export function hazardNames(i) {
  const hs = [...(i.hazards || []), ...(i.hazard ? [i.hazard] : [])]
  if (!hs.length && i.location?.flooded && (i.needs || []).includes('rescue')) hs.push('water')
  return [...new Set(hs)].map((h) => [h, HAZARD_WORD[h] || h])
}

// ---------------------------------------------------------------- "why might this be fake?"
const toRad = (d) => (d * Math.PI) / 180
export function kmBetween(a, b) {
  const [la1, lo1] = a, [la2, lo2] = b
  const x = Math.sin(toRad(la2 - la1) / 2) ** 2 + Math.cos(toRad(la1)) * Math.cos(toRad(la2)) * Math.sin(toRad(lo2 - lo1) / 2) ** 2
  return 6371 * 2 * Math.asin(Math.sqrt(x))
}

// One red flag -> short label (cards, map) + plain explanation with the evidence (pop-up).
export function explainFlag(flag, inc, area) {
  const [key, rest = ''] = flag.split(/:\s*/, 2)
  if (key === 'copy_paste_burst') {
    const m = rest.match(/from (\d+) numbers within (\d+)s/)
    const n = m ? m[1] : 'several', s = m ? Number(m[2]) : null
    const span = s == null ? '' : s < 2 ? ' almost at the same moment' : ` within ${s} seconds`
    return {
      icon: '📋', short: `Same text from ${n} numbers${s == null ? '' : s < 2 ? ' at once' : ` in ${s} s`}`,
      title: 'Same text from many numbers',
      why: `Exactly the same words came from ${n} different phone numbers${span}. People in real trouble write in their own words; forwarded spam is identical.`,
    }
  }
  if (key === 'out_of_area') {
    const km = inc?.location?.lat != null && area ? Math.round(kmBetween([inc.location.lat, inc.location.lng], area.center)) : null
    return {
      icon: '📍', short: km ? `${km} km outside the flood area` : 'Outside the flood area',
      title: 'Place is outside the flood area',
      why: `${km ? `This place is about ${km} km from the centre of the affected area` : 'This place is outside the affected area'}${area ? `, and RAHAT covers ${area.radius_km} km around it` : ''}. A real emergency there belongs to another control room.`,
    }
  }
  if (key === 'low_reputation_sender') return {
    icon: '👤', short: 'Sender had false reports before', title: 'Sender had false reports before',
    why: 'A team earlier went to a report from this number and found no one there.',
  }
  if (key === 'serial_reporter') return {
    icon: '🔁', short: 'One number, many emergencies', title: 'One number, many different emergencies',
    why: 'This number reported several unrelated emergencies at different places within 30 minutes.',
  }
  if (key === 'joke_marker') return {
    icon: '😂', short: 'Reads like a joke', title: 'Message reads like a joke',
    why: 'It contains laughing emoji or joke words. Real calls for help rarely do: check before sending a team.',
  }
  if (key === 'implausible_count') return {
    icon: '🔢', short: 'Huge count, one sender', title: 'Huge number of people, only one sender',
    why: 'One number claims hundreds of people at one spot and nobody else reports it. Could be panic or a prank: call back first.',
  }
  return { icon: '⚑', short: key, title: key, why: rest || flag }
}

// confidence_reasons from the backend -> score steps on a 0-100 scale: { delta, text }
const STEP_TEXT = {
  copy_paste_burst: 'Same text from many numbers', out_of_area: 'Place is outside the flood area',
  low_reputation_sender: 'Sender had false reports before', serial_reporter: 'One number, many emergencies',
}
export function trustSteps(reasons) {
  return (reasons || []).map((r) => {
    let m = r.match(/^start neutral (\d\.\d+)/)
    if (m) return { start: true, value: Math.round(parseFloat(m[1]) * 100), text: 'Every report starts here (one lone report is not treated as fake)' }
    m = r.match(/^([+-])(\d\.\d+):\s*(.*)$/)
    if (m) {
      const delta = Math.round(parseFloat(m[2]) * 100) * (m[1] === '-' ? -1 : 1)
      let text = m[3]
      const f = text.match(/^red flag (\w+)/)
      if (f) text = STEP_TEXT[f[1]] || f[1]
      else text = text.replace(/^(\d+) independent senders$/, '$1 different people reported it independently')
        .replace('known landmark inside affected area', 'Known place inside the flood area')
        .replace('specific people count', 'Gives an exact number of people')
      return { delta, text }
    }
    if (r.startsWith('callback/operator CONFIRMED')) return { set: '≥ 90', text: 'Confirmed by phone / operator' }
    if (r.startsWith('callback said help not needed')) return { set: '≤ 15', text: 'Caller said no help is needed' }
    return { note: true, text: r.replace(/\(.*\)/, '').trim() }
  })
}
