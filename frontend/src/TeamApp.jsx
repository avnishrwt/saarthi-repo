import { useCallback, useEffect, useRef, useState } from 'react'
import { api, useLive, BAND_COLOR } from './api.js'
import RouteMap from './RouteMap.jsx'

// Field team PWA (FR12). Offline-first: last assignment cached in localStorage,
// status taps queued locally and synced when the network returns.
const QKEY = 'rahat.queue', TKEY = 'rahat.team', CKEY = 'rahat.cache.'
const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d } catch { return d } }
const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch { /* private mode */ } }

export default function TeamApp() {
  const [teams, setTeams] = useState([])
  const [teamId, setTeamId] = useState(load(TKEY, ''))
  const [data, setData] = useState(null)
  const [queue, setQueue] = useState(load(QKEY, []))
  const [online, setOnline] = useState(navigator.onLine)
  const [ticks, setTicks] = useState({})
  const [tab, setTab] = useState('assignment')

  const refresh = useCallback(() => {
    api('/teams').then(setTeams).catch(() => {})
    if (!teamId) return
    api(`/teams/${teamId}/assignment`)
      .then((d) => { setData(d); save(CKEY + teamId, d); setOnline(true) })
      .catch(() => { setOnline(false); setData(load(CKEY + teamId, null)) })
  }, [teamId])
  useLive(refresh)
  useEffect(() => { refresh() }, [teamId, refresh])

  const flush = useCallback(async () => {
    let q = load(QKEY, [])
    while (q.length) {
      const u = q[0]
      try { await api(`/teams/${u.team_id}/status`, { method: 'PATCH', body: { status: u.status, client_ts: u.client_ts } }) }
      catch { setOnline(false); break }
      q = q.slice(1); save(QKEY, q); setQueue(q)
    }
    if (!q.length) refresh()
  }, [refresh])
  useEffect(() => {
    const on = () => { setOnline(true); flush() }, off = () => setOnline(false)
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

  const tap = (status) => {
    const q = [...load(QKEY, []), { team_id: teamId, status, client_ts: new Date().toISOString() }]
    save(QKEY, q); setQueue(q); flush()
  }

  const a = data?.assignment, inc = data?.incident, cl = data?.checklist
  return (
    <div className="phone-app">
      <header className="tbar">
        <b>RAHAT Team</b>
        <select value={teamId} onChange={(e) => { setTeamId(e.target.value); save(TKEY, e.target.value) }}>
          <option value="">Select team…</option>
          {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </header>
      {!online && <div className="offline">Offline: showing last saved assignment. {queue.length} update(s) will sync.</div>}
      {online && queue.length > 0 && <div className="offline">Syncing {queue.length} update(s)…</div>}
      <nav className="tabs">
        <button className={tab === 'assignment' ? 'on' : ''} onClick={() => setTab('assignment')}>Assignment</button>
        <button className={tab === 'volunteer' ? 'on' : ''} onClick={() => setTab('volunteer')}>Report for someone</button>
      </nav>
      {tab === 'volunteer' ? <Volunteer /> : !teamId ? <p className="pad">Pick your team above.</p> : !a ? (
        <p className="pad">No assignment. Stay on standby; this screen updates by itself.
          {data?.team && <><br /><span className="muted">Status: {data.team.status}</span></>}</p>
      ) : (
        <div className="pad">
          <div className="card">
            <span className="badge" style={{ background: BAND_COLOR[inc.band] }}>{inc.band}</span> <b>{inc.id}</b>
            <span className="muted"> · {a.status}</span>
            <h3>{inc.title}</h3>
            <div>People: <b>{inc.people_count ?? 'unknown'}{inc.people_estimated ? ' (est.)' : ''}</b></div>
            <div>Vulnerable: <b>{inc.vulnerable.join(', ') || 'none reported'}</b></div>
            <div>Hazard: <b>{inc.hazard_detail || inc.hazard || 'none reported'}</b></div>
            <div>ETA: <b>{Math.round(a.eta_min)} min</b></div>
            {inc.red_flags.length > 0 && <div className="flags small">⚑ Unverified report: {inc.red_flags[0]}</div>}
          </div>
          {inc.location.lat != null && <>
            <RouteMap team={data.team} incident={inc} myPos={myPos} />
            <div className="row">
              <button onClick={toggleGps}>{watchId != null ? 'Stop sharing location' : 'Share my location'}</button>
              <a className="route" href={`https://www.google.com/maps/dir/?api=1&destination=${inc.location.lat},${inc.location.lng}${data.team.type === 'boat' ? '' : '&travelmode=driving'}`} target="_blank" rel="noreferrer">
                Navigate in Google Maps
              </a>
            </div>
            {gpsErr && <div className="muted small">{gpsErr}</div>}
          </>}
          <div className="status-grid">
            {a.status === 'approved' && <button onClick={() => tap('en_route')}>On the way</button>}
            <button onClick={() => tap('reached')}>Reached</button>
            <button className="primary" onClick={() => tap('rescued')}>Rescued / Delivered</button>
            <button className="danger" onClick={() => tap('no_one_here')}>No one here</button>
            <button onClick={() => tap('need_backup')}>Need backup</button>
          </div>
          <h4>SOP checklist</h4>
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
    </div>
  )
}

function Volunteer() {
  const [f, setF] = useState({ name: '', place: '', people: 1, needs: { trapped: true }, vul: {}, notes: '', lat: null, lng: null })
  const [places, setPlaces] = useState([])
  const [done, setDone] = useState('')
  useEffect(() => { api('/state').then((s) => setPlaces(s.places.filter((p) => p.in_area))).catch(() => {}) }, [])
  const tog = (k, v) => setF({ ...f, [k]: { ...f[k], [v]: !f[k][v] } })
  const gps = () => navigator.geolocation?.getCurrentPosition((p) => setF({ ...f, lat: p.coords.latitude, lng: p.coords.longitude }))
  const submit = async () => {
    const place = places.find((p) => p.id === f.place)
    const words = [...Object.keys(f.needs).filter((k) => f.needs[k]), ...Object.keys(f.vul).filter((k) => f.vul[k])]
    const text = `[Volunteer report${f.name ? ' by ' + f.name : ''}] ${f.people} people at ${place ? place.name : 'pinned location'}, ${words.join(', ')}. ${f.notes}`
    await api('/messages', { body: { channel: 'volunteer', sender_id: `volunteer-${f.name || 'anon'}`, raw_text: text,
      lat: f.lat ?? place?.lat ?? null, lng: f.lng ?? place?.lng ?? null } })
    setDone('Sent. The control room sees it as an incident.')
  }
  return (
    <div className="pad form">
      <label>Your name <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <label>Place <select value={f.place} onChange={(e) => setF({ ...f, place: e.target.value })}>
        <option value="">choose…</option>{places.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select></label>
      <button onClick={gps}>Use my GPS {f.lat ? '✓' : ''}</button>
      <label>People <input type="number" min="1" value={f.people} onChange={(e) => setF({ ...f, people: e.target.value })} /></label>
      <fieldset><legend>Needs</legend>{['trapped', 'medical', 'food', 'drinking water', 'medicine'].map((n) =>
        <label key={n} className="chk"><input type="checkbox" checked={!!f.needs[n]} onChange={() => tog('needs', n)} />{n}</label>)}</fieldset>
      <fieldset><legend>Vulnerable</legend>{['elderly', 'child', 'disabled', 'pregnant', 'alone'].map((n) =>
        <label key={n} className="chk"><input type="checkbox" checked={!!f.vul[n]} onChange={() => tog('vul', n)} />{n}</label>)}</fieldset>
      <label>Notes <textarea value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
      <button className="primary" onClick={submit}>Send report</button>
      {done && <div className="ok">{done}</div>}
    </div>
  )
}
