import { useCallback, useState } from 'react'
import { api, useLive, BAND_COLOR, fmtTime } from './api.js'
import MapView from './MapView.jsx'
import IncidentPanel from './IncidentPanel.jsx'

const SURGE_LABEL = { off: 'Surge Mode OFF', standby: 'STANDBY (alert)', pending_confirm: 'SPIKE: confirm Surge?', on: 'SURGE MODE ACTIVE' }

export default function Dashboard() {
  const [state, setState] = useState(null)
  const [sel, setSel] = useState(null)
  const [version, setVersion] = useState(0)
  const [cmp, setCmp] = useState(null)
  const [err, setErr] = useState('')

  const refresh = useCallback(() => {
    api('/state').then((s) => { setState(s); setVersion((v) => v + 1); setErr('') }).catch((e) => setErr(String(e)))
  }, [])
  useLive(refresh)
  const onSelect = useCallback((id) => setSel(id), [])

  if (!state) return <div className="loading">Connecting to RAHAT backend… {err}</div>
  const s = state.surge
  const teams = Object.fromEntries(state.teams.map((t) => [t.id, t]))
  const byInc = {}
  state.assignments.forEach((a) => { (byInc[a.incident_id] ||= []).push(a) })
  const live = state.incidents.filter((i) => i.status === 'open' || i.status === 'assigned')
  const closed = state.incidents.filter((i) => i.status === 'resolved' || i.status === 'false')

  const run = (p) => p.then(refresh).catch((e) => setErr(String(e)))
  const alert = () => run(api('/sim/alert', { body: {} }))   // sends data/alert_imd_sample.json

  return (
    <div className="dash">
      <header>
        <div className="brand">RAHAT Command <span className="muted">· {state.district}</span></div>
        <div className={`surge surge-${s.mode}`} title={s.reason}>{SURGE_LABEL[s.mode]}</div>
        <div className="tools">
          <button onClick={alert}>Simulate IMD alert</button>
          <button onClick={() => run(api('/sim/replay', { body: { speed: 4 } }))}>Replay burst</button>
          {s.mode !== 'on'
            ? <button onClick={() => run(api('/surge', { body: { on: true, actor: 'duty_officer' } }))}>{s.mode === 'pending_confirm' ? 'Confirm Surge' : 'Surge ON'}</button>
            : <button onClick={() => run(api('/surge', { body: { on: false, actor: 'duty_officer' } }))}>Surge OFF</button>}
          <button onClick={() => api('/optimize/compare').then(setCmp)}>RAHAT vs greedy</button>
          <button className="danger" onClick={() => confirm('Reset all demo data?') && run(api('/sim/reset', { body: {} }))}>Reset</button>
          <a href="#/citizen" target="_blank" rel="noreferrer">Citizen sim ↗</a>
          <a href="#/team" target="_blank" rel="noreferrer">Team app ↗</a>
        </div>
      </header>
      {err && <div className="err">{err}</div>}
      <main>
        <MapView state={state} selected={sel} onSelect={onSelect} />
        <div className="side">
          <h4>Priority queue <span className="muted">({live.length} live)</span></h4>
          <div className="queue">
            {live.map((i) => {
              const as = (byInc[i.id] || []).filter((a) => a.status !== 'done')
              const act = as.find((a) => a.status !== 'recommended')
              const rec = as.find((a) => a.status === 'recommended')
              return (
                <div key={i.id} className={`qrow ${sel === i.id ? 'sel' : ''}`} onClick={() => setSel(i.id)}>
                  <span className="badge" style={{ background: BAND_COLOR[i.band] }}>{i.band}</span>
                  <div className="qmain">
                    <div className="qtitle">{i.title}</div>
                    <div className="muted small">
                      {i.id} · P {i.priority_score} · {i.message_ids.length} msg
                      {i.confidence < 0.5 && <span className="verify">VERIFY</span>}
                      {i.callback.status === 'pending' && <span className="tag">callback…</span>}
                    </div>
                  </div>
                  <div className="qteam">
                    {act ? <><b>{teams[act.team_id]?.id}</b><div className="small">{act.status} · {Math.round(act.eta_min)}m</div></>
                      : rec ? <><span className="muted">{teams[rec.team_id]?.id}?</span><div className="small">ETA {Math.round(rec.eta_min)}m</div></>
                        : <span className="muted small">{i.action === 'verify_first' ? 'verify' : i.action === 'need_location' ? 'no location' : 'waiting'}</span>}
                  </div>
                </div>
              )
            })}
          </div>
          {state.review.length > 0 && <>
            <h4>Human review <span className="muted">({state.review.length})</span></h4>
            {state.review.map((m) => <div key={m.id} className="review small">{fmtTime(m.received_at)} · {m.channel}: {m.raw_text || m.meta.review_reason}</div>)}
          </>}
          {state.residents.some((r) => r.last_check_result) && <>
            <h4>Proactive checks</h4>
            {state.residents.map((r) => <div key={r.id} className="small">{r.name}, {r.area}: <b>{r.last_check_result || '-'}</b></div>)}
          </>}
          {closed.length > 0 && <details><summary>Closed ({closed.length})</summary>{closed.map((i) => <div key={i.id} className="small muted" onClick={() => setSel(i.id)}>{i.id} {i.status}: {i.title}</div>)}</details>}
        </div>
        {sel && <IncidentPanel id={sel} state={state} version={version} onClose={() => setSel(null)} />}
      </main>
      <footer>
        <span>Open <b>{state.counts.open}</b></span>
        <span>Assigned <b>{state.counts.assigned}</b></span>
        <span>Waiting <b>{state.counts.waiting}</b></span>
        <span>Resolved <b>{state.counts.resolved}</b></span>
        <span>Messages <b>{state.counts.messages}</b> → incidents <b>{state.incidents.length}</b></span>
        <span className="muted">info {state.counts.info_messages} · review {state.counts.review_messages}</span>
        <span className="muted">extractor {state.metrics.extractor} · solve {state.metrics.last_solve_ms} ms · pipeline {state.metrics.avg_pipeline_s ?? '-'} s/msg</span>
      </footer>
      {cmp && <Compare cmp={cmp} onClose={() => setCmp(null)} />}
    </div>
  )
}

function Compare({ cmp, onClose }) {
  const col = (name, r) => (
    <div className="cmpcol">
      <h3>{name}</h3>
      <div>Longest critical wait: <b className="big">{r.longest_critical_wait_min ?? '-'} min</b></div>
      <div>Critical within {r.critical_target_min} min: <b>{r.critical_within_target}/{r.critical_incidents}</b></div>
      <div>Critical unserved: <b>{r.critical_unserved.length}</b> · total travel {r.total_travel_min} min</div>
      <table className="tbl"><tbody>
        {r.assignments.map((a, k) => <tr key={k}><td><span className="badge" style={{ background: BAND_COLOR[a.band] }}>{a.band}</span></td><td>{a.team}</td><td>→ {a.incident}</td><td>{a.eta_min} min</td></tr>)}
      </tbody></table>
    </div>
  )
  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-body" onClick={(e) => e.stopPropagation()}>
        <h2>Same incidents, same teams (all at base)</h2>
        <div className="cmp">{col('Greedy: nearest free team, first come', cmp.greedy)}{col(`RAHAT: min-cost flow (${cmp.cost_mode})`, cmp.rahat)}</div>
        <p className="muted small">Travel = straight line × road factor / team speed (prototype). Numbers are illustrative until the evaluation is run.</p>
        <button onClick={onClose}>Close</button>
      </div>
    </div>
  )
}
