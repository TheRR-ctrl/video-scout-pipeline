/* Service worker del panel.
 *
 * Está aquí por una razón concreta, no por moda: sin un service worker con
 * manejador de `fetch`, Chrome en Android no ofrece instalar la página, y
 * "Agregar a pantalla de inicio" deja un marcador con barra de navegador en
 * vez de una app. Con esto el panel se instala de verdad.
 *
 * El segundo motivo es más práctico. El panel vive en 127.0.0.1:8770, que
 * solo existe mientras `servidor.py` corre en Termux. Abrir el icono con el
 * servidor apagado enseñaba el dinosaurio de Chrome. Ahora enseña
 * `apagado.html`, que dice qué pasa, deja el comando a un toque y entra sola
 * en cuanto el panel vuelve a responder.
 *
 * Lo que NO se cachea, a propósito:
 *
 *   - `index.html`. Pesa 150 KB y cambia en cada `git pull`. Servirlo desde
 *     caché significaría un panel viejo hablando con una API nueva, que es
 *     peor que no tener app. La navegación va siempre a la red y solo cae a
 *     `apagado.html` cuando la red no está.
 *   - Todo `/api/`, `/video/`, `/audio/`, `/miniatura/` y `/previsualizacion/`.
 *     Son datos vivos: un estado cacheado haría creer que hay un render
 *     corriendo cuando ya terminó. El service worker ni los toca.
 */

const CACHE = "mesa-v1";

// Lo único que se guarda: la pantalla de "panel apagado" y los iconos, que
// es justo lo que hace falta cuando el servidor no está para servirlos.
const CONCHA = ["/apagado.html", "/icono-192.png", "/icono-512.png"];

self.addEventListener("install", (e) => {
  // skipWaiting: al actualizar el repo, la versión nueva manda ya, sin
  // esperar a que se cierren todas las pestañas del panel.
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(CONCHA)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Navegación: la red manda; si el servidor no está, la pantalla de apagado.
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req).catch(() =>
        caches.match("/apagado.html").then(
          (r) => r || new Response(
            "Panel apagado. Arráncalo con: panel",
            { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } }
          )
        )
      )
    );
    return;
  }

  // Los iconos y el manifiesto: de la caché si están, y si no de la red.
  // Se revalidan en segundo plano para que un icono nuevo entre al siguiente
  // arranque sin tener que subir la versión de la caché a mano.
  if (CONCHA.includes(url.pathname) || url.pathname === "/manifest.webmanifest") {
    e.respondWith(
      caches.match(req).then((guardado) => {
        const red = fetch(req).then((r) => {
          if (r && r.ok) caches.open(CACHE).then((c) => c.put(req, r.clone()));
          return r;
        });
        return guardado || red;
      }).catch(() => fetch(req))
    );
  }

  // Todo lo demás (API, videos, audio, miniaturas) pasa de largo: red directa.
});
