// Grok Bot service worker: web push + an app shell that still opens when the desk is offline.
// The API is never cached: numbers always come from the desk (or the app's own last-good copy in memory).
const SHELL = "grokbot-shell-v2";
const FILES = ["/", "/app.css", "/app.js", "/chart.js", "/manifest.json", "/icons/icon-192.png", "/icons/avatar.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// network first for the shell, cache as fallback; everything else (the API, reports) goes straight to the network
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const shell = req.mode === "navigate" || FILES.includes(url.pathname);
  if (!shell || url.pathname.startsWith("/api/") || url.pathname.startsWith("/report/")) return;
  event.respondWith(
    fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(SHELL).then((c) => c.put(req.mode === "navigate" ? "/" : req, copy)); }
      return res;
    }).catch(() => caches.match(req.mode === "navigate" ? "/" : req).then((r) => r || Response.error()))
  );
});

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
