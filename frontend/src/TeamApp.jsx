import { useCallback, useEffect, useRef, useState } from 'react'
import { api, useLive, toast, errText, BAND_COLOR, BAND_NAME, NEED_TEXT, TEAM_ICON, VULN_TEXT, flagText } from './api.js'
import RouteMap from './RouteMap.jsx'
import Toasts from './Toasts.jsx'

// Field team PWA (FR12). Offline-first: last assignment cached in localStorage,
// status taps queued locally and synced when the network returns.
const QKEY = 'rahat.queue', TKEY = 'rahat.team', CKEY = 'rahat.cache.'
const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d } catch { return d } }
const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch { /* private mode */ } }

// Field buttons: English + Hindi, big enough for wet hands.
const STATUS = {
  en_route: { en: 'On the way', hi: 'रास्ते में', icon: '🚤' },
  reached: { en: 'Reached', hi: 'पहुँच गए', icon: '📍' },
  rescued: { en: 'Rescued / Delivered', hi: 'बचा लिया / सामान दे दिया', icon: '✅' },
  need_backup: { en: 'Need backup', hi: 'और मदद चाहिए', icon: '🆘' },
  no_one_here: { en: 'No one here', hi: 'यहाँ कोई नहीं', icon: '✗' },
}
// The one next step for each assignment status; everything else is secondary.
const NEXT = { approved: 'en_route', en_route: 'reached', on_scene: 'rescued' }
const STEPS = [['approved', 'Assigned'], ['en_route', 'On the way'], ['on_scene', 'Reached'], ['done', 'Done']]
// a tap that is still waiting on the phone already moves the screen forward (optimistic)
const TAP_TO_STEP = { en_route: 'en_route', reached: 'on_scene', rescued: 'done', no_one_here: 'done' }
const hhmm = (t) => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

