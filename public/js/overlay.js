// OBS-Modus: holt per obs-websocket Screenshots einer bestehenden Quelle und zeichnet
// das Overlay transparent darüber. Die Browserquelle muss deckungsgleich über der Quelle liegen.
import { loadOpenCv } from './detector.js';
import { CardReader } from './reader.js';
import { CardLens } from './lens.js';
import { OverlayRenderer, fitTransform } from './render.js';
import { ObsClient } from './obs.js';
import { loadImage } from './overlay-utils.js';

const params = new URLSearchParams(location.search);
const cfg = {
  source: params.get('source'),
  host: params.get('host') || 'localhost',
  port: params.get('port') || '4455',
  password: params.get('password') || '',
  fps: Math.min(15, Math.max(1, Number(params.get('fps')) || 5)),
  game: params.get('game') || 'auto',
  debug: params.get('debug') === '1',
  scale: Number(params.get('scale')) || null,
};

const statusEl = document.getElementById('status');
const setStatus = (text, error = false) => {
  statusEl.textContent = text;
  statusEl.classList.toggle('error', error);
};

const canvas = document.getElementById('overlay');
const renderer = new OverlayRenderer(canvas, { showPending: cfg.debug });
const sizeCanvas = () => {
  renderer.resize(window.innerWidth, window.innerHeight);
  renderer.scale = cfg.scale || Math.max(0.6, window.innerHeight / 1080);
};
sizeCanvas();
window.addEventListener('resize', sizeCanvas);

let state = null;

function animate() {
  if (state) renderer.draw(state, fitTransform(state.width, state.height, renderer.w, renderer.h, 'fill'), performance.now(), cfg.debug ? state.detections : null);
  requestAnimationFrame(animate);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function run() {
  if (!cfg.source) {
    setStatus('Parameter ?source=<Name der OBS-Quelle> fehlt', true);
    return;
  }
  setStatus('Lade Bilderkennung …');
  const [{ cv }, reader] = await Promise.all([loadOpenCv(), (async () => { const r = new CardReader(); await r.init(); return r; })()]);
  const lens = new CardLens(cv, reader, { game: cfg.game });
  if (cfg.debug) {
    window.__lens = lens;
    lens.addEventListener('debug', (e) => console.log('[ocr]', e.detail.trackId, JSON.stringify(e.detail.text)));
  }
  requestAnimationFrame(animate);

  for (;;) {
    let obs;
    try {
      setStatus(`Verbinde mit OBS (${cfg.host}:${cfg.port}) …`);
      obs = await new ObsClient(`ws://${cfg.host}:${cfg.port}`, cfg.password).connect();
      setStatus(cfg.debug ? `Verbunden – lese Quelle „${cfg.source}“` : '');
      for (;;) {
        const t0 = performance.now();
        const { imageData } = await obs.request('GetSourceScreenshot', {
          sourceName: cfg.source,
          imageFormat: 'jpg',
          imageCompressionQuality: 90,
        });
        const img = await loadImage(imageData);
        state = lens.process(img, img.naturalWidth, img.naturalHeight);
        await sleep(Math.max(0, 1000 / cfg.fps - (performance.now() - t0)));
      }
    } catch (err) {
      console.error(err);
      setStatus(`${err.message} – neuer Versuch in 3 s`, true);
      obs?.close();
      state = null;
      renderer.draw(null);
      await sleep(3000);
    }
  }
}

run().catch((err) => setStatus(err.message, true));
