import { useCallback, useEffect, useRef, useState } from 'react'
import { api, useLive, toast, errText, explainFlag, BAND_COLOR, BAND_NAME, ASSIGN_TEXT, TEAM_ICON, VULN_TEXT, ago, fmtTime, hazardNames } from './api.js'
import Toasts from './Toasts.jsx'
import { flushOpQueue, opQueue, setSimOffline, simOffline } from './opqueue.js'
import MapView from './MapView.jsx'
import IncidentPanel from './IncidentPanel.jsx'

// What each Surge state means, in one line a first-time viewer can read.
const SURGE = {
  off: { label: 'Normal mode', sub: 'No flood alert. Messages are handled one by one.' },
  standby: { label: 'Flood alert: standby', sub: 'Official alert received. Calling registered elderly residents. Surge starts if messages spike.' },
  pending_confirm: { label: 'Message spike: confirm Surge?', sub: 'Many messages in 5 min but no official alert. The duty officer must confirm.' },
  on: { label: 'SURGE MODE ON', sub: 'Alert + message spike. Every message is triaged automatically and teams are suggested.' },
}

// Each live incident goes into the first group it matches: "what does the operator do next?"
const GROUPS = [
  { key: 'decide', icon: '✋', title: 'Approve a team', hint: 'RAHAT suggested a team. Check and approve.' },
  { key: 'waiting', icon: '⏳', title: 'Waiting for a free team', hint: 'All capable teams are busy. Assigned when one frees up.' },
  { key: 'verify', icon: '🔎', title: 'Check before sending', hint: 'Looks suspicious. Call the sender first.' },
  { key: 'location', icon: '📍', title: 'Location unknown', hint: 'We asked the sender where they are.' },
  { key: 'onway', icon: '🚤', title: 'Team on the way', hint: 'Approved. The team updates status from the field app.' },
]