export default function TeamApp() {
  const [teams, setTeams] = useState([])
  // per-team link: #/team?id=BOAT-A opens that team directly (and remembers it)
  const [teamId, setTeamId] = useState(() => {
    const fromLink = new URLSearchParams(location.hash.split('?')[1] || '').get('id')
    if (fromLink) save(TKEY, fromLink)
    return fromLink || load(TKEY, '')
  })
  const [data, setData] = useState(null)
  const [queue, setQueue] = useState(load(QKEY, []))
  const [online, setOnline] = useState(navigator.onLine)
  const [ticks, setTicks] = useState({})
  const [tab, setTab] = useState('assignment')
  const lastAsg = useRef(null)

  const refresh = useCallback(() => {
    api('/teams').then(setTeams).catch(() => {})
    if (!teamId) return
    api(`/teams/${teamId}/assignment`)
      .then((d) => {
        const id = d?.assignment?.id || null
        if (id && lastAsg.current !== null && id !== lastAsg.current) {
          toast(`New job: ${d.incident.title}`, 'crit')
          navigator.vibrate?.([200, 100, 200])
        }
        lastAsg.current = id
        setData(d); save(CKEY + teamId, d); setOnline(true)
      })
      .catch(() => { setOnline(false); wasOffline.current = true; setData(load(CKEY + teamId, null)) })
  }, [teamId])
  useLive(refresh)
  useEffect(() => { lastAsg.current = null; refresh() }, [teamId, refresh])

  const wasOffline = useRef(false)
  const flushing = useRef(false)
  const flush = useCallback(async () => {
    if (flushing.current) return false            // the 10 s timer and the "online" event can fire together
    flushing.current = true
    let q = load(QKEY, [])
    let sent = 0
    try {
      while (q.length) {
        const u = q[0]
        try {
          const r = await api(`/teams/${u.team_id}/status`, { method: 'PATCH',
            body: { status: u.status, client_ts: u.client_ts, assignment_id: u.assignment_id, via: 'phone' } })
          if (r?.ignored) toast(`"${STATUS[u.status]?.en || u.status}" was already updated by the control room (radio), skipped`, 'info')
        } catch { setOnline(false); wasOffline.current = true; return false }
        q = q.slice(1); save(QKEY, q); setQueue(q); sent += 1
      }
    } finally { flushing.current = false }
    if (wasOffline.current && sent > 0) {
      toast(`✓ Back online: ${sent} saved update${sent > 1 ? 's' : ''} sent to the control room`, 'ok')
      navigator.vibrate?.(150)
    }
    wasOffline.current = false
    setOnline(true)
    refresh()
    return true
  }, [refresh])
  useEffect(() => {
    const on = () => { setOnline(true); flush() }, off = () => { setOnline(false); wasOffline.current = true }
    window.addEventListener('online', on); window.addEventListener('offline', off)
    const t = setInterval(flush, 10000)
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); clearInterval(t) }
  }, [flush])

  // live GPS: position shown on the route map and sent to the dashboard (throttled to every 15 s)
  const [myPos, setMyPos] = useState(null)
  const [watchId, setWatchId] = useState(null)
  const [gpsErr, setGpsErr] = useState('')
  const lastSent = useRef(0)
  const toggleGps = () => {
    if (watchId != null) { navigator.geolocation.clearWatch(watchId); setWatchId(null); setMyPos(null); return }
    if (!navigator.geolocation) { setGpsErr('GPS not available on this device'); return }
    const id = navigator.geolocation.watchPosition((p) => {
      const pos = [p.coords.latitude, p.coords.longitude]
      setMyPos(pos); setGpsErr('')
      if (Date.now() - lastSent.current > 15000 && teamId) {
        lastSent.current = Date.now()
        api(`/teams/${teamId}/status`, { method: 'PATCH', body: { status: 'location', lat: pos[0], lng: pos[1] } }).catch(() => {})
      }
    }, (e) => setGpsErr(`GPS: ${e.message} (phones need https or localhost for GPS)`), { enableHighAccuracy: true })
    setWatchId(id)
  }
  useEffect(() => () => watchId != null && navigator.geolocation.clearWatch(watchId), [watchId])

  const tap = async (status) => {
    if (status === 'no_one_here' && !confirm('Nobody at this location? The report will be marked false.')) return
    const q = [...load(QKEY, []), { team_id: teamId, status, client_ts: new Date().toISOString(), assignment_id: data?.assignment?.id }]
    save(QKEY, q); setQueue(q)
    const ok = await flush()
    toast(ok ? `✓ Sent: ${STATUS[status].en}` : `Saved on phone: "${STATUS[status].en}" will send when the network is back`, ok ? 'ok' : 'info')
  }
  const pickTeam = (id) => { setTeamId(id); save(TKEY, id); setData(null) }

  const a = data?.assignment, inc = data?.incident, cl = data?.checklist, me = teams.find((t) => t.id === teamId)
  const pending = queue.filter((u) => u.team_id === teamId)
  const lastStep = [...pending].reverse().map((u) => TAP_TO_STEP[u.status]).find(Boolean)
  const shown = lastStep || a?.status                     // what the team sees, including taps not sent yet
  const next = a && NEXT[shown]
  const stepIdx = a ? STEPS.findIndex(([k]) => k === shown) : -1

  return (
    <div className="phone-app">
      <header className="tbar">
        <b>RAHAT Team</b>
        {me && <span className="tme">{TEAM_ICON[me.type]} {me.name}</span>}
        {teamId && <button className="light small-btn" onClick={() => pickTeam('')}>Change team</button>}
      </header>
      {!online && (
        <div className="offline offline-box">
          <b>📴 No network: the app still works.</b>
          <span>Showing the last saved job. Taps are saved on this phone and sent by themselves when the network is back.</span>
          {pending.length > 0 && <ul>{pending.map((u, k) => <li key={k}>⏳ {STATUS[u.status]?.en || u.status} <i>saved at {hhmm(u.client_ts)}</i></li>)}</ul>}
        </div>
      )}
      {online && pending.length > 0 && <div className="offline">Sending {pending.length} saved update(s)…</div>}
      <nav className="tabs">
        <button className={tab === 'assignment' ? 'on' : ''} onClick={() => setTab('assignment')}>My job</button>
        <button className={tab === 'volunteer' ? 'on' : ''} onClick={() => setTab('volunteer')}>Report for someone</button>
      </nav>

      {tab === 'volunteer' ? <Volunteer /> : !teamId ? (
        <div className="pad">
          <h3>Which team are you?</h3>
          <p className="muted">Tap your team once. The phone remembers it.</p>
          <div className="team-pick">
            {teams.map((t) => (
              <button key={t.id} onClick={() => pickTeam(t.id)}>
                <span className="tp-icon">{TEAM_ICON[t.type] || '⛑'}</span>
                <b>{t.name}</b>
                <span className={`tp-status tp-${t.status}`}>{t.status}</span>
              </button>
            ))}
          </div>
          {teams.length === 0 && <div className="muted">Loading teams… (is the server running?)</div>}
        </div>
      ) : !a ? (
        <div className="pad standby">
          <div className="standby-icon">🕓</div>
          <h3>No job right now</h3>
          <p>Stay ready. When the control room sends you, the job appears here by itself and the phone vibrates.</p>
          {data?.team && <p className="muted">Your status: <b>{data.team.status}</b></p>}
        </div>
      ) : (
        <div className="pad">
          {shown === 'done' && pending.length > 0 && (
            <div className="saved-done">✅ Saved on phone: <b>{STATUS[pending[pending.length - 1].status]?.en}</b>. It reaches the control room as soon as there is network.</div>
          )}
          <ol className="steps">
            {STEPS.map(([k, label], n) => <li key={k} className={n < stepIdx ? 'done' : n === stepIdx ? 'now' : ''}>{label}</li>)}
          </ol>

          <div className="card job" style={{ borderLeftColor: BAND_COLOR[inc.band] }}>
            <span className="badge" style={{ background: BAND_COLOR[inc.band] }}>{BAND_NAME[inc.band]}</span>
            <h3>{inc.title}</h3>
            <div className="job-grid">
              <div><span>People</span><b>{inc.people_count ?? 'unknown'}{inc.people_estimated ? ' (est.)' : ''}</b></div>
              <div><span>Reach in</span><b>{Math.round(a.eta_min)} min</b></div>
              <div><span>Vulnerable</span><b>{inc.vulnerable.map((v) => VULN_TEXT[v] || v).join(', ') || 'none reported'}</b></div>
              <div><span>Needs</span><b>{inc.needs.map((n) => NEED_TEXT[n] || n).join(', ') || '-'}</b></div>
              {(inc.hazard_detail || inc.water_depth_ft != null) && <div><span>Hazard</span><b>{inc.hazard_detail || `water ${inc.water_depth_ft} ft`}</b></div>}
            </div>
            {inc.red_flags.length > 0 && <div className="flags small">⚑ Not verified yet: {flagText(inc.red_flags[0])}</div>}
          </div>

          {next && (
            <button className="approve-btn approve-wide next-btn" onClick={() => tap(next)}>
              {STATUS[next].icon} {STATUS[next].en}<span>{STATUS[next].hi}</span>
            </button>
          )}

          {inc.location.lat != null && <>
            <RouteMap team={data.team} incident={inc} myPos={myPos} />
            <div className="row">
              <button onClick={toggleGps}>{watchId != null ? '⏹ Stop sharing location' : '📡 Share my location'}</button>
              <a className="route" href={`https://www.google.com/maps/dir/?api=1&destination=${inc.location.lat},${inc.location.lng}${data.team.type === 'boat' ? '' : '&travelmode=driving'}`} target="_blank" rel="noreferrer">
                🧭 Open in Google Maps
              </a>
            </div>
            {gpsErr && <div className="muted small">{gpsErr}</div>}
          </>}

          <div className="status-grid">
            {['rescued', 'need_backup', 'no_one_here'].filter((k) => k !== next).map((k) => (
              <button key={k} className={k === 'no_one_here' ? 'danger' : ''} onClick={() => tap(k)}>
                {STATUS[k].icon} {STATUS[k].en}<span>{STATUS[k].hi}</span>
              </button>
            ))}
          </div>

          <h4>Safety checklist (official SOP)</h4>
          {!cl || cl.status !== 'OK' ? <div className="sop-miss">{cl?.message || 'Not in SOP, escalate to officer'}</div> : (
            <ul className="checklist ticks">
              {cl.items.map((it, n) => (
                <li key={n}><label>
                  <input type="checkbox" checked={!!ticks[n]} onChange={() => setTicks({ ...ticks, [n]: !ticks[n] })} />
                  {it.text} <span className="src">[{it.source}]</span>
                </label></li>
              ))}
            </ul>
          )}
        </div>
      )}
      <Toasts />
    </div>
  )
}

