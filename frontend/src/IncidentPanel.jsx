import { useEffect, useState } from 'react'
import { api, ACTION_LABEL, BAND_COLOR, fmtTime } from './api.js'

export default function IncidentPanel({ id, state, onClose, version }) {
  const [d, setD] = useState(null)
  const [cl, setCl] = useState(null)
  const [changeTo, setChangeTo] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    api(`/incidents/${id}`).then((x) => live && setD(x)).catch((e) => setErr(String(e)))
    api(`/incidents/${id}/checklist`).then((x) => live && setCl(x)).catch(() => {})
    return () => { live = false }
  }, [id, version])

  if (!d) return <aside className="panel">Loading… {err}</aside>
  const i = d.incident
  const teams = Object.fromEntries(state.teams.map((t) => [t.id, t]))
  const rec = d.assignments.filter((a) => a.status === 'recommended')
  const active = d.assignments.filter((a) => ['approved', 'en_route', 'on_scene', 'done'].includes(a.status))
  const freeTeams = state.teams.filter((t) => t.status === 'free')

  const act = async (fn) => { setErr(''); try { await fn() } catch (e) { setErr(String(e)) } }
  const decide = (a, action, team_id) =>
    act(() => api(`/assignments/${a.id}/approve`, { body: { action, operator: 'operator-1', team_id } }))
  const verify = (result) => act(() => api(`/incidents/${i.id}/verify`, { body: { result, by: 'operator-1' } }))

  return (
    <aside className="panel">
      <div className="panel-head">
        <span className="badge" style={{ background: BAND_COLOR[i.band] }}>{i.band}</span>
        <b>{i.id}</b> <span className="muted">{i.status}</span>
        <button className="x" onClick={onClose} aria-label="close">×</button>
      </div>
      <h3>{i.title}</h3>
      <div className="action">{ACTION_LABEL[i.action] || i.action}</div>
      {err && <div className="err">{err}</div>}

      <section>
        <h4>Priority {i.priority_score} <span className="muted">(how serious if true)</span></h4>
        <table className="tbl">
          <thead><tr><th>Factor</th><th>Value</th><th>Weight</th><th>=</th><th>Why</th></tr></thead>
          <tbody>
            {Object.entries(i.priority_breakdown).map(([k, f]) => (
              <tr key={k}><td>{k}</td><td>{f.value}</td><td>×{f.weight}</td><td><b>{f.weighted}</b></td><td className="muted">{f.why}</td></tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h4>Confidence {(i.confidence * 100).toFixed(0)}% <span className="muted">(how likely true; never lowers priority)</span></h4>
        <div className="meter"><div style={{ width: `${i.confidence * 100}%`, background: i.confidence < 0.3 ? '#c62828' : i.confidence < 0.5 ? '#ef6c00' : '#2e7d32' }} /></div>
        {i.red_flags.length > 0 && <ul className="flags">{i.red_flags.map((f) => <li key={f}>⚑ {f}</li>)}</ul>}
        <details><summary>How confidence was computed</summary><ul>{i.confidence_reasons.map((r) => <li key={r}>{r}</li>)}</ul></details>
        <div className="row">
          Callback: <b>{i.callback.status}</b>
          <button onClick={() => verify('confirmed')}>Confirmed</button>
          <button onClick={() => verify('no_answer')}>No answer</button>
          <button onClick={() => verify('denied')}>Not needed</button>
        </div>
      </section>

      <section>
        <h4>Teams <span className="muted">needs: {i.required_team_types.join(' + ') || 'none'}</span></h4>
        {rec.length === 0 && active.length === 0 && <div className="muted">No recommendation ({i.action === 'verify_first' ? 'verify first' : 'no free capable team'}).</div>}
        {rec.map((a) => (
          <div key={a.id} className="rec">
            <div><b>{teams[a.team_id]?.name}</b> → {i.id}, ETA <b>{Math.round(a.eta_min)} min</b> <span className="muted">({a.team_type_slot})</span></div>
            <div className="muted small">{a.reason}</div>
            <div className="muted small">cost {a.cost} = travel {a.cost_breakdown.travel_term} {a.cost_breakdown.priority_term} priority + {a.cost_breakdown.penalty} penalty</div>
            <div className="row">
              <button className="primary" onClick={() => decide(a, 'approve')}>Approve</button>
              <select value={changeTo} onChange={(e) => setChangeTo(e.target.value)}>
                <option value="">Change to…</option>
                {freeTeams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
              <button disabled={!changeTo} onClick={() => decide(a, 'change', changeTo)}>Change</button>
              <button className="danger" onClick={() => decide(a, 'reject')}>Reject</button>
            </div>
          </div>
        ))}
        {active.map((a) => (
          <div key={a.id} className="muted">{teams[a.team_id]?.name}: <b>{a.status}</b> (approved by {a.approved_by})</div>
        ))}
      </section>

      <section>
        <h4>SOP checklist</h4>
        {!cl ? <div className="muted">…</div> : cl.status !== 'OK' ? (
          <div className="sop-miss">{cl.message}{cl.sop_loaded === 0 && <div className="muted small">No SOP documents loaded in data/sop/</div>}</div>
        ) : (
          <ol className="checklist">{cl.items.map((it, n) => <li key={n}>{it.text} <span className="src">[{it.source}]</span></li>)}</ol>
        )}
      </section>

      <section>
        <h4>Messages ({d.messages.length})</h4>
        {d.messages.map((m) => (
          <div key={m.id} className="msg"><span className="muted small">{fmtTime(m.received_at)} · {m.channel} · {m.sender_hash.slice(0, 6)}</span><div>{m.raw_text || <i>(no text)</i>}</div></div>
        ))}
        {i.followup_question && <div className="muted small">Follow-up sent: “{i.followup_question}”</div>}
        {i.notes.length > 0 && <ul className="small">{i.notes.map((n, k) => <li key={k}>{n}</li>)}</ul>}
      </section>
    </aside>
  )
}
