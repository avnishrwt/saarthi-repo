import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import 'leaflet/dist/leaflet.css'
import './styles.css'
import Dashboard from './Dashboard.jsx'
import TeamApp from './TeamApp.jsx'
import Citizen from './Citizen.jsx'

// Hash routes: #/ operator dashboard, #/team field team PWA, #/citizen simulator
function App() {
  const [route, setRoute] = useState(location.hash.replace('#', '') || '/')
  useEffect(() => {
    const f = () => setRoute(location.hash.replace('#', '') || '/')
    window.addEventListener('hashchange', f)
    return () => window.removeEventListener('hashchange', f)
  }, [])
  if (route.startsWith('/team')) return <TeamApp />
  if (route.startsWith('/citizen')) return <Citizen />
  return <Dashboard />
}

if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('./sw.js').catch(() => {})
}

createRoot(document.getElementById('root')).render(<App />)