const EMPTY = { name: '', place: '', people: 1, needs: { trapped: true }, vul: {}, notes: '', lat: null, lng: null }

function Volunteer() {
  const [f, setF] = useState(EMPTY)
  const [places, setPlaces] = useState([])
  const [sending, setSending] = useState(false)
  useEffect(() => { api('/state').then((s) => setPlaces(s.places.filter((p) => p.in_area))).catch(() => {}) }, [])
  const tog = (k, v) => setF({ ...f, [k]: { ...f[k], [v]: !f[k][v] } })
  const gps = () => navigator.geolocation?.getCurrentPosition(
    (p) => { setF({ ...f, lat: p.coords.latitude, lng: p.coords.longitude }); toast('Location added ✓', 'ok') },
    (e) => toast(`GPS: ${e.message}`, 'error'))
  const submit = async () => {
    if (!f.place && f.lat == null) { toast('Choose a place or tap "Use my GPS" first', 'error'); return }
    const place = places.find((p) => p.id === f.place)
    const words = [...Object.keys(f.needs).filter((k) => f.needs[k]), ...Object.keys(f.vul).filter((k) => f.vul[k])]
    const text = `[Volunteer report${f.name ? ' by ' + f.name : ''}] ${f.people} people at ${place ? place.name : 'pinned location'}, ${words.join(', ')}. ${f.notes}`
    setSending(true)
    try {
      await api('/messages', { body: { channel: 'volunteer', sender_id: `volunteer-${f.name || 'anon'}`, raw_text: text,
        lat: f.lat ?? place?.lat ?? null, lng: f.lng ?? place?.lng ?? null } })
      toast('✓ Report sent. The control room sees it now.', 'ok')
      setF({ ...EMPTY, name: f.name })
    } catch (e) { toast(errText(e), 'error') } finally { setSending(false) }
  }
  return (
    <div className="pad form">
      <p className="muted">Reporting for someone who can't send a message themselves (ASHA worker / volunteer).</p>
      <label>Your name <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label>Where? <select value={f.place} onChange={(e) => setF({ ...f, place: e.target.value })}>
        <option value="">choose a place…</option>{places.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select></label>
      <button onClick={gps}>📡 Use my GPS {f.lat ? '✓' : ''}</button>
      <label>How many people? <input type="number" min="1" value={f.people} onChange={(e) => setF({ ...f, people: e.target.value })} /></label>
      <fieldset><legend>What do they need?</legend>{['trapped', 'medical', 'food', 'drinking water', 'medicine'].map((n) =>
        <label key={n} className="chk"><input type="checkbox" checked={!!f.needs[n]} onChange={() => tog('needs', n)} />{n}</label>)}</fieldset>
      <fieldset><legend>Anyone vulnerable?</legend>{['elderly', 'child', 'disabled', 'pregnant', 'alone'].map((n) =>
        <label key={n} className="chk"><input type="checkbox" checked={!!f.vul[n]} onChange={() => tog('vul', n)} />{n}</label>)}</fieldset>
      <label>Anything else? <textarea value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
      <button className="approve-btn approve-wide" disabled={sending} onClick={submit}>{sending ? 'Sending…' : 'Send report'}</button>
    </div>
  )
}
