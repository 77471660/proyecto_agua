const SW_VERSION = 'aquasmart-pwa-v5';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});

self.addEventListener('push', (event) => {
  let data = {};

  if (event.data) {
    try {
      data = event.data.json();
    } catch (error) {
      data = {
        title: 'AquaSmart CRM',
        body: event.data.text(),
      };
    }
  }

  const title = data.title || 'AquaSmart CRM';
  const options = {
    body: data.body || 'Tienes una nueva actualizacion.',
    icon: '/static/img/icons/android-chrome-192x192.png',
    badge: '/static/img/icons/favicon-96x96.png',
    data: {
      url: data.url || '/pedidos/repartidor/',
    },
    tag: data.tag || 'aquasmart-push',
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const targetUrl = new URL(
    event.notification.data && event.notification.data.url
      ? event.notification.data.url
      : '/pedidos/repartidor/',
    self.location.origin
  ).href;

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((clients) => {
        for (const client of clients) {
          if (client.url === targetUrl && 'focus' in client) {
            return client.focus();
          }
        }

        if (self.clients.openWindow) {
          return self.clients.openWindow(targetUrl);
        }

        return undefined;
      })
  );
});
