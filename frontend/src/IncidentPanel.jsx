import { useEffect, useState } from 'react'
import { sendTeamStatus } from './opqueue.js'
import { api, API, ACTION_LABEL, ASSIGN_TEXT, BAND_COLOR, BAND_NAME, CALLBACK_TEXT, NEED_TEXT, TEAM_ICON, VULN_TEXT, ago, errText, explainFlag, fmtTime, toast, trustSteps } from './api.js'

const FACTOR_NAME = { medical: 'Medical danger', people: 'People at risk', vulnerable: 'Elderly / child / alone', hazard: 'Water / hazard level', waiting: 'Time waiting' }
const STATUS_TEXT = { open: 'Open', assigned: 'Team assigned', resolved: 'Resolved', false: 'False report' }
const RADIO = [['en_route', '🚤 On the way'], ['reached', '📍 Reached'], ['rescued', '✅ Rescued'], ['need_backup', '🆘 Need backup'], ['no_one_here', '✗ No one here']]
const CHANNEL_ICON = { whatsapp: '🟢 WhatsApp', sms: '✉ SMS', voice: '🎙 Voice note', missed_call: '📞 Missed call', ivr: '☎ IVR', web: '🌐 Web', helpline: '🚨 112', volunteer: '🙋 Volunteer', proactive: '🔔 Auto-call' }

