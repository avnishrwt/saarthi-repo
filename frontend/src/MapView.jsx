import { useEffect, useRef } from 'react'
import L from 'leaflet'
import { BAND_COLOR } from './api.js'

// Incidents = circles sized + coloured by priority (with a text label, never colour only);
// teams = labelled markers; dashed lines = recommended (grey) / approved (blue) routes.
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
    return () => map.current.remove()
  }, [])

  useEffect(() => {
    if (!state || !layer.current) return
    const g = layer.current
    g.clearLayers()
    const { area, incidents, teams, assignments, places } = state
    if (!centered.current) { map.current.setView(area.center, 13); centered.current = true }
    L.circle(area.center, { radius: area.radius_km * 1000, color: '#0f3d3e', weight: 1, dashArray: '4 6', fill: false }).addTo(g)
    places.filter((p) => p.flooded).forEach((p) =>
      L.circle([p.lat, p.lng], { radius: 120, color: '#4fc3f7', weight: 0, fillOpacity: 0.18 }).addTo(g))

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
      const r = 8 + Math.min(i.priority_score, 30) * 0.7
      const c = L.circleMarker([i.location.lat, i.location.lng], {
        radius: r, color: i.id === selected ? '#000' : '#fff', weight: i.id === selected ? 3 : 1.5,
        fillColor: closed ? '#9e9e9e' : BAND_COLOR[i.band], fillOpacity: closed ? 0.35 : 0.85,
        dashArray: i.confidence < 0.5 ? '3 3' : null,
      }).addTo(g)
      c.bindTooltip(`${i.id} · ${i.band}${i.confidence < 0.5 ? ' · VERIFY' : ''}<br>${i.title}`, { direction: 'top' })
      c.on('click', () => onSelect(i.id))
      L.marker([i.location.lat, i.location.lng], {
        icon: L.divIcon({ className: 'inc-label', html: `${i.band}${closed ? ' ✓' : ''}`, iconSize: [40, 14], iconAnchor: [20, -r + 2] }),
        interactive: false,
      }).addTo(g)
    })

    teams.forEach((t) => {
      L.marker([t.lat, t.lng], {
        icon: L.divIcon({
          className: `team-marker team-${t.status}`,
          html: `<span>${t.type === 'boat' ? '⛵' : t.type === 'medical' ? '✚' : t.type === 'relief' ? '🚚' : t.type === 'fire' ? '🔥' : '⛑'} ${t.id}</span>`,
          iconSize: [80, 20], iconAnchor: [10, 10],
        }),
      }).bindTooltip(`${t.name} · ${t.status}`).addTo(g)
    })
  }, [state, selected, onSelect])

  return <div ref={el} className="map" />
}
