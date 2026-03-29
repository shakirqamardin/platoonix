self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Keep behavior safe: network-first for app pages, cache fallback for static assets.
self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  // Cache static assets opportunistically.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.open("platoonix-static-v2").then(async (cache) => {
        const cached = await cache.match(request);
        if (cached) return cached;
        const response = await fetch(request);
        if (response && response.status === 200) {
          cache.put(request, response.clone());
        }
        return response;
      })
    );
    return;
  }

  // Network-first for dynamic pages/APIs.
  event.respondWith(
    fetch(request).catch(async () => {
      const cache = await caches.open("platoonix-static-v2");
      const offlineLogo = await cache.match("/static/icon-192.png");
      if (offlineLogo) return offlineLogo;
      throw new Error("Offline and no cache available");
    })
  );
});
