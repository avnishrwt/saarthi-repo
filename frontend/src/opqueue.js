// Operator enters a team's status for it (radio fallback). If the control room itself has no
// connection to the server, the update is kept in this browser and sent by itself later.
import { api, toast } from './api.js'

const KEY = 'rahat.opqueue'
const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || [] } catch { return [] } }
const save = (q) => { try { localStorage.setItem(KEY, JSON.stringify(q)) } catch { /* private mode */ } window.dispatchEvent(new Event('rahat-opqueue')) }
export const opQueue = load

// Demo switch (Demo menu): pretend the control room cannot reach the server. Clearly a simulation.
const SIM = 'rahat.simOffline'
export const simOffline = () => { try { return localStorage.getItem(SIM) === '1' } catch { return false } }
export function setSimOffline(on) {
  try { on ? localStorage.setItem(SIM, '1') : localStorage.removeItem(SIM) } catch { /* private mode */ }
  window.dispatchEvent(new Event('rahat-opqueue'))
  if (!on) flushOpQueue()
}
const post = (u) => simOffline()
  ? Promise.reject(new Error("Can't reach the RAHAT server. Is the backend running? (simulated)"))
  : api(`/teams/${u.team_id}/status`, { method: 'PATCH', body: u })
const isNetwork = (e) => /reach the RAHAT server/.test(String(e?.message || e))

export async function sendTeamStatus(teamId, status, assignmentId, label) {
  const u = { team_id: teamId, status, assignment_id: assignmentId, client_ts: new Date().toISOString(), by: 'operator-1', via: 'radio' }
  try {
    const r = await post(u)
    toast(r?.ignored ? `${label}: already updated, skipped` : `📻 Entered for ${teamId}: ${label}`, r?.ignored ? 'info' : 'ok')
    return true
  } catch (e) {
    if (!isNetwork(e)) { toast(String(e.message || e), 'error'); return false }
    save([...load(), u])
    toast(`📴 Control room offline: "${label}" for ${teamId} saved here, sends by itself when the connection is back`, 'info')
    return false
  }
}

let busy = false
export async function flushOpQueue() {
  if (busy) return
  busy = true
  let q = load(), sent = 0
  try {
    while (q.length) {
      try { await post(q[0]) } catch (e) { if (isNetwork(e)) break }   // other errors: drop that one update
      q = q.slice(1); save(q); sent += 1
    }
  } finally { busy = false }
  if (sent) toast(`✓ Connection back: ${sent} radio update${sent > 1 ? 's' : ''} sent`, 'ok')
}