// Incident details as a pop-up over the dashboard. Esc, × or a click outside closes it.
export default function IncidentPanel({ id, state, onClose, version }) {
  const [d, setD] = useState(null)
  const [cl, setCl] = useState(null)
  const [changeTo, setChangeTo] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    api(`/incidents/${id}`).then((x) => live && setD(x)).catch((e) => setErr(errText(e)))
    api(`/incidents/${id}/checklist`).then((x) => live && setCl(x)).catch(() => {})
    return () => { live = false }
  }, [id, version])

  const shell = (body) => (
    <div className="imodal" onClick={onClose}>
      <div className="imodal-box" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">{body}</div>
    </div>
  )
  if (!d) return shell(<div className="imodal-loading"><div className="spinner" />{err || 'Loading case…'}</div>)

  const i = d.incident
  const teams = Object.fromEntries(state.teams.map((t) => [t.id, t]))
  const place = state.places.find((p) => p.id === i.location.place_id)
  const rec = d.assignments.filter((a) => a.status === 'recommended')
  const active = d.assignments.filter((a) => ['approved', 'en_route', 'on_scene', 'done'].includes(a.status))
  const freeTeams = state.teams.filter((t) => t.status === 'free')
  const senders = new Set(i.sender_hashes).size
  const conf = Math.round(i.confidence * 100)
  const confWord = i.confidence < 0.3 ? 'Probably fake' : i.confidence < 0.5 ? 'Doubtful' : i.confidence < 0.8 ? 'Likely true' : 'Very likely true'
  const confColor = i.confidence < 0.3 ? '#c62828' : i.confidence < 0.5 ? '#ef6c00' : '#2e7d32'
  const factors = Object.entries(i.priority_breakdown)
  const maxW = Math.max(1, ...factors.map(([, f]) => f.weighted))

  const act = async (fn, ok) => {
    setErr('')
    try { await fn(); if (ok) toast(ok, 'ok') } catch (e) { setErr(errText(e)); toast(errText(e), 'error') }
  }
  const tname = (tid) => teams[tid]?.name || tid
  const DECIDE_MSG = {
    approve: (a) => `✓ ${tname(a.team_id)} sent. The sender gets an update.`,
    change: (a, t) => `✓ ${tname(t)} sent instead of ${tname(a.team_id)}`,
    reject: (a) => `${tname(a.team_id)} rejected: RAHAT will suggest another team`,
  }
  const decide = (a, action, team_id) =>
    act(() => api(`/assignments/${a.id}/approve`, { body: { action, operator: 'operator-1', team_id } }), DECIDE_MSG[action](a, team_id))
  const VERIFY_MSG = { confirmed: 'Marked as confirmed by phone', no_answer: 'Marked: no answer', denied: 'Marked: caller says no help needed' }
  const verify = (result) => act(() => api(`/incidents/${i.id}/verify`, { body: { result, by: 'operator-1' } }), VERIFY_MSG[result])

  // the facts, as big tiles: icon, label, value
  const tiles = [
    { icon: '📍', label: 'Where', value: place?.name || i.location.text || 'Unknown',
      tags: [i.location.flooded && 'Flooded: boats only', i.location.in_area === false && 'Outside flood area'].filter(Boolean),
      sub: i.location.text && place && i.location.text.toLowerCase() !== place.name.toLowerCase() ? `Sender said: "${i.location.text}"` : null,
      warn: i.location.in_area === false },
    { icon: '👥', label: 'People', value: i.people_count ? `${i.people_count}${i.people_estimated ? ' (approx.)' : ''}` : 'Not stated' },
    { icon: '❗', label: 'Vulnerable', value: i.vulnerable.map((v) => VULN_TEXT[v] || v).join(', ') || 'None reported', hot: i.vulnerable.length > 0 },
    { icon: '🆘', label: 'Needs', value: i.needs.map((n) => NEED_TEXT[n] || n).join(', ') || 'Not stated' },
    i.water_depth_ft != null && { icon: '🌊', label: 'Water', value: `${i.water_depth_ft} feet`, hot: i.water_depth_ft >= 4 },
    i.medical !== 'none' && { icon: '✚', label: 'Medical', value: i.medical.replace('_', ' '), hot: i.medical !== 'minor' },
    { icon: '✉', label: 'Reports', value: `${i.message_ids.length} message${i.message_ids.length > 1 ? 's' : ''}`, sub: `from ${senders} ${senders === 1 ? 'person' : 'different people'}` },
  ].filter(Boolean)

  return shell(<>
    <div className="ihead" style={{ background: BAND_COLOR[i.band] }}>
      <div className="ihead-top">
        <span className="ihead-band">{BAND_NAME[i.band]} priority</span>
        <span className="ihead-meta">{i.id} · {STATUS_TEXT[i.status] || i.status} · first report {ago(i.created_at)}</span>
        <button className="ihead-x" onClick={onClose} aria-label="close">×</button>
      </div>
      <h2>{i.title}</h2>
    </div>

    <div className="inext">👉 <b>Next step:</b> {
      active.some((a) => a.status !== 'done')
        ? `${active.filter((a) => a.status !== 'done').map((a) => tname(a.team_id)).join(', ')} is on the job: follow its updates (or enter them below if the team calls by radio)`
        : i.status === 'resolved' ? 'Done: case resolved' : i.status === 'false' ? 'Closed: marked as a false report'
        : ACTION_LABEL[i.action] || i.action}</div>
    {err && <div className="err">{err}</div>}
    {i.notes.filter((n) => n.startsWith('update from another sender nearby')).map((n, k) => (
      <div key={k} className="nearby-update">📣 <b>Someone else nearby sent an update.</b> It was <u>not</u> added to this case
        (it may be about another house). Please check: {n.replace(/^update from another sender nearby, please check: /, '')}</div>
    ))}

    <div className="tiles">
      {tiles.map((t) => (
        <div key={t.label} className={`tile ${t.hot ? 'hot' : ''} ${t.warn ? 'warn' : ''}`}>
          <div className="tile-label"><span>{t.icon}</span> {t.label}</div>
          <div className="tile-value">{t.value}</div>
          {t.tags?.map((g) => <span key={g} className="tile-tag">{g}</span>)}
          {t.sub && <div className="tile-sub">{t.sub}</div>}
        </div>
      ))}
    </div>

    <div className="icols">
      <div>
        <section className="ibox">
          <h4>🚤 Team <span className="muted small">needs: {i.required_team_types.map((t) => `${TEAM_ICON[t] || ''} ${t.replace('_', ' ')}`).join(' + ') || 'none'}</span></h4>
          {rec.length === 0 && active.length === 0 && (
            <div className="muted">No team suggested yet: {i.action === 'verify_first' ? 'check the report first.' : i.action === 'need_location' ? 'waiting for the location.' : 'no free team can reach this place right now.'}</div>
          )}
          {rec.map((a) => (
            <div key={a.id} className="rec">
              <div className="rec-main">{TEAM_ICON[teams[a.team_id]?.type]} <b>{tname(a.team_id)}</b> can reach in <b className="eta">{Math.round(a.eta_min)} min</b></div>
              <button className="approve-btn approve-wide" onClick={() => decide(a, 'approve')}>✓ Approve & send team</button>
              <div className="row">
                <select value={changeTo} onChange={(e) => setChangeTo(e.target.value)}>
                  <option value="">Send another team…</option>
                  {freeTeams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
                <button disabled={!changeTo} onClick={() => decide(a, 'change', changeTo)}>Send that</button>
                <button className="danger" onClick={() => decide(a, 'reject')}>Not this team</button>
              </div>
              <details className="small"><summary>How this team was chosen</summary>
                <div className="muted">{a.reason}. Plan score {a.cost} = travel {a.cost_breakdown.travel_term} {a.cost_breakdown.priority_term} (priority bonus) + {a.cost_breakdown.penalty} (penalty). RAHAT picks the lowest total for all cases together.</div>
              </details>
            </div>
          ))}
          {active.map((a) => (
            <div key={a.id} className="teamstate">
              <span className="teamstate-icon">{TEAM_ICON[teams[a.team_id]?.type] || '⛑'}</span>
              <div><b>{tname(a.team_id)}</b>
                <div className={`pill pill-${a.status}`}>{ASSIGN_TEXT[a.status] || a.status}</div>
                <div className="muted small">approved by {a.approved_by} · ETA {Math.round(a.eta_min)} min</div>
              </div>
            </div>
          ))}
          {active.filter((a) => a.status !== 'done').map((a) => (
            <div key={`radio-${a.id}`} className="radio-box">
              <div className="radio-head">📻 Team called by radio / phone? Enter it for them</div>
              <div className="radio-btns">
                {RADIO.filter(([s]) => !(s === 'en_route' && a.status !== 'approved') && !(s === 'reached' && a.status === 'on_scene')).map(([s, label]) => (
                  <button key={s} className={s === 'no_one_here' ? 'danger' : s === 'rescued' ? 'ok-btn' : ''}
                    onClick={() => (s !== 'no_one_here' || confirm('Team found no one there? The report will be marked false.'))
                      && sendTeamStatus(a.team_id, s, a.id, label)}>{label}</button>
                ))}
              </div>
              <div className="muted small">Saved as "entered by operator via radio" in the log. If the team's own phone sends the same later, it is skipped.</div>
            </div>
          ))}
        </section>

        <section className="ibox">
          <h4>🔎 Is it real? <span className="muted small">(never lowers urgency)</span></h4>
          <div className="conf-line"><b style={{ color: confColor }}>{confWord}</b> <span className="muted">· trust score {conf} / 100</span></div>
          <div className="meter"><div style={{ width: `${conf}%`, background: confColor }} /></div>

          {i.red_flags.length > 0 && (
            <div className="fakebox">
              <div className="fakebox-head">⚠️ Why we think this may be fake</div>
              {i.red_flags.map((f) => {
                const x = explainFlag(f, i, state.area)
                return (
                  <div key={f} className="fakereason">
                    <span className="fakereason-icon">{x.icon}</span>
                    <div><b>{x.title}</b><p>{x.why}</p></div>
                  </div>
                )
              })}
              <div className="fakebox-foot">
                <b>What RAHAT does:</b> the case is <b>not deleted</b> and its <b>urgency is not lowered</b>.
                No team is suggested until someone checks it. If the caller confirms, trust jumps to 90+.
              </div>
            </div>
          )}

          <div className="steps-trust">
            <div className="steps-trust-head">How the trust score was worked out</div>
            {trustSteps(i.confidence_reasons).map((s, k) => (
              <div key={k} className={`ts ${s.delta > 0 ? 'up' : s.delta < 0 ? 'down' : ''}`}>
                <span className="ts-n">{s.start ? s.value : s.delta != null ? `${s.delta > 0 ? '+' : '−'}${Math.abs(s.delta)}` : s.set || '·'}</span>
                <span className="ts-t">{s.text}</span>
              </div>
            ))}
            <div className="ts total"><span className="ts-n">= {conf}</span><span className="ts-t">final trust score{i.confidence <= 0.02 ? ' (lowest possible)' : ''}</span></div>
            <div className="muted small">Rule-based score, not a tested probability.</div>
          </div>
          <div className="row"><span>☎ Phone check: <b>{CALLBACK_TEXT[i.callback.status] || i.callback.status}</b></span></div>
          <div className="row">
            <button onClick={() => verify('confirmed')}>✓ Caller confirmed</button>
            <button onClick={() => verify('no_answer')}>No answer</button>
            <button onClick={() => verify('denied')}>✗ No help needed</button>
          </div>
        </section>

        <section className="ibox">
          <h4>📊 Why this urgency? <span className="muted small">score {i.priority_score}</span></h4>
          <div className="bars">
            {factors.map(([k, f]) => (
              <div key={k} className="bar-row" title={`value ${f.value} × weight ${f.weight} = ${f.weighted}`}>
                <div className="bar-label">{FACTOR_NAME[k] || k}</div>
                <div className="bar"><div style={{ width: `${(f.weighted / maxW) * 100}%`, background: BAND_COLOR[i.band] }} /></div>
                <div className="bar-n">{f.weighted}</div>
                {f.why && <div className="bar-why muted small">{f.why}</div>}
              </div>
            ))}
          </div>
          <div className="muted small">{(state.config.bands || []).filter(([, t]) => t > 0).map(([b, t]) => `${BAND_NAME[b]} ≥ ${t}`).join(' · ')}</div>
        </section>
      </div>

      <div>
        <section className="ibox">
          <h4>💬 Messages ({d.messages.length})</h4>
          <div className="ichat">
            {d.messages.map((m) => (
              <div key={m.id} className="ibubble">
                <div className="ibubble-meta">{m.audio_url ? '🎙 Voice note' : CHANNEL_ICON[m.channel] || m.channel} · sender {m.sender_hash.slice(0, 6)} · {fmtTime(m.received_at)}</div>
                <div>{m.raw_text || <i>{m.audio_url ? '(could not turn it into text: please listen)' : '(no text)'}</i>}</div>
                {m.audio_url && (
                  <div className="voice">
                    <audio controls preload="none" src={`${API}/messages/${m.id}/audio`} />
                    {m.meta?.transcribed_by && <span className="voice-by">text by {m.meta.transcribed_by.split(':')[0]}: please listen if unsure</span>}
                  </div>
                )}
              </div>
            ))}
            {i.followup_question && <div className="ibubble ours"><div className="ibubble-meta">RAHAT asked</div>{i.followup_question}</div>}
          </div>
          {i.notes.length > 0 && <details className="small"><summary>System notes ({i.notes.length})</summary><ul>{i.notes.map((n, k) => <li key={k}>{n}</li>)}</ul></details>}
        </section>

        <section className="ibox">
          <h4>📋 Official SOP checklist</h4>
          {!cl ? <div className="muted">…</div> : cl.status !== 'OK' ? (
            <div className="sop-miss">{cl.message}
              {cl.why && <div className="small">{cl.why}</div>}
              {cl.sop_loaded === 0 && <div className="muted small">No SOP documents loaded yet: run  python -m rag.fetch_sop</div>}</div>
          ) : (<>
            <ol className="checklist">{cl.items.map((it, n) => <li key={n}>{it.text} <span className="src">[{it.source}]</span></li>)}</ol>
            <div className="muted small sop-note">Every step is copied from an official document (section + page shown), nothing is written by AI from memory.{' '}
              {(cl.documents || []).map((d, k) => <span key={k}>{k ? ' · ' : 'Sources: '}<a href={d.url} target="_blank" rel="noreferrer">{d.title?.split(':').pop().trim()} ({d.year})</a></span>)}
            </div>
          </>)}
        </section>
      </div>
    </div>
  </>)
}
