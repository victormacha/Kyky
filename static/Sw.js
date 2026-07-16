// Service worker mínimo da Kyky.
// Objetivo principal: fazer o navegador considerar o site "instalável"
// (requisito técnico do PWA). Cache é bem simples de propósito - o app
// depende de dados sempre atualizados (chat, sessões), então não
// cacheamos respostas de API, só o "casco" estático do app.

const CACHE_NAME = "kyky-shell-v1";
const SHELL_FILES = ["/", "/static/style.css", "/static/app.js"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  // Só intercepta GETs pro "casco" do app - todo o resto (API, uploads)
  // vai direto pra rede, sem cache, pra nunca mostrar dado desatualizado.
  const url = new URL(event.request.url);
  const isShellRequest =
    event.request.method === "GET" && SHELL_FILES.includes(url.pathname);

  if (!isShellRequest) return;

  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
