// Offline shell for the team PWA. Caches ONLY the app's own files (HTML / JS / CSS / icons),
// never API responses: a cached API answer would make the app think it is online in airplane mode.
// Status updates are queued in localStorage by the app itself (see src/TeamApp.jsx).
const CACHE = 'rahat-shell-v2'
const SCOPE = new URL(self.registration.scope).pathname          // e.g. "/ui/" or "/"
const isShell = (url) =>
  url.pathname.startsWith(SCOPE) &&
  (url.pathname === SCOPE || url.pathname.endsWith('.html') || url.pathname.includes('/assets/') ||
   /\.(js|css|svg|png|webmanifest)$/.test(url.pathname))

self.addEventListener('install', () => { self.skipWaiting() })
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))   // drop v1 (it had API answers)
    .then(() => self.clients.claim()))
})
self.addEventListener('fetch', (e) => {
  const req = e.request
  const url = new URL(req.url)
  if (req.method !== 'GET' || url.origin !== self.location.origin) return
  if (req.mode === 'navigate') {                 // the page itself: network first, cached shell when offline
    e.respondWith(fetch(req).then((res) => { const c = res.clone(); caches.open(CACHE).then((x) => x.put(SCOPE, c)); return res })
      .catch(() => caches.match(SCOPE)))
    return
  }
  if (!isShell(url)) return                      // API calls go straight to the network (and fail when offline)
  e.respondWith(fetch(req)
    .then((res) => { const c = res.clone(); caches.open(CACHE).then((x) => x.put(req, c)); return res })
    .catch(() => caches.match(req)))
})
