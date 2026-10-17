// Seitenleisten-Panel „Manometer“ für Home Assistant.
// Lädt panel.html (Oberfläche) und leitet alle Anfragen über die Home-Assistant-Anmeldung.
class ManometerLeserPanel extends HTMLElement {
  set hass(hass) {
    window.mlHass = hass;
    if (!this._started) { this._started = true; this._init(); }
  }
  set narrow(_v) {}
  set route(_v) {}
  set panel(_v) {}

  async _init() {
    window.mlFetch = (url, init = {}) => {
      const h = window.mlHass;
      if (h && typeof h.fetchWithAuth === 'function') return h.fetchWithAuth(url, init);
      const headers = new Headers(init.headers || {});
      if (h && h.auth && h.auth.data) headers.set('Authorization', 'Bearer ' + h.auth.data.access_token);
      return fetch(url, {...init, headers});
    };
    try {
      const src = new URL('panel.html', import.meta.url);
      src.searchParams.set('v', Date.now());
      const html = await (await fetch(src)).text();
      const doc = new DOMParser().parseFromString(html, 'text/html');
      if (!document.querySelector('meta[name=viewport]')) {
        const m = document.createElement('meta');
        m.name = 'viewport'; m.content = 'width=device-width, initial-scale=1';
        document.head.appendChild(m);
      }
      doc.head.querySelectorAll('style').forEach(st => document.head.appendChild(st));
      const scripts = [...doc.body.querySelectorAll('script')];
      scripts.forEach(x => x.remove());
      this.innerHTML = doc.body.innerHTML;
      for (const sc of scripts) {
        const n = document.createElement('script');
        n.textContent = sc.textContent;
        document.body.appendChild(n);
      }
      if (window.mlStart) window.mlStart();
    } catch (err) {
      this.innerHTML = `<p style="padding:16px;font-family:sans-serif">Manometer-Panel konnte nicht geladen werden: ${err}</p>`;
    }
  }
}
if (!customElements.get('manometer-leser-panel')) customElements.define('manometer-leser-panel', ManometerLeserPanel);
