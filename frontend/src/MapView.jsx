import { useEffect, useRef } from 'react'
import L from 'leaflet'
import { BAND_COLOR, BAND_NAME, TEAM_ICON, caseKind, explainFlag } from './api.js'

// Incidents = circles sized + coloured by priority (with a text label, never colour only);
// teams = labelled markers; dashed lines = recommended (grey) / approved (blue) routes.
// Titles can contain text from public messages: never inject them as raw HTML.
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

export default function MapView({ state, selected, onSelect }) {
  const el = useRef(null)
  const map = useRef(null)
  const layer = useRef(null)
  const centered = useRef(false)

  useEffect(() => {
    map.current = L.map(el.current, { zoomControl: true }).setView([30.30, 78.035], 13)   // Dehradun
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; OpenStreetMap contributors',
    }).addTo(map.current)
    layer.current = L.layerGroup().addTo(map.current)
    // "Show all cases" button (top-left, under the zoom buttons)
    const Fit = L.Control.extend({
      onAdd: () => {
        const b = L.DomUtil.create('button', 'map-fit')
        b.type = 'button'; b.textContent = '⤢ Show all cases'; b.title = 'Zoom to all open cases'
        L.DomEvent.disableClickPropagation(b)
        b.onclick = () => fitAll.current()
        return b
      },
    })
    new Fit({ position: 'topleft' }).addTo(map.current)
    return () => map.current.remove()
  }, [])

  const fitAll = useRef(() => {})
  const lastSel = useRef(null)

  useEffect(() => {
    if (!state || !layer.current) return
    const g = layer.current
    g.clearLayers()
    const { area, incidents, teams, assignments, places } = state
    if (!centered.current) { map.current.setView(area.center, 13); centered.current = true }
    const pts = incidents.filter((i) => i.location.lat != null && (i.status === 'open' || i.status === 'assigned')).map((i) => [i.location.lat, i.location.lng])
    fitAll.current = () => pts.length ? map.current.fitBounds(pts, { padding: [60, 60], maxZoom: 15 }) : map.current.setView(area.center, 13)
    const selInc = incidents.find((i) => i.id === selected)
    if (selected && selected !== lastSel.current && selInc?.location.lat != null) {
      map.current.flyTo([selInc.location.lat, selInc.location.lng], Math.max(map.current.getZoom(), 15), { duration: 0.6 })
    }
    lastSel.current = selected
    L.circle(area.center, { radius: area.radius_km * 1000, color: '#0f3d3e', weight: 1, dashArray: '4 6', fill: false }).addTo(g)
    // flooded areas: light blue patch + a wave icon (skipped where a case already sits on top)
    const occupied = new Set(incidents.filter((i) => i.status === 'open' || i.status === 'assigned').map((i) => i.location.place_id))
    places.filter((p) => p.flooded).forEach((p) => {
      L.circle([p.lat, p.lng], { radius: 180, color: '#38bdf8', weight: 1, opacity: 0.4, fillColor: '#7dd3fc', fillOpacity: 0.22 }).addTo(g)
      if (!occupied.has(p.id)) {
        L.marker([p.lat, p.lng], { icon: L.divIcon({ className: 'poi poi-flood', html: '🌊', iconSize: [28, 28] }) })
          .bindTooltip(`${esc(p.name)}: flooded, boats only`).addTo(g)
      }
    })
    // hospitals: fixed landmarks the operator needs for medical cases
    places.filter((p) => /hospital|aspatal/i.test(p.name)).forEach((p) => {
      L.marker([p.lat, p.lng], { icon: L.divIcon({ className: 'poi poi-hospital', html: '<span>🏥</span>', iconSize: [34, 34] }) })
        .bindTooltip(`🏥 ${esc(p.name)}`).addTo(g)
    })

    const teamById = Object.fromEntries(teams.map((t) => [t.id, t]))
    const incById = Object.fromEntries(incidents.map((i) => [i.id, i]))
    assignments.filter((a) => a.status !== 'done').forEach((a) => {
      const t = teamById[a.team_id], i = incById[a.incident_id]
      if (!t || !i || i.location.lat == null) return
      L.polyline([[t.lat, t.lng], [i.location.lat, i.location.lng]], {
        color: a.status === 'recommended' ? '#607d8b' : '#1565c0', weight: a.status === 'recommended' ? 2 : 3,
        dashArray: '6 6', opacity: 0.9,
      }).addTo(g)
    })

    incidents.forEach((i) => {
      if (i.location.lat == null) return
      const closed = i.status === 'resolved' || i.status === 'false'
      const pos = [i.location.lat, i.location.lng]
      const r = 10 + Math.min(i.priority_score, 30) * 0.8          // coloured halo: bigger = more urgent
      const size = closed ? 30 : Math.round(36 + Math.min(i.priority_score, 30) * 0.4)   // emoji badge 36..48 px
      const color = closed ? '#9e9e9e' : BAND_COLOR[i.band]
      const [, emoji, kindName] = caseKind(i)
      const tip = `${emoji} <b>${kindName}</b> · ${BAND_NAME[i.band]} priority${closed ? ' (closed)' : ''}<br>${esc(i.title)}${i.red_flags.length ? `<br><span style="color:#b54708">⚠ May be fake: ${i.red_flags.map((f) => esc(explainFlag(f, i, area).short)).join(' · ')}</span>` : ''}<br><i>click for details</i>`

      if (!closed && i.band === 'CRIT') {   // pulsing ring: critical cases catch the eye on a busy map
        L.marker(pos, {
          icon: L.divIcon({ className: 'pulse-wrap', html: `<span class="pulse" style="width:${size}px;height:${size}px;border-color:${color}"></span>`, iconSize: [size, size] }),
          interactive: false, zIndexOffset: 400,
        }).addTo(g)
      }
      L.circleMarker(pos, {
        radius: r, color, weight: 0, fillColor: color, fillOpacity: closed ? 0.12 : 0.22, interactive: false,
      }).addTo(g)
      // big round badge with the case-type emoji, border in the priority colour (dashed = check first)
      L.marker(pos, {
        icon: L.divIcon({
          className: `inc-badge ${closed ? 'closed' : ''} ${i.id === selected ? 'sel' : ''}`,
          html: `<span style="width:${size}px;height:${size}px;font-size:${Math.round(size * 0.55)}px;border-color:${color};border-style:${i.confidence < 0.5 ? 'dashed' : 'solid'}">${emoji}</span>`,
          iconSize: [size, size],
        }),
        zIndexOffset: 500 + Math.round(i.priority_score),
      }).bindTooltip(tip, { direction: 'top', offset: [0, -size / 2] }).on('click', () => onSelect(i.id)).addTo(g)
      L.marker(pos, {
        icon: L.divIcon({ className: 'inc-label', html: `<span style="background:${color}">${BAND_NAME[i.band]}${closed ? ' ✓' : ''}</span>`, iconSize: [70, 16], iconAnchor: [35, -size / 2 + 1] }),
        interactive: false, zIndexOffset: 500,
      }).addTo(g)
    })

    teams.forEach((t) => {
      L.marker([t.lat, t.lng], {
        icon: L.divIcon({
          className: `team-marker team-${t.status}`,
          html: `<span>${TEAM_ICON[t.type] || '⛑'} ${t.id}</span>`,
          iconSize: [80, 20], iconAnchor: [10, 10],
        }),
      }).bindTooltip(`${esc(t.name)}: ${t.status === 'free' ? 'free' : t.status === 'busy' ? 'on a job' : 'returning'}`).addTo(g)
    })
  }, [state, selected, onSelect])

  return <div ref={el} className="map" />
}
