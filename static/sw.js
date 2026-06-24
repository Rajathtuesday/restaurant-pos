/* ============================================================
   Rasova POS — Service Worker
   Handles: offline caching, install prompt.
   (Offline order queuing lives in page JS — see offlineQueue in
   templates/core/base.html — not here. Background Sync API support is
   unreliable inside the Android app's WebView, so the queue is flushed
   from the page itself on 'online'/page-load, not via a sync event.)
   ============================================================ */

const CACHE_VERSION = 'rasova-v3';
const STATIC_CACHE  = `${CACHE_VERSION}-static`;
const MENU_CACHE    = `${CACHE_VERSION}-menu`;

/* Assets to cache on install */
const STATIC_ASSETS = [
    '/static/css/themes/luxury.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css',
    'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js',
    'https://cdn.jsdelivr.net/npm/sweetalert2@11.10.8/dist/sweetalert2.all.min.js',
    'https://cdn.jsdelivr.net/npm/sweetalert2@11.10.8/dist/sweetalert2.min.css',
];

/* Pages to pre-warm on install (best-effort, need auth so may 302 — that's fine) */
const OFFLINE_PAGES = [
    '/dashboard/',
    '/billing/',
    '/kitchen/',
];

/* ── Install: pre-cache static assets ──────────────────── */
self.addEventListener('install', event => {
    event.waitUntil(
        Promise.all([
            caches.open(STATIC_CACHE).then(cache =>
                Promise.allSettled(
                    STATIC_ASSETS.map(url =>
                        cache.add(url).catch(() => {}) // don't fail install if CDN unreachable
                    )
                )
            ),
            caches.open(MENU_CACHE).then(cache =>
                Promise.allSettled(
                    OFFLINE_PAGES.map(url =>
                        fetch(url, { credentials: 'include' })
                            .then(r => r.ok ? cache.put(url, r) : null)
                            .catch(() => {})
                    )
                )
            )
        ])
        .then(() => self.skipWaiting())
    );
});

/* ── Activate: clean up old caches ─────────────────────── */
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(
                keys
                    .filter(k => k.startsWith('rasova-') && k !== STATIC_CACHE && k !== MENU_CACHE)
                    .map(k => caches.delete(k))
            )
        )
        .then(() => self.clients.claim())
    );
});

/* ── Fetch: cache-first for static, network-first for pages ── */
self.addEventListener('fetch', event => {
    const { request } = event;
    const url = new URL(request.url);

    // Skip: non-GET, cross-origin API calls, admin, landing page
    if (request.method !== 'GET') return;
    if (url.pathname === '/') return;           // landing page — let nginx/browser handle it
    if (url.pathname.startsWith('/admin/')) return;
    if (url.pathname.startsWith('/api/')) return;

    // Static assets (JS, CSS, fonts) — cache first
    if (
        url.hostname !== location.hostname ||
        url.pathname.startsWith('/static/')
    ) {
        event.respondWith(
            caches.match(request).then(cached =>
                cached || fetch(request).then(response => {
                    if (response.ok) {
                        const clone = response.clone();
                        caches.open(STATIC_CACHE).then(c => c.put(request, clone));
                    }
                    return response;
                }).catch(() => cached)
            )
        );
        return;
    }

    // App pages — network first, cache on success, serve cache when offline
    const isBillingOrKitchen = url.pathname.startsWith('/billing') || url.pathname.startsWith('/kitchen');
    event.respondWith(
        fetch(request, { credentials: 'include' })
            .then(response => {
                if (response.ok) {
                    const clone = response.clone();
                    caches.open(MENU_CACHE).then(c => c.put(request, clone));
                }
                return response;
            })
            .catch(() =>
                caches.match(request).then(cached => {
                    if (cached) return cached;
                    return isBillingOrKitchen
                        ? caches.match('/billing/')
                        : caches.match('/dashboard/');
                }).then(r => r || new Response('Offline', { status: 503, statusText: 'Offline' }))
            )
    );
});
