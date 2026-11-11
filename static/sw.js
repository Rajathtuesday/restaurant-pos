/* ============================================================
   Rasova POS — Service Worker
   Handles: offline caching, install prompt.
   (Offline order queuing lives in page JS — see offlineQueue in
   templates/core/base.html — not here. Background Sync API support is
   unreliable inside the Android app's WebView, so the queue is flushed
   from the page itself on 'online'/page-load, not via a sync event.)
   ============================================================ */

const CACHE_VERSION = 'rasova-v4';
const STATIC_CACHE  = `${CACHE_VERSION}-static`;
const MENU_CACHE    = `${CACHE_VERSION}-menu`;

/* Assets to cache on install */
const STATIC_ASSETS = [
    // Rasova serves these itself (static/vendor, see VENDOR.md): no outside
    // host between a cashier and the page. The unhashed names are served
    // alongside the hashed ones, so this list needs no rebuild step.
    '/static/css/themes/luxury.css',
    '/static/css/rasova.css',
    '/static/vendor/fonts/fonts.css',
    '/static/vendor/bootstrap/bootstrap.min.css',
    '/static/vendor/bootstrap-icons/bootstrap-icons.min.css',
    '/static/vendor/bootstrap/bootstrap.bundle.min.js',
    '/static/vendor/sweetalert2/sweetalert2.all.min.js',
    '/static/vendor/sweetalert2/sweetalert2.min.css',
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
    // /live-demo/ redirects to the tenant's own subdomain (see
    // _subdomain_redirect in accounts/views/auth_views.py) -- a
    // cross-origin redirect. The "app pages" handler below does its own
    // fetch()-and-follow-redirect and hands the result to respondWith(),
    // which browsers can't do across origins for a navigation request --
    // the click silently re-renders the current page instead of actually
    // navigating. Let the browser handle this redirect natively, same
    // reasoning as the landing page skip just above.
    if (url.pathname === '/live-demo/') return;

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
