/* ============================================================
   Rasova POS: Service Worker
   Handles: offline caching, install prompt.
   (Offline order queuing lives in page JS (see offlineQueue in
   templates/core/base.html), not here. Background Sync API support is
   unreliable inside the Android app's WebView, so the queue is flushed
   from the page itself on 'online'/page-load, not via a sync event.)
   ============================================================ */

// v8 (27 Nov 2026): clears admin pages kept under an ADMIN_URL other than /admin/.
const CACHE_VERSION = 'rasova-v8';
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

/* Pages to pre-warm on install (best-effort: they need a login, and a
   page that answers with a redirect to the login page is not kept). */
const OFFLINE_PAGES = [
    '/dashboard/',
    '/billing/',
    '/kitchen/',
];

/* Django's hashed static names: name.<12 hex digits>.ext. A file under such
   a name never changes, so a kept copy is always right. */
const HASHED_NAME = /\.[0-9a-f]{12}\.[a-z0-9]+$/i;

/* A pre-warmed page is only useful offline if what it loads is cached too.
   Its code is in files now (static/js/billing/, static/js/core/), under
   names that carry a hash in production, so no fixed list can name them:
   the page's own HTML is read for its scripts and stylesheets, each script
   for the modules it imports, each stylesheet for its fonts. */
const ASSET_IN_PAGE = /<(?:script|link)\b[^>]*?\b(?:src|href)="([^"]+)"/g;
const IMPORT_IN_SCRIPT = /(?:\bimport|\bfrom)\s*["'](\.{1,2}\/[^"']+)["']/g;
const URL_IN_STYLE = /url\(\s*["']?([^"')]+)["']?\s*\)/g;

function sameOriginStatic(reference, base) {
    try {
        const url = new URL(reference, base);
        return url.origin === self.location.origin && url.pathname.startsWith('/static/') ? url.pathname : null;
    } catch (notAUrl) {
        return null;
    }
}

async function cacheWithWhatItLoads(cache, path, seen) {
    if (seen.has(path)) return;
    seen.add(path);
    let response = await cache.match(path);
    if (!response) {
        response = await fetch(path);
        if (!response.ok) return;
        await cache.put(path, response.clone());
    }
    const pattern = path.endsWith('.js') ? IMPORT_IN_SCRIPT : (path.endsWith('.css') ? URL_IN_STYLE : null);
    if (!pattern) return;
    const text = await response.text();
    const next = [];
    for (const match of text.matchAll(pattern)) {
        const found = sameOriginStatic(match[1], self.location.origin + path);
        if (found) next.push(found);
    }
    await Promise.allSettled(next.map(found => cacheWithWhatItLoads(cache, found, seen)));
}

async function cachePageAssets(html, pageUrl, seen) {
    const cache = await caches.open(STATIC_CACHE);
    const found = [];
    for (const match of html.matchAll(ASSET_IN_PAGE)) {
        const path = sameOriginStatic(match[1], self.location.origin + pageUrl);
        if (path) found.push(path);
    }
    await Promise.allSettled(found.map(path => cacheWithWhatItLoads(cache, path, seen)));
}

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
            caches.open(MENU_CACHE).then(cache => {
                const seen = new Set();
                return Promise.allSettled(
                    OFFLINE_PAGES.map(url =>
                        fetch(url, { credentials: 'include' })
                            .then(r => {
                                // Logged out, the answer is the login page after a
                                // redirect: keeping it as "/billing/" would show a
                                // cashier a login form when the connection drops.
                                if (!r.ok || r.redirected) return null;
                                const copy = r.clone();
                                return cache.put(url, r)
                                    .then(() => copy.text())
                                    .then(html => cachePageAssets(html, url, seen));
                            })
                            .catch(() => {})
                    )
                );
            })
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
    if (url.pathname === '/') return;           // landing page: let nginx/browser handle it
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

    // Another host's files are the browser's own business, except images
    // (dish photos and logos on the media host), kept for offline. A script
    // such as Cloudflare's analytics beacon, which a blocker refuses, used to
    // come through here and be answered with nothing: "Failed to convert
    // value to 'Response'" in every console (21 Nov 2026).
    if (url.hostname !== location.hostname && request.destination !== 'image') return;

    // Our own static files whose name does not carry a hash of their content
    // (every one of them on a development machine, where nothing is hashed):
    // the server first, the kept copy only when there is no network. Kept
    // first, a stylesheet that was fixed went on being served as it was
    // before the fix, on every normal page load, for as long as the cache
    // lived: only a hard refresh showed the new one. cache: 'no-cache' asks
    // the server every time (a 304 when nothing changed): the browser's own
    // cache once handed back an old page_actions.js beside a new app.js, and
    // the page's modules failed to load (15 Nov 2026).
    if (url.hostname === location.hostname && url.pathname.startsWith('/static/') && !HASHED_NAME.test(url.pathname)) {
        event.respondWith(
            fetch(request, { cache: 'no-cache' }).then(response => {
                if (response.ok) {
                    const clone = response.clone();
                    caches.open(STATIC_CACHE).then(c => c.put(request, clone));
                }
                return response;
            }).catch(() =>
                caches.match(request).then(cached => cached || new Response('Offline', { status: 503, statusText: 'Offline' }))
            )
        );
        return;
    }

    // Static files that can never change under the same name (production's
    // hashed names, app.3f2a9c1b7d4e.js) and images from other hosts: the
    // kept copy first. With no copy and no network the answer is a network
    // error (Response.error()), never nothing: respondWith(undefined) throws.
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
                }).catch(() => Response.error())
            )
        );
        return;
    }

    // App pages: network first, cache on success, serve cache when offline.
    // A page the server marks X-Rasova-Offline: never (the admin's, at its
    // secret address) is never kept.
    const isBillingOrKitchen = url.pathname.startsWith('/billing') || url.pathname.startsWith('/kitchen');
    event.respondWith(
        fetch(request, { credentials: 'include' })
            .then(response => {
                if (response.ok && response.headers.get('X-Rasova-Offline') !== 'never') {
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
