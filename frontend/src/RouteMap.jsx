import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'

// In-app route for the field team.
// Boats: straight line over water (roads are flooded anyway).
// Road teams: road route from the public OSRM demo server (fine for a hackathon demo, not
// for production: self-host OSRM or use a paid routing API). Offline or failure -> straight
// dashed line + "approximate" label. Flooded / blocked roads are NOT known to OSRM.
const OSRM = 'https://router.project-osrm.org/route/v1/driving'
const SPEED_KMH = { boat: 10, medical: 30, relief: 25, search_rescue: 25, fire: 30 }

function haversineKm(a, b) {
  const R = 6371, r = Math.PI / 180
  const dLat = (b[0] - a[0]) * r, dLng = (b[1] - a[1]) * r
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

export default function RouteMap({ team, incident, myPos }) {
  const el = useRef(null)
  const map = useRef(null)
  const layer = useRef(null)
  const [info, setInfo] = useState(null)

  const from = myPos || [team.lat, team.lng]
  const to = [incident.location.lat, incident.location.lng]

  useEffect(() => {
    map.current = L.map(el.current, { zoomControl: false, attributionControl: true })
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OSM' }).addTo(map.current)
    layer.current = L.layerGroup().addTo(map.current)
    return () => map.current.remove()
  }, [])

  useEffect(() => {
    let cancelled = false
    const g = layer.current
    const draw = (coords, kind, km, min) => {
      if (cancelled) return
      g.clearLayers()
      L.polyline(coords, { color: kind === 'road' ? '#1565c0' : '#0277bd', weight: 5, opacity: 0.85,
        dashArray: kind === 'road' ? null : '8 8' }).addTo(g)
      L.circleMarker(from, { radius: 8, color: '#fff', weight: 2, fillColor: '#2e7d32', fillOpacity: 1 })
        .bindTooltip(myPos ? 'You (GPS)' : `${team.name} (last known)`, { permanent: true, direction: 'bottom' }).addTo(g)
      L.circleMarker(to, { radius: 10, color: '#fff', weight: 2, fillColor: '#c62828', fillOpacity: 1 })
        .bindTooltip(incident.id, { permanent: true, direction: 'top' }).addTo(g)
      map.current.fitBounds(L.latLngBounds([from, to, ...coords]).pad(0.25))
      setInfo({ kind, km: km.toFixed(2), min: Math.round(min) })
    }
    const straight = (kind) => {
      const km = haversineKm(from, to) * 1.3
      draw([from, to], kind, km, (km / (SPEED_KMH[team.type] || 20)) * 60)
    }
    if (team.type === 'boat' || !navigator.onLine) {
      straight(team.type === 'boat' ? 'water' : 'approx')
    } else {
      fetch(`${OSRM}/${from[1]},${from[0]};${to[1]},${to[0]}?overview=full&geometries=geojson`)
        .then((r) => r.json())
        .then((j) => {
          const rt = j.routes?.[0]
          if (!rt) throw new Error('no route')
          draw(rt.geometry.coordinates.map(([lng, lat]) => [lat, lng]), 'road', rt.distance / 1000, rt.duration / 60)
        })
        .catch(() => straight('approx'))
    }
    return () => { cancelled = true }
  }, [from[0], from[1], to[0], to[1], team.type])

  return (
    <div className="routebox">
      <div ref={el} className="routemap" />
      {info && (
        <div className="routeinfo">
          {info.kind === 'road' && <>Road route: <b>{info.km} km</b> · ~<b>{info.min} min</b></>}
          {info.kind === 'water' && <>Boat, straight line over water: <b>{info.km} km</b> · ~<b>{info.min} min</b></>}
          {info.kind === 'approx' && <>Approximate (offline / no road route): <b>{info.km} km</b> · ~<b>{info.min} min</b></>}
          <div className="muted small">Road routing does not know which roads are flooded. Follow local instructions.</div>
        </div>
      )}
    </div>
  )
}
