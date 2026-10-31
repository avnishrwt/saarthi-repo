import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fmtTime } from './api.js'

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
  const [sender, setSender] = useState(rnd())
  const [channel, setChannel] = useState('sms')
  const [text, setText] = useState('')
  const [inbox, setInbox] = useState([])
  const [log, setLog] = useState([])
  const [state, setState] = useState(null)
  const [ivrPlace, setIvrPlace] = useState('RISPANA_BRIDGE')
  const [rec, setRec] = useState(null)
  const chunks = useRef([])

  const note = (s) => setLog((l) => [`${new Date().toLocaleTimeString()} ${s}`, ...l].slice(0, 12))
  const refresh = useCallback(() => {
    api(`/sim/outbox?sender_id=${encodeURIComponent(sender)}`).then(setInbox).catch(() => {})
    api('/state').then(setState).catch(() => {})
  }, [sender])
  useEffect(() => { refresh(); const t = setInterval(refresh, 3000); return () => clearInterval(t) }, [refresh])

  const send = async (t = text, s = sender, c = channel) => {
    if (!t.trim()) return
    const r = await api('/messages', { body: { channel: c, sender_id: s, raw_text: t } })
    note(`sent ${r.id} (${c}) from ${s}`)
    setText('')
  }
  const preset = (p) => {
    const s = p.sender === 'RANDOM' ? rnd() : p.sender
    setSender(s); setChannel(p.channel); setText(p.text)
  }
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
        <div className="phone-top">Citizen phone <span className="muted small">(simulated)</span></div>
        <label className="small">From <input value={sender} onChange={(e) => setSender(e.target.value)} /></label>
        <div className="row">
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="sms">SMS</option><option value="whatsapp">WhatsApp</option><option value="helpline">112 transcript</option>
          </select>
          <button onClick={() => setSender(rnd())}>new number</button>
        </div>
        <textarea rows={4} placeholder="Type any Hinglish / Hindi / English message…" value={text} onChange={(e) => setText(e.target.value)} />
        <button className="primary wide" onClick={() => send()}>Send</button>
        <div className="presets">{PRESETS.map((p) => <button key={p.label} onClick={() => preset(p)}>{p.label}</button>)}</div>
        <div className="row">
          {rec ? <button className="danger" onClick={() => { rec.stop(); setRec(null) }}>Stop + send voice note</button>
            : <button onClick={startRec}>Record voice note</button>}
          <label className="upload">Upload audio<input type="file" accept="audio/*" onChange={(e) => e.target.files[0] && upload(e.target.files[0], e.target.files[0].name)} /></label>
        </div>
        <h4>Inbox (replies from RAHAT)</h4>
        <div className="inbox">
          {inbox.length === 0 ? <div className="muted small">No messages yet.</div> :
            inbox.map((m, k) => <div key={k} className="sms"><span className="muted small">{fmtTime(m.at)} · {m.channel}</span><div>{m.text}</div></div>)}
        </div>
      </div>

      <div className="simcol">
        <h3>Simulated calls</h3>
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
        <button onClick={fakeBurst}>Send fake from 6 new numbers</button>

        <h3>Proactive check (opt-in vulnerable list)</h3>
        <button onClick={() => api('/sim/proactive/start', { body: {} }).then(() => note('proactive calls started'))}>Start calls</button>
        {(state?.residents || []).map((r) => (
          <div key={r.id} className="row">
            <span>{r.name}, {r.age}, {r.area}: <b>{r.last_check_result || '-'}</b></span>
            {['no_answer', 'ok', 'need_help'].map((res) =>
              <button key={res} onClick={() => api(`/sim/proactive/${r.id}`, { body: { result: res } }).then(() => note(`${r.name}: ${res}`))}>{res}</button>)}
          </div>
        ))}

        <h3>Pending callbacks</h3>
        {callbacks.length === 0 && <div className="muted small">none</div>}
        {callbacks.map((i) => (
          <div key={i.id} className="row">
            <span className="small"><b>{i.id}</b> {i.title}</span>
            {['confirmed', 'no_answer', 'denied'].map((res) =>
              <button key={res} onClick={() => api(`/incidents/${i.id}/verify`, { body: { result: res, by: 'callback' } }).then(() => note(`${i.id} callback: ${res}`))}>{res}</button>)}
          </div>
        ))}
        <h3>Log</h3>
        <div className="small mono">{log.map((l, k) => <div key={k}>{l}</div>)}</div>
      </div>
    </div>
  )
}
