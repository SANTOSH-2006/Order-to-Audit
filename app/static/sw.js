// Minimal service worker: lets the order page + price list open without network.
const CACHE = 'order-v1', SHELL = ['/m/new', '/static/style.css', '/static/queue.js', '/static/order.js', '/api/catalog'];
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {})));
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const u = new URL(e.request.url);
  if (!SHELL.includes(u.pathname)) return;
  e.respondWith(fetch(e.request).then(r => { const c = r.clone(); caches.open(CACHE).then(x => x.put(e.request, c)); return r; }).catch(() => caches.match(e.request)));
});