export default function Dashboard() {
  const [state, setState] = useState(null)
  const [sel, setSel] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('inc'))   // deep link: #/?inc=INC-003
  const [version, setVersion] = useState(0)
  const [cmp, setCmp] = useState(null)
  const [err, setErr] = useState('')
  const [demoOpen, setDemoOpen] = useState(false)
  const [busy, setBusy] = useState('')
  const [tab, setTab] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('tab') || 'cases')   // cases | activity | teams
  const [q, setQ] = useState('')              // search in cases
  const [help, setHelp] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').has('help'))   // "How it works" pop-up; #/?help opens it
  const [wsUp, setWsUp] = useState(true)
  const [netOk, setNetOk] = useState(navigator.onLine)     // browser network + last request to the server
  const connected = wsUp && netOk
  useEffect(() => {
    const on = () => setNetOk(true), off = () => setNetOk(false)
    window.addEventListener('online', on); window.addEventListener('offline', off)
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off) }
  }, [])
  const [opPending, setOpPending] = useState(() => opQueue().length)   // radio updates waiting for the server
  const [sim, setSim] = useState(simOffline)                          // demo: "control room offline" switch
  useEffect(() => {
    const upd = () => { setOpPending(opQueue().length); setSim(simOffline()) }
    window.addEventListener('rahat-opqueue', upd)
    const t = setInterval(flushOpQueue, 10000)
    flushOpQueue()
    return () => { window.removeEventListener('rahat-opqueue', upd); clearInterval(t) }
  }, [])
  useEffect(() => { if (connected) flushOpQueue() }, [connected])
  const seen = useRef(null)        // incident id -> message count already announced
  const fresh = useRef(new Set())  // incidents that arrived while the page was open (highlighted once)

  // Announce new incidents and new messages merged into existing ones (the live WhatsApp moment).
  const announce = useCallback((st) => {
    const prev = seen.current
    seen.current = new Map(st.incidents.map((i) => [i.id, i.message_ids.length]))
    if (!prev) return                                   // first load: nothing is "new"
    st.incidents.forEach((i) => {
      const was = prev.get(i.id)
      if (was == null) {
        fresh.current.add(i.id)
        const real = (st.live_incidents || []).includes(i.id) ? '🟢 Real WhatsApp · ' : ''
        toast(`${real}New ${BAND_NAME[i.band].toLowerCase()} case: ${i.title}`, i.band === 'CRIT' || real ? 'crit' : 'info', () => setSel(i.id))
      } else if (i.message_ids.length > was) {
        toast(`Another message about "${i.title}" (now ${i.message_ids.length}, merged, not duplicated)`, 'info', () => setSel(i.id))
      }
    })
  }, [])

  const refresh = useCallback(() => {
    api('/state').then((s) => { announce(s); setState(s); setVersion((v) => v + 1); setErr(''); setNetOk(true) })
      .catch((e) => { setErr(errText(e)); if (/reach the RAHAT server/.test(errText(e))) setNetOk(false) })
  }, [announce])
  useLive(refresh, setWsUp)
  const onSelect = useCallback((id) => setSel(id), [])
  useEffect(() => {
    const esc = (e) => { if (e.key === 'Escape') { setSel(null); setCmp(null); setDemoOpen(false); setHelp(false) } }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [])

  if (!state) return (
    <div className="loading">
      <div className="spinner" />
      <div>Connecting to the RAHAT server…</div>
      {err && <div className="err">{err}<div className="small">Start it with: .venv\Scripts\python -m uvicorn backend.main:app --port 8000</div></div>}
    </div>
  )
  const s = state.surge
  const teams = Object.fromEntries(state.teams.map((t) => [t.id, t]))
  const placeName = Object.fromEntries(state.places.map((p) => [p.id, p.name]))
  const byInc = {}
  state.assignments.forEach((a) => { (byInc[a.incident_id] ||= []).push(a) })
  const live = state.incidents.filter((i) => i.status === 'open' || i.status === 'assigned')
  const closed = state.incidents.filter((i) => i.status === 'resolved' || i.status === 'false')

  const groups = Object.fromEntries(GROUPS.map((g) => [g.key, []]))
  const needle = q.trim().toLowerCase()
  const matches = (i) => !needle || `${i.title} ${placeName[i.location.place_id] || ''} ${i.location.text || ''} ${i.id}`.toLowerCase().includes(needle)
  live.filter(matches).forEach((i) => {
    const as = (byInc[i.id] || []).filter((a) => a.status !== 'done')
    const act = as.find((a) => a.status !== 'recommended')
    const rec = as.find((a) => a.status === 'recommended')
    const row = { i, act, rec }
    if (act) groups.onway.push(row)
    else if (rec) groups.decide.push(row)
    else if (i.action === 'verify_first' || i.action === 'callback_review') groups.verify.push(row)
    else if (i.action === 'need_location' || i.location.lat == null) groups.location.push(row)
    else groups.waiting.push(row)
  })

  const run = (p, ok) => p.then(() => { if (ok) toast(ok, 'ok'); refresh() }).catch((e) => toast(errText(e), 'error'))
  const demo = (fn) => { setDemoOpen(false); fn() }
  const approve = (e, a, inc) => {
    e.stopPropagation()
    setBusy(a.id)
    run(api(`/assignments/${a.id}/approve`, { body: { action: 'approve', operator: 'operator-1' } }),
      `✓ ${teams[a.team_id]?.name || a.team_id} sent to: ${inc.title}. The sender gets an update.`).finally(() => setBusy(''))
  }
  const startDemo = async (scenario = 'demo_scenario.json', label = '18 messages arriving over the next ~10 s') => {
    try {
      await api('/sim/alert', { body: {} })
      await api('/sim/replay', { body: { speed: 4, scenario } })
      toast(`Demo started: flood alert received, ${label}…`, 'ok')
      refresh()
    } catch (e) { toast(errText(e), 'error') }
  }
  const startNight50 = () => startDemo('demo_scenario_dehradun50.json', '50 synthetic Dehradun messages arriving over ~50 s')
  const freeTeams = state.teams.filter((t) => t.status === 'free').length
  const jump = (key) => {
    setTab('cases'); setQ('')
    setTimeout(() => document.getElementById(`grp-${key}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50)
  }
  const surgeInfo = SURGE[s.mode] || SURGE.off

  return (
    <div className="dash">
      <header>
        <div className="brandbox">
          <div className="logo" aria-hidden="true">🛟</div>
          <div className="brand">RAHAT <span className={`live ${connected ? 'on' : 'off'}`} title={connected ? 'Updates arrive instantly' : 'Connection lost, retrying'}>{connected ? '● Live' : '○ Reconnecting…'}</span>
            <span className="brand-sub">Flood control room · {state.district}</span></div>
        </div>
        <div className={`surge surge-${s.mode}`} title={`Reason: ${s.reason}`}>
          <span className="surge-dot" />
          <div><b>{surgeInfo.label}</b><span>{surgeInfo.sub}</span></div>
        </div>
        <div className="tools">
          {s.mode !== 'on'
            ? <button className="light" onClick={() => run(api('/surge', { body: { on: true, actor: 'duty_officer' } }), 'Surge Mode is ON')}>{s.mode === 'pending_confirm' ? 'Confirm Surge' : 'Turn Surge ON'}</button>
            : <button className="light" onClick={() => run(api('/surge', { body: { on: false, actor: 'duty_officer' } }), 'Surge Mode is OFF')}>Turn Surge OFF</button>}
          <button className="light" onClick={() => api('/optimize/compare').then(setCmp).catch((e) => toast(errText(e), 'error'))}>Why RAHAT beats "nearest team"</button>
          <button className="light" onClick={() => setHelp(true)}>❓ How it works</button>
          <DemoMenu open={demoOpen} setOpen={setDemoOpen}>
            <button onClick={() => demo(() => startDemo())}><b>▶ Start full demo</b> <span className="muted small">(alert + 18 messages)</span></button>
            <button onClick={() => demo(startNight50)}><b>▶ Dehradun night: 50 messages</b> <span className="muted small">(alert + synthetic set)</span></button>
            <button onClick={() => demo(() => run(api('/sim/alert', { body: {} }), 'Flood alert received (simulated IMD)'))}>Only the flood alert</button>
            <button onClick={() => demo(() => run(api('/sim/replay', { body: { speed: 4 } }), 'Replaying 18 demo messages…'))}>Only the message burst</button>
            <button onClick={() => demo(() => setSimOffline(!sim))}>{sim ? '📶 End simulation: control room back online' : '📴 Simulate: control room offline'}</button>
            <a href="#/citizen" target="_blank" rel="noreferrer">Citizen phone simulator ↗</a>
            <a href="#/team" target="_blank" rel="noreferrer">Rescue team app ↗</a>
            <hr />
            <button className="danger" onClick={() => demo(() => confirm('Reset all demo data?') && run(api('/sim/reset', { body: {} }), 'Demo data reset'))}>Reset demo data</button>
          </DemoMenu>
        </div>
      </header>
      {err && <div className="err">{err}</div>}
      {(!connected || sim || opPending > 0) && (
        <div className="op-offline">
          {sim && <span className="sim-tag">SIMULATION</span>}
          {!connected || sim ? '📴 No connection to the RAHAT server. You can still enter radio updates; ' : '⏳ '}
          {opPending > 0 ? <b>{opPending} radio update{opPending > 1 ? 's' : ''} saved here, sending automatically when the connection is back.</b>
            : 'they will be saved in this browser and sent automatically.'}
        </div>
      )}

      <div className="kpis">
        <Kpi color="slate" icon="✉" n={state.counts.messages} label="messages received" sub={`merged into ${state.incidents.length} cases`} onClick={() => setTab('activity')} />
        <Kpi color="amber" icon="✋" n={groups.decide.length} label="need your approval" tone={groups.decide.length ? 'warn' : ''} onClick={() => jump('decide')} />
        <Kpi color="red" icon="⏳" n={groups.waiting.length} label="waiting for a team" tone={groups.waiting.some((r) => r.i.band === 'CRIT') ? 'bad' : ''} onClick={() => jump('waiting')} />
        <Kpi color="purple" icon="🔎" n={groups.verify.length} label="to check (maybe fake)" onClick={() => jump('verify')} />
        <Kpi color="blue" icon="🚤" n={groups.onway.length} label="teams on the way" onClick={() => jump('onway')} />
        <Kpi color="green" icon="✅" n={state.counts.resolved} label="rescued / resolved" tone="good" onClick={() => jump('closed')} />
        <Kpi color="teal" icon="👥" n={`${freeTeams}/${state.teams.length}`} label="teams free" onClick={() => setTab('teams')} />
      </div>

      <main>
        <div className="mapwrap">
          <MapView state={state} selected={sel} onSelect={onSelect} />
          <Legend />
        </div>
        <div className="side">
          <nav className="stabs">
            <button className={tab === 'cases' ? 'on' : ''} onClick={() => setTab('cases')}>Cases <span className="count">{live.length}</span></button>
            <button className={tab === 'activity' ? 'on' : ''} onClick={() => setTab('activity')}>Live activity</button>
            <button className={tab === 'teams' ? 'on' : ''} onClick={() => setTab('teams')}>Teams <span className="count">{freeTeams}/{state.teams.length}</span></button>
          </nav>
          {tab === 'activity' && <Activity state={state} version={version} onSelect={setSel} />}
          {tab === 'teams' && <TeamsTab state={state} onSelect={setSel} />}
          {tab === 'cases' && <>
          {live.length > 3 && (
            <input className="search" placeholder="🔍 Search a place or case…  e.g. Majra" value={q} onChange={(e) => setQ(e.target.value)} />
          )}
          {needle && GROUPS.every((g) => groups[g.key].length === 0) && <div className="empty">No case matches "{q}".</div>}
          {live.length === 0 && (
            <div className="welcome">
              <h3>No open cases right now</h3>
              <p>New help messages appear here by themselves, sorted by what you need to do.</p>
              <button className="approve-btn approve-wide" onClick={() => startDemo()}>▶ Start full demo</button>
              <button className="light wide-light" onClick={startNight50}>▶ Bigger demo: Dehradun night, 50 messages</button>
              <div className="welcome-or">or try it live</div>
              <ol>
                <li>Send a WhatsApp message to the RAHAT number, e.g. <i>"Deep Nagar gali 3 mein dadi phasi hai, paani 5 feet"</i></li>
                <li>It appears here in a few seconds with a suggested team</li>
                <li>Press <b>✓ Approve</b> to send the team</li>
              </ol>
              <a href="#/citizen" target="_blank" rel="noreferrer">No phone? Use the citizen phone simulator ↗</a>
            </div>
          )}
          {GROUPS.map((g) => groups[g.key].length > 0 && (
            <section key={g.key} id={`grp-${g.key}`} className={`group group-${g.key}`}>
              <h4><span className="gicon">{g.icon}</span>{g.title} <span className="count">{groups[g.key].length}</span></h4>
              <div className="hint">{g.hint}</div>
              {groups[g.key].map(({ i, act, rec }) => (
                <Card key={i.id} i={i} act={act} rec={rec} teams={teams} area={state.area} live={(state.live_incidents || []).includes(i.id)} place={placeName[i.location.place_id] || i.location.text}
                  selected={sel === i.id} fresh={fresh.current.has(i.id)} onClick={() => setSel(i.id)} onApprove={approve} busy={busy} />
              ))}
            </section>
          ))}

          {state.residents.some((r) => r.last_check_result) && (
            <section className="group">
              <h4>Registered elderly residents (auto-called)</h4>
              {state.residents.map((r) => <div key={r.id} className="small">{r.name}, {r.area}: <b>{r.last_check_result || 'not called yet'}</b></div>)}
            </section>
          )}
          {state.review.length > 0 && (
            <section className="group">
              <h4>Needs a human to read <span className="count">{state.review.length}</span></h4>
              {state.review.map((m) => <div key={m.id} className="review small">{fmtTime(m.received_at)} · {m.channel}: {m.raw_text || m.meta.review_reason}</div>)}
            </section>
          )}
          {closed.length > 0 && (
            <details className="group" id="grp-closed" open={closed.length <= 5}>
              <summary>Closed ({closed.length})</summary>
              {closed.map((i) => <div key={i.id} className="small muted closedrow" onClick={() => setSel(i.id)}>{i.status === 'false' ? '✗ false report' : '✓ resolved'}: {i.title}</div>)}
            </details>
          )}
          </>}
        </div>
        {sel && <IncidentPanel id={sel} state={state} version={version} onClose={() => setSel(null)} />}
      </main>

      <footer className="muted small">
        <span>Not help requests: {state.counts.info_messages}</span>
        <span>AI extractor: {state.metrics.extractor}</span>
        <span>Team plan solved in {state.metrics.last_solve_ms ?? '-'} ms</span>
        <span>{state.metrics.avg_pipeline_s ?? '-'} s per message</span>
      </footer>
      {cmp && <Compare cmp={cmp} onClose={() => setCmp(null)} />}
      {help && <HowItWorks onClose={() => setHelp(false)} />}
      <Toasts />
    </div>
  )
}

function Kpi({ icon, color = 'slate', n, label, sub, tone = '', onClick }) {
  return (
    <div className={`kpi kpi-${color} ${tone} ${onClick ? 'clickable' : ''}`} onClick={onClick} role={onClick ? 'button' : undefined} title={onClick ? 'Click to see these' : undefined}>
      <div className="kpi-bubble">{icon}</div>
      <div className="kpi-body">
        <div className="kpi-n">{n}</div>
        <div className="kpi-l">{label}</div>
        {sub && <div className="kpi-s">{sub}</div>}
      </div>
    </div>
  )
}

// One-screen explanation for judges / first-time users
function HowItWorks({ onClose }) {
  const steps = [
    ['📩', 'A help message arrives', 'From WhatsApp, SMS, a missed call or a volunteer. Hinglish, Hindi or English, even with typos.'],
    ['🤖', 'RAHAT reads and sorts it', 'AI finds the place, people, children/elderly, water depth. Messages about the same family are merged. Copy-paste fakes are flagged, never deleted.'],
    ['🗺️', 'It plans all teams together', 'Not just "nearest team". It plans the whole city at once so the most serious cases are reached first, and suggests one team per case.'],
    ['✋', 'You approve', 'The control room officer always decides. One tap on ✓ Approve sends the team.'],
    ['🚤', 'Team goes, updates from the field', 'The team sees the job and route on its phone and taps On the way → Reached → Rescued. The sender gets updates.'],
  ]
  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-body how" onClick={(e) => e.stopPropagation()}>
        <div className="how-head"><h2>How RAHAT works</h2><button className="x" onClick={onClose} aria-label="close">×</button></div>
        <ol className="how-steps">
          {steps.map(([icon, title, text], k) => (
            <li key={k}><div className="how-icon">{icon}</div><div><b>{k + 1}. {title}</b><p>{text}</p></div></li>
          ))}
        </ol>
        <div className="how-colors">
          <b>Colours:</b>
          {['CRIT', 'HIGH', 'MED', 'LOW'].map((b) => <span key={b} className="badge" style={{ background: BAND_COLOR[b] }}>{BAND_NAME[b]}</span>)}
          <span className="muted small">= how serious (urgency). Trust ("is it real?") is shown separately and never lowers urgency.</span>
        </div>
        <button className="approve-btn" onClick={onClose}>Got it</button>
      </div>
    </div>
  )
}

function Card({ i, act, rec, teams, place, area, live, selected, fresh, onClick, onApprove, busy }) {
  const senders = new Set(i.sender_hashes).size
  // titles look like "6 people trapped - MAJRA": the place is shown separately, so drop the repeat
  const cut = place ? i.title.toLowerCase().lastIndexOf(` - ${place.toLowerCase()}`) : -1
  const title = cut > 0 ? i.title.slice(0, cut) : i.title
  const facts = [
    place && ['📍', place],
    i.people_count && ['👥', `${i.people_count}${i.people_estimated ? '?' : ''} ${i.people_count === 1 ? 'person' : 'people'}`],
    i.vulnerable.length > 0 && ['⚠️', i.vulnerable.map((v) => VULN_TEXT[v] || v).join(', '), 'vuln'],
    ['💬', `${i.message_ids.length} msg${senders > 1 ? ` · ${senders} people` : ''}`],
  ].filter(Boolean)
  const t = rec && teams[rec.team_id]
  const ta = act && teams[act.team_id]
  return (
    <div className={`qcard band-${i.band} ${selected ? 'sel' : ''} ${fresh ? 'fresh' : ''}`} onClick={onClick}>
      <div className="qcard-top">
        <span className="qband"><i />{BAND_NAME[i.band]}</span>
        <span className="qtime">{live && <span className="qlive" title="At least one message came from a real phone via WhatsApp">🟢 Live WhatsApp</span>}{ago(i.created_at)}</span>
      </div>
      <div className="qcard-title">{hazardNames(i).map(([h, w]) => <span key={h} className={`hz-tag hz-${h}`}>{w}</span>)}{title}</div>
      <div className="qfacts">
        {facts.map(([icon, text, cls]) => <span key={icon} className={cls || ''}><em>{icon}</em>{text}</span>)}
      </div>
      {i.red_flags.length > 0 && (
        <div className="qflags" title="Why RAHAT thinks this may be fake (open the case for details)">
          <span className="qflags-label">May be fake:</span>
          {i.red_flags.map((f) => { const x = explainFlag(f, i, area); return <span key={f} className="qflag">{x.icon} {x.short}</span> })}
        </div>
      )}
      {rec && !act && (
        <div className="qaction">
          <div className="qteam">
            <span className="qteam-icon">{TEAM_ICON[t?.type] || '⛑'}</span>
            <div><b>{t?.name || rec.team_id}</b><small>{Math.round(rec.eta_min)} min away · suggested</small></div>
          </div>
          <button className="qapprove" disabled={busy === rec.id} onClick={(e) => onApprove(e, rec, i)}>
            {busy === rec.id ? 'Sending…' : '✓ Approve'}
          </button>
        </div>
      )}
      {act && (
        <div className="qaction onway">
          <div className="qteam">
            <span className="qteam-icon">{TEAM_ICON[ta?.type] || '⛑'}</span>
            <div><b>{ta?.name || act.team_id}</b><small>ETA {Math.round(act.eta_min)} min</small></div>
          </div>
          <span className={`qstatus st-${act.status}`}>{ASSIGN_TEXT[act.status] || act.status}</span>
        </div>
      )}
    </div>
  )
}

function DemoMenu({ open, setOpen, children }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const close = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open, setOpen])
  return (
    <div className="demomenu" ref={ref}>
      <button className="light" onClick={() => setOpen(!open)}>Demo ▾</button>
      {open && <div className="demomenu-body">{children}</div>}
    </div>
  )
}

function Legend() {
  const [open, setOpen] = useState(false)   // starts closed so the map stays clear
  if (!open) return <button className="legend-btn" onClick={() => setOpen(true)}>🗺️ Map key</button>
  return (
    <div className="legend">
      <div className="legend-head"><b>Map key</b><button className="x" onClick={() => setOpen(false)} aria-label="hide map key">×</button></div>
      {['CRIT', 'HIGH', 'MED', 'LOW'].map((b) => (
        <div key={b}><span className="dot" style={{ background: BAND_COLOR[b] }} /> {BAND_NAME[b]} priority</div>
      ))}
      <div><span className="dot dashed" /> Dashed outline = check first</div>
      <div><span className="dot" style={{ background: '#9e9e9e' }} /> Closed</div>
      <div className="legend-sep">Lines and areas</div>
      <div><span className="line grey" /> Suggested team</div>
      <div><span className="line blue" /> Team approved / on the way</div>
      <div><span className="dot flood" /> Flooded area (boats only)</div>
      <div><span className="ring" /> Service area</div>
      <div className="muted small">Bigger circle = more urgent</div>
    </div>
  )
}

function Compare({ cmp, onClose }) {
  const col = (name, sub, r, win) => (
    <div className={`cmpcol ${win ? 'win' : ''}`}>
      <h3>{name}</h3>
      <div className="muted small">{sub}</div>
      <div className="cmpbig">Longest wait for a critical case<br /><b className="big">{r.longest_critical_wait_min ?? '-'} min</b></div>
      <div>Critical cases reached within {r.critical_target_min} min: <b>{r.critical_within_target} of {r.critical_incidents}</b></div>
      <div>Critical cases with no team: <b>{r.critical_unserved.length}</b> · total travel {r.total_travel_min} min</div>
      <table className="tbl"><thead><tr><th>Priority</th><th>Team</th><th>Goes to</th><th>Time</th></tr></thead><tbody>
        {r.assignments.map((a, k) => <tr key={k}><td><span className="badge" style={{ background: BAND_COLOR[a.band] }}>{BAND_NAME[a.band]}</span></td><td>{a.team}</td><td>{a.incident}</td><td>{a.eta_min} min</td></tr>)}
      </tbody></table>
    </div>
  )
  const better = (cmp.rahat.longest_critical_wait_min ?? 1e9) <= (cmp.greedy.longest_critical_wait_min ?? 1e9)
  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-body" onClick={(e) => e.stopPropagation()}>
        <h2>Same incidents, same teams: two ways to assign</h2>
        <div className="cmp">
          {col('Usual way: nearest free team', 'First message gets the nearest team, even if a later one is more serious.', cmp.greedy, false)}
          {col('RAHAT', `Plans all teams together so the most serious cases are reached first (${cmp.cost_mode}).`, cmp.rahat, better)}
        </div>
        <p className="muted small">Travel time = straight line × road factor ÷ team speed (prototype estimate). Numbers are illustrative until the evaluation is run.</p>
        <button onClick={onClose}>Close</button>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ live activity (from the audit log)
const hhmm = (t) => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
const STATUS_WORD = { en_route: 'is on the way', reached: 'reached the spot', rescued: 'finished: rescued / delivered', no_one_here: 'found no one there', need_backup: 'asked for backup', free: 'is free again', location: 'shared GPS location' }

function describe(x, ctx) {
  const inc = (id) => ctx.inc[id]
  const incTitle = (id) => inc(id)?.title || id
  const asg = ctx.asg[x.entity]
  const team = (id) => ctx.team[id]?.name || id
  const a = x.after || {}
  const st = x.action.startsWith('status_') ? x.action.slice(7) : null
  if (x.action === 'message_attached') {
    const isNew = inc(x.entity)?.message_ids?.[0] === a.message
    return { icon: '✉', text: isNew ? `New case: ${incTitle(x.entity)}` : `Another message about: ${incTitle(x.entity)} (merged)`, inc: x.entity, kind: isNew ? 'new' : '' }
  }
  if (x.action === 'recommended') return { icon: '🤖', text: `RAHAT suggested ${team(a.team)} for ${incTitle(a.incident)} (${Math.round(a.eta_min)} min away)`, inc: a.incident }
  if (x.action === 'approved') return { icon: '✓', text: `${x.actor} approved: ${team(a.team_id)} → ${incTitle(a.incident_id)}`, inc: a.incident_id, kind: 'ok' }
  if (x.action === 'override') return { icon: '↔', text: `${x.actor} sent ${team(a.team_id)} instead → ${incTitle(a.incident_id)}`, inc: a.incident_id, kind: 'ok' }
  if (x.action === 'rejected') return { icon: '✗', text: `${x.actor} rejected ${team(a.team_id)} for ${incTitle(a.incident_id)}`, inc: a.incident_id }
  if (st && st !== 'location') {
    // the field app stamps the time of the tap; a big gap means it was tapped offline and synced later
    const lag = a.client_ts ? (new Date(x.at) - new Date(a.client_ts)) / 1000 : 0
    const later = lag < 90 ? `${Math.round(lag)} s later` : `${Math.round(lag / 60)} min later`
    const offline = a.by ? ` · 📻 entered by ${a.by}${a.via ? ` via ${a.via}` : ''}`
      : lag > 20 ? ` · 📴 tapped offline at ${hhmm(a.client_ts)}, reached control room ${later}` : ''
    return { icon: st === 'rescued' ? '✅' : st === 'no_one_here' ? '✗' : st === 'need_backup' ? '🆘' : '🚤', text: `${team(x.actor)} ${STATUS_WORD[st] || st}${asg ? `: ${incTitle(asg.incident_id)}` : ''}${offline}`, inc: asg?.incident_id, kind: st === 'rescued' ? 'ok' : st === 'need_backup' ? 'bad' : '' }
  }
  if (x.action.startsWith('verify_')) return { icon: '☎', text: `${incTitle(x.entity)}: ${x.action.slice(7).replace('_', ' ')}`, inc: x.entity }
  if (x.action === 'alert_received') return { icon: '⚠', text: `Flood alert received: ${x.actor}`, kind: 'bad' }
  if (x.action.startsWith('surge_')) return { icon: '⚡', text: `Surge mode → ${a.mode}${a.why ? ` (${a.why})` : ''}`, kind: a.mode === 'on' ? 'bad' : '' }
  if (x.action === 'stale_status_ignored') return { icon: '↩', text: `Late update "${a.status}" from ${team(x.actor)} skipped: that job was already updated (e.g. by radio)` }
  if (x.action === 'proactive_checks_started') return { icon: '📞', text: `Auto-calls started to ${a.count} registered elderly residents` }
  if (x.action.startsWith('proactive_')) return { icon: '📞', text: `Auto-call ${x.entity}: ${x.action.slice(10).replace('_', ' ')}` }
  return null   // seeded, withdrawn suggestions, GPS pings: noise for this view
}

function Activity({ state, version, onSelect }) {
  const [rows, setRows] = useState(null)
  useEffect(() => {
    let live = true
    api('/audit?limit=150').then((r) => live && setRows(r)).catch(() => live && setRows([]))
    return () => { live = false }
  }, [version])
  if (!rows) return <div className="empty">Loading…</div>
  const ctx = {
    inc: Object.fromEntries(state.incidents.map((i) => [i.id, i])),
    team: Object.fromEntries(state.teams.map((t) => [t.id, t])),
    asg: Object.fromEntries(state.assignments.map((a) => [a.id, a])),
  }
  // fold runs of messages about the same case into one row ("8 messages about …")
  const items = []
  rows.forEach((x) => {
    const d = describe(x, ctx)
    if (!d) return
    const last = items[items.length - 1]
    if (last && x.action === 'message_attached' && last.x.action === 'message_attached' && last.x.entity === x.entity && !d.kind && !last.d.kind) {
      last.n += 1
      last.d = { ...last.d, text: `${last.n} messages about: ${ctx.inc[x.entity]?.title || x.entity} (all merged into one case)` }
      return
    }
    items.push({ x, d, n: 1 })
  })
  if (!items.length) return <div className="empty">Nothing has happened yet. Every message, suggestion and team update shows up here.</div>
  return (
    <div className="activity">
      {items.map(({ x, d }) => (
        <div key={x.id} className={`act ${d.kind || ''} ${d.inc ? 'clickable' : ''}`} onClick={() => d.inc && onSelect(d.inc)}>
          <span className="act-time">{hhmm(x.at)}</span>
          <span className="act-icon">{d.icon}</span>
          <span className="act-text">{d.text}</span>
        </div>
      ))}
    </div>
  )
}

function TeamsTab({ state, onSelect }) {
  const inc = Object.fromEntries(state.incidents.map((i) => [i.id, i]))
  const job = {}
  state.assignments.filter((a) => !['recommended', 'done'].includes(a.status)).forEach((a) => { job[a.team_id] = a })
  const order = { busy: 0, returning: 1, free: 2 }
  return (
    <div className="teams-tab">
      <p className="hint">Each team's phone link: <code>#/team?id=TEAM-ID</code></p>
      {[...state.teams].sort((x, y) => order[x.status] - order[y.status] || x.id.localeCompare(y.id)).map((t) => {
        const a = job[t.id]
        return (
          <div key={t.id} className={`trow trow-${t.status} ${a ? 'clickable' : ''}`} onClick={() => a && onSelect(a.incident_id)}>
            <span className="trow-icon">{TEAM_ICON[t.type] || '⛑'}</span>
            <div className="trow-main">
              <b>{t.name}</b> <span className="muted small">{t.id} · carries {t.capacity}</span>
              <div className="small">{a ? <>{ASSIGN_TEXT[a.status] || a.status} → <b>{inc[a.incident_id]?.title || a.incident_id}</b></> : t.status === 'free' ? 'Ready for a job' : t.status}</div>
            </div>
            <span className={`tstat tstat-${t.status}`}>{t.status === 'busy' ? 'on a job' : t.status}</span>
          </div>
        )
      })}
    </div>
  )
}
