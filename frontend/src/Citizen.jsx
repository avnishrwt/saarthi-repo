import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fmtTime, toast, errText, CALLBACK_TEXT } from './api.js'
import Toasts from './Toasts.jsx'

// Citizen simulator (brief section 5): phone-shaped page for live demo moments.
// Everything hits the same API as a real SMS gateway / 112 feed would.
const PRESETS = [
  { label: 'Dadi (relative 1)', sender: 'S-dadi-1', channel: 'whatsapp', text: 'Bhai Deep Nagar gali 3 mein meri dadi phasi hai, paani 5 feet hai' },
  { label: 'Ramesh uncle (lone)', sender: 'S-ramesh', channel: 'sms', text: 'main akela hu, 75 saal, ghar me paani aa gaya, kanwali mandir ke peeche' },
  { label: 'Fake (copy-paste)', sender: 'RANDOM', channel: 'sms', text: 'URGENT 50 log phase hai Har ki Pauri pe' },
  { label: 'Vague food', sender: 'S-vague-9', channel: 'sms', text: 'khana chahiye 4-5 log hai' },
  { label: 'Hindi', sender: 'S-hindi-1', channel: 'sms', text: 'नेहरू कॉलोनी में पानी भर गया है, दादी फंसी है' },
]
const rnd = () => '+9199' + Math.floor(10000000 + Math.random() * 89999999)

