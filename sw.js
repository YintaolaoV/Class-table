const CACHE_NAME = 'static-timetable-shell-v1.0.35';
const APP_SHELL = [
  './', './index.html', './styles.css', './app.js', './manifest.json',
  './icons/icon-192.png', './icons/icon-512.png', './icons/apple-touch-icon.png'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('static-timetable-shell-') && key !== CACHE_NAME).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  event.respondWith(
    fetch(event.request).then(response => {
      const copy = response.clone();
      caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
      return response;
    }).catch(() => caches.match(event.request).then(hit => hit || (event.request.mode === 'navigate' ? caches.match('./index.html') : Response.error())))
  );
});

self.addEventListener('push', event => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (error) { payload = {title: '静态课表', body: event.data?.text() || '有新的课程提醒。'}; }
  event.waitUntil(self.registration.showNotification(payload.title || '静态课表', {
    body: payload.body || '有新的课程提醒。',
    icon: './icons/icon-192.png',
    badge: './icons/icon-192.png',
    data: {url: payload.url || './'}
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(clients.matchAll({type: 'window', includeUncontrolled: true}).then(list => {
    const target = event.notification.data?.url || './';
    for (const client of list) if ('focus' in client) return client.focus();
    return clients.openWindow(target);
  }));
});
