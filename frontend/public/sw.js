const CACHE_VERSION = 'mtagentlens-v2';
const APP_SHELL = [
  '/',
  '/handoff',
  '/handoff/',
  '/investigations',
  '/investigations/',
  '/manifest.webmanifest',
  '/icon.svg',
  '/offline.html',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_VERSION)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then(async (response) => {
          const cache = await caches.open(CACHE_VERSION);
          await cache.put(request, response.clone());
          await cache.put(url.pathname || '/', response.clone());
          return response;
        })
        .catch(async () => {
          const candidates = [
            request,
            url.pathname || '/',
            url.pathname.endsWith('/') ? url.pathname.slice(0, -1) : `${url.pathname}/`,
            '/',
          ];

          for (const candidate of candidates) {
            const cached = await caches.match(candidate, { ignoreSearch: true });
            if (cached) {
              return cached;
            }
          }

          return caches.match('/offline.html');
        }),
    );
    return;
  }

  if (
    url.pathname.startsWith('/_next/static/') ||
    url.pathname.endsWith('.js') ||
    url.pathname.endsWith('.css') ||
    url.pathname.endsWith('.svg') ||
    url.pathname.endsWith('.png') ||
    url.pathname.endsWith('.ico') ||
    url.pathname.endsWith('.json')
  ) {
    event.respondWith(
      caches.match(request, { ignoreSearch: true }).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            const cloned = response.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(request, cloned));
            return response;
          }),
      ),
    );
  }
});

self.addEventListener('push', (event) => {
  const payload = event.data ? event.data.json() : {};
  const title = payload.title || 'MtAgentLens';
  const options = {
    body: payload.body || '检测到新的风险事件或责任链变化。',
    icon: '/icon.svg',
    badge: '/icon.svg',
    data: {
      url: payload.url || '/',
    },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl =
    event.notification.data && event.notification.data.url ? event.notification.data.url : '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
      return undefined;
    }),
  );
});