export default function Citizen() {
  // #/citizen?from=S-dadi-1 opens that phone directly (handy to show one person's conversation)
  const [sender, setSender] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('from') || rnd())
  const [channel, setChannel] = useState('sms')
  const [text, setText] = useState('')
  const [chat, setChat] = useState([])          // this phone's conversation, from the backend
  const [log, setLog] = useState([])
  const chatEnd = useRef(null)
  const [state, setState] = useState(null)
  const [ivrPlace, setIvrPlace] = useState('RISPANA_BRIDGE')
  const [rec, setRec] = useState(null)
  const chunks = useRef([])
  const [liveChats, setLiveChats] = useState([])   // REAL WhatsApp phones (Cloud API webhook)
  const [live, setLive] = useState(null)           // the real chat being viewed: { sender_hash, label }
  const knownLive = useRef(null)

  const note = (s) => setLog((l) => [`${new Date().toLocaleTimeString()} ${s}`, ...l].slice(0, 12))
  const refresh = useCallback(() => {
    api('/sim/live_chats').then((xs) => {
      if (knownLive.current) {
        xs.filter((c) => !knownLive.current.has(c.sender_hash)).forEach((c) =>
          toast(`🟢 New real WhatsApp message from ${c.label}`, 'crit', () => setLive(c)))
      }
      knownLive.current = new Set(xs.map((c) => c.sender_hash))
      setLiveChats(xs)
    }).catch(() => {})
    if (live) {
      api(`/sim/conversation?sender_hash=${encodeURIComponent(live.sender_hash)}`).then(setChat).catch(() => {})
      api('/state').then(setState).catch(() => {})
      return
    }
    api(`/sim/conversation?sender_id=${encodeURIComponent(sender)}`).then(setChat).catch(() =>
      // older backend without /sim/conversation: show at least RAHAT's replies
      api(`/sim/outbox?sender_id=${encodeURIComponent(sender)}`)
        .then((xs) => setChat(xs.map((m) => ({ ...m, mine: false })).reverse())).catch(() => {}))
    api('/state').then(setState).catch(() => {})
  }, [sender, live])
  useEffect(() => { refresh(); const t = setInterval(refresh, 2000); return () => clearInterval(t) }, [refresh])

  const send = async (t = text, s = sender, c = channel) => {
    if (!t.trim()) return
    try {
      const r = await api('/messages', { body: { channel: c, sender_id: s, raw_text: t } })
      note(`sent ${r.id} (${c}) from ${s}`)
      if (s === sender) setText('')
      setTimeout(refresh, 400)      // show the message + RAHAT's acknowledgement right away
    } catch (e) { toast(errText(e), 'error') }
  }
  // one tap: switch to that person's phone and send their message
  const preset = (p) => {
    const s = p.sender === 'RANDOM' ? rnd() : p.sender
    setLive(null); setSender(s); setChannel(p.channel); setText('')
    send(p.text, s, p.channel)
  }
  useEffect(() => { chatEnd.current?.scrollIntoView({ block: 'nearest' }) }, [chat.length])
  const fakeBurst = async () => {
    for (let k = 0; k < 6; k++) await send('URGENT 50 log phase hai Har ki Pauri pe', rnd(), 'sms')
  }

  const startRec = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      chunks.current = []
      mr.ondataavailable = (e) => chunks.current.push(e.data)
      mr.onstop = () => { stream.getTracks().forEach((t) => t.stop()); upload(new Blob(chunks.current, { type: 'audio/webm' }), 'note.webm') }
      mr.start(); setRec(mr)
    } catch (e) { note('microphone not available: ' + e.message) }
  }
  const upload = async (blob, name) => {
    const fd = new FormData()
    fd.append('file', blob, name); fd.append('sender_id', sender); fd.append('channel', 'voice')
    const r = await api('/messages/voice', { body: fd })
    note(`voice note ${r.id} uploaded`)
  }

  const callbacks = (state?.incidents || []).filter((i) => i.callback.status === 'pending' && ['open', 'assigned'].includes(i.status))
  return (
    <div className="citizen">
      <div className="phone">
        <div className="phone-top">{live ? <>🟢 Real WhatsApp chat <span className="muted small">(live)</span></>
          : <>📱 Citizen phone <span className="muted small">(simulated)</span></>}</div>
        <div className={`chat-head ${live ? 'is-live' : ''}`}>
          <div className="chat-avatar">R</div>
          <div><b>RAHAT Helpline</b><div className="small muted">{live ? live.label : `from ${sender}`}</div></div>
          {live && <button className="chat-back" onClick={() => { setLive(null); setChat([]) }}>← Simulator</button>}
        </div>
        <div className="chat">
          {chat.length === 0 && <div className="chat-empty">Type a message for help, or tap an example below.</div>}
          {chat.map((m, k) => (
            <div key={k} className={`bubble ${m.mine ? 'mine' : 'theirs'}`}>{m.text}<span>{fmtTime(m.at)}</span></div>
          ))}
          <div ref={chatEnd} />
        </div>
        {live ? (
          <div className="composer live-note">This person types on their own phone. RAHAT's replies go to their WhatsApp automatically.</div>
        ) : (
          <div className="composer">
            <textarea rows={2} placeholder="Type in Hinglish / Hindi / English…  (Enter = send)" value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
            <button className="send-btn" onClick={() => send()} aria-label="send">➤</button>
          </div>
        )}
        <div className="small muted" style={{ marginTop: 8 }}>Quick examples (one tap sends):</div>
        <div className="presets">{PRESETS.map((p) => <button key={p.label} onClick={() => preset(p)}>{p.label}</button>)}</div>
        <details className="small"><summary>Phone settings</summary>
          <label className="small">Number <input value={sender} onChange={(e) => setSender(e.target.value)} /></label>
          <div className="row">
            <select value={channel} onChange={(e) => setChannel(e.target.value)}>
              <option value="sms">SMS</option><option value="whatsapp">WhatsApp</option><option value="helpline">112 transcript</option>
            </select>
            <button onClick={() => setSender(rnd())}>New number</button>
          </div>
        </details>
        <div className="row">
          {rec ? <button className="danger" onClick={() => { rec.stop(); setRec(null) }}>Stop + send voice note</button>
            : <button onClick={startRec}>Record voice note</button>}
          <label className="upload">Upload audio<input type="file" accept="audio/*" onChange={(e) => e.target.files[0] && upload(e.target.files[0], e.target.files[0].name)} /></label>
        </div>
      </div>

      <div className="simcol">
        <p><a href="#/">← Back to control room</a></p>
        <div className="livebox">
          <h3>🟢 Real WhatsApp messages <span className="muted small">(from real phones, live)</span></h3>
          {liveChats.length === 0 ? (
            <div className="muted small">None yet. When someone messages the RAHAT WhatsApp number, the chat appears here
              and the case appears on the dashboard at the same time.</div>
          ) : liveChats.map((c) => {
            const inc = (state?.incidents || []).find((i) => i.id === c.incident_id)
            return (
              <button key={c.sender_hash} className={`livechat ${live?.sender_hash === c.sender_hash ? 'on' : ''}`} onClick={() => setLive(c)}>
                <b>{c.label}</b> <span className="muted small">· {c.count} msg · {fmtTime(c.last_at)}</span>
                <div className="small">{c.last_text}</div>
                {inc && <div className="small livechat-case">→ on dashboard as {inc.id}: {inc.title} ({inc.status})</div>}
              </button>
            )
          })}
        </div>
        <h3>Simulated calls</h3>
        <p className="muted small">For people without smartphones: a missed call or keypad answer also becomes a case.</p>
        <div className="row">
          <button onClick={() => api('/sim/missed_call', { body: { sender_id: sender } }).then(() => note('missed call -> IVR callback placed'))}>Missed call from this number</button>
        </div>
        <div className="row">
          IVR answer at
          <select value={ivrPlace} onChange={(e) => setIvrPlace(e.target.value)}>
            {(state?.places || []).filter((p) => p.in_area).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          {[['1', 'rescue'], ['2', 'medical'], ['3', 'food']].map(([d, n]) =>
            <button key={d} onClick={() => api('/sim/ivr', { body: { sender_id: sender, digit: d, place_id: ivrPlace } }).then(() => note(`IVR pressed ${d} (${n})`))}>Press {d}: {n}</button>)}
        </div>
        <p><button onClick={fakeBurst}>Send a fake message from 6 new numbers</button> <span className="muted small">shows how RAHAT flags copy-paste fakes</span></p>

        <h3>Calls to registered elderly residents</h3>
        <p className="muted small">After a flood alert RAHAT calls people on the opt-in list. "No answer" creates a case.</p>
        <button onClick={() => api('/sim/proactive/start', { body: {} }).then(() => note('proactive calls started'))}>Start calls</button>
        {(state?.residents || []).map((r) => (
          <div key={r.id} className="row">
            <span>{r.name}, {r.age}, {r.area}: <b>{r.last_check_result || '-'}</b></span>
            {[['no_answer', 'No answer'], ['ok', 'Says OK'], ['need_help', 'Needs help']].map(([res, label]) =>
              <button key={res} onClick={() => api(`/sim/proactive/${r.id}`, { body: { result: res } }).then(() => note(`${r.name}: ${label}`))}>{label}</button>)}
          </div>
        ))}

        <h3>Verification calls waiting</h3>
        {callbacks.length === 0 && <div className="muted small">None right now.</div>}
        {callbacks.map((i) => (
          <div key={i.id} className="row">
            <span className="small"><b>{i.id}</b> {i.title}</span>
            {['confirmed', 'no_answer', 'denied'].map((res) =>
              <button key={res} onClick={() => api(`/incidents/${i.id}/verify`, { body: { result: res, by: 'callback' } }).then(() => note(`${i.id}: ${CALLBACK_TEXT[res]}`))}>{CALLBACK_TEXT[res]}</button>)}
          </div>
        ))}
        <h3>What happened</h3>
        <div className="small mono">{log.map((l, k) => <div key={k}>{l}</div>)}</div>
      </div>
      <Toasts />
    </div>
  )
}
