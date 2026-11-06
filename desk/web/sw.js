// Grok Bot service worker: web push only. No API caching.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (_) { d = { body: event.data && event.data.text() }; }
  const title = d.title || "Grok Bot";
  event.waitUntil(self.registration.showNotification(title, {
    body: d.body || "",
    icon: d.icon || "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    tag: d.id ? "push-" + d.id : undefined,
    // the exact links.py URL; tapping the push opens it
    data: { url: d.url || "/#/home" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/#/home";
  event.waitUntil(self.clients.openWindow(url));
});
