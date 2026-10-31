// Minimal offline shell for the team PWA: network first, cached copy when offline.
// Status updates are queued in localStorage by the app itself (see src/TeamApp.jsx).
const CACHE = 'rahat-shell-v1'
self.addEventListener('install', (e) => { self.skipWaiting() })
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()) })
self.addEventListener('fetch', (e) => {
  const req = e.request
  if (req.method !== 'GET' || new URL(req.url).origin !== self.location.origin) return
  e.respondWith(
    fetch(req)
      .then((res) => { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); return res })
      .catch(() => caches.match(req).then((r) => r || caches.match('./index.html')))
  )
})
