// Kamera-Modus im Browser.
import { loadOpenCv } from './detector.js';
import { CardReader } from './reader.js';
import { CardLens } from './lens.js';
import { OverlayRenderer, fitTransform } from './render.js';

const $ = (id) => document.getElementById(id);
const video = $('video');
const stage = $('stage');
const hint = $('hint');
const eur = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' });
const PREF_KEY = 'tcg-card-lens';

const prefs = (() => {
  try {
    return JSON.parse(localStorage.getItem(PREF_KEY)) || {};
  } catch {
    return {};
  }
})();
const savePrefs = () => {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify(prefs));
  } catch {}
};

const renderer = new OverlayRenderer($('overlay'));
const resize = () => renderer.resize(stage.clientWidth, stage.clientHeight);
new ResizeObserver(resize).observe(stage);
resize();

let lens = null;
let state = null;

// ---------- Kamera ----------
async function startCamera(deviceId) {
  assertCameraAvailable();
  video.srcObject?.getTracks().forEach((t) => t.stop());
  const stream = await navigator.mediaDevices.getUserMedia({
    // So hoch wie möglich: mehr Pixel pro Karte = bessere Texterkennung
    // Ohne Auswahl die Rückkamera bevorzugen (Handy)
    video: {
      ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: 'environment' } }),
      width: { ideal: 3840 },
      height: { ideal: 2160 },
    },
    audio: false,
  });
  video.srcObject = stream;
  await video.play();
  setupFocus(stream.getVideoTracks()[0]);
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput');
  const current = stream.getVideoTracks()[0].getSettings().deviceId;
  $('camera').innerHTML = devices.map((d, i) => `<option value="${d.deviceId}">${d.label || `Kamera ${i + 1}`}</option>`).join('');
  $('camera').value = current;
}

$('camera').addEventListener('change', (e) => {
  prefs.camera = e.target.value;
  savePrefs();
  startCamera(e.target.value).catch(showError);
});
$('game').value = prefs.game || 'auto';
$('game').addEventListener('change', (e) => {
  prefs.game = e.target.value;
  savePrefs();
  if (lens) lens.game = e.target.value;
});
$('debug').checked = !!prefs.debug;
$('debug').addEventListener('change', (e) => {
  prefs.debug = e.target.checked;
  savePrefs();
});

// ---------- Fokus ----------
// Nur verfügbar, wenn Kamera und Browser es unterstützen. Fixfokus-Kameras (z. B. Elgato Facecam MK.2,
// scharf ab ~30 cm) bieten das nicht an – dort hilft nur der richtige Abstand.
let focusInfo = 'unbekannt';
function setupFocus(track) {
  const caps = track.getCapabilities?.() || {};
  const box = $('focusBox');
  const slider = $('focus');
  box.hidden = true;
  if (caps.focusMode?.includes('continuous')) {
    track.applyConstraints({ advanced: [{ focusMode: 'continuous' }] }).catch(() => {});
    focusInfo = 'Autofokus';
  } else {
    focusInfo = caps.focusMode?.length ? caps.focusMode.join('/') : 'fix (nicht steuerbar)';
  }
  if (caps.focusDistance && caps.focusMode?.includes('manual')) {
    Object.assign(slider, { min: caps.focusDistance.min, max: caps.focusDistance.max, step: caps.focusDistance.step || 0.01 });
    slider.value = track.getSettings().focusDistance ?? caps.focusDistance.min;
    box.hidden = false;
    slider.oninput = () =>
      track.applyConstraints({ advanced: [{ focusMode: 'manual', focusDistance: Number(slider.value) }] }).catch(console.warn);
    focusInfo += ' · manuell einstellbar';
  }
}

// ---------- Diagnose ----------
let lastSeenCard = performance.now();
function updateDiag() {
  if (!state) return;
  const sizes = state.detections.map((d) => Math.hypot(d.quad[1][0] - d.quad[2][0], d.quad[1][1] - d.quad[2][1]));
  const biggest = sizes.length ? Math.round(Math.max(...sizes)) : 0;
  if (sizes.length) lastSeenCard = performance.now();
  const lines = [`${state.width}×${state.height} · Fokus: ${focusInfo} · Kartenumrisse: ${sizes.length}${biggest ? ` (Höhe ${biggest} px)` : ''}`];
  if (biggest && biggest < 400) lines.push('Karte sehr klein im Bild – Namenszeile kaum lesbar. Näher ran (im Schärfebereich bleiben) oder höhere Auflösung.');
  if (performance.now() - lastSeenCard > 5000)
    lines.push('Kein Kartenumriss gefunden – Karte flach vor ruhigen, kontrastreichen Hintergrund legen, Rand vollständig im Bild, nicht näher als der Schärfebereich.');
  $('diag').innerText = lines.join('\n');
}

$('snapBtn').addEventListener('click', async () => {
  if (!video.videoWidth) return;
  const c = document.createElement('canvas');
  c.width = video.videoWidth;
  c.height = video.videoHeight;
  c.getContext('2d').drawImage(video, 0, 0);
  const track = video.srcObject?.getVideoTracks()[0];
  const info = {
    camera: track?.label,
    settings: track?.getSettings?.(),
    capabilities: track?.getCapabilities?.(),
    detections: state?.detections.map((d) => d.quad.map((p) => p.map(Math.round))),
    tracks: lens?.tracks.map((t) => ({ state: t.state, card: t.card?.name, number: t.card?.number })),
    lastOcr: lens?.lastDebug?.text,
  };
  $('snapMsg').textContent = 'Speichere …';
  try {
    const res = await fetch('/api/snapshot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: c.toDataURL('image/jpeg', 0.92), info }),
    }).then((r) => r.json());
    $('snapMsg').textContent = res.file ? `Gespeichert: ${res.file}` : res.error;
  } catch (err) {
    $('snapMsg').textContent = err.message;
  }
});

function showError(err) {
  console.error(err);
  const messages = {
    NotAllowedError: 'Kein Kamerazugriff – bitte im Browser erlauben (Schloss-Symbol in der Adresszeile → Kamera).',
    NotFoundError: 'Keine Kamera gefunden.',
    NotReadableError: 'Kamera ist belegt – andere Apps (z. B. Kamera-App, OBS) schließen.',
    OverconstrainedError: 'Die gewählte Kamera ist nicht verfügbar.',
    InsecureContext: err.message,
  };
  hint.textContent = messages[err.name] || `Kamera-Fehler: ${err.name || ''} ${err.message}`;
}

// Ohne HTTPS (außer localhost) gibt es navigator.mediaDevices nicht – dann fragt der Browser gar nicht erst.
function assertCameraAvailable() {
  if (window.isSecureContext && navigator.mediaDevices?.getUserMedia) return;
  const httpsUrl = `https://${location.hostname}:4712${location.pathname}`;
  const err = new Error(
    window.isSecureContext
      ? 'Dieser Browser unterstützt keinen Kamerazugriff.'
      : `Kamera nur über HTTPS möglich – bitte ${httpsUrl} öffnen.`
  );
  err.name = 'InsecureContext';
  throw err;
}

// ---------- Erkannte Karten ----------
const seen = new Map();
function addCard(card) {
  seen.delete(`${card.game}:${card.id}`);
  seen.set(`${card.game}:${card.id}`, card);
  const list = [...seen.values()].reverse().slice(0, 30);
  $('cards').innerHTML = list
    .map((c) => {
      const price = c.prices.trend ?? c.prices.foilTrend;
      return `<li>
        ${c.image ? `<img src="${c.image}" alt="" loading="lazy">` : '<span></span>'}
        <div><div class="name">${escapeHtml(c.name)}</div>
          <div class="meta">${escapeHtml(c.setName || '')} · #${escapeHtml(c.number || '')}</div>
          ${c.cardmarketUrl ? `<a href="${c.cardmarketUrl}" target="_blank" rel="noopener">Cardmarket ↗</a>` : ''}</div>
        <div class="price">${price == null ? '–' : eur.format(price)}</div>
      </li>`;
    })
    .join('');
}
const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

// ---------- Manuelle Suche ----------
let searchTimer;
$('q').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const q = e.target.value.trim();
    if (q.length < 3) return ($('results').innerHTML = '');
    const results = await fetch(`/api/search?q=${encodeURIComponent(q)}&game=${$('game').value}`).then((r) => r.json());
    $('results').innerHTML = results
      .map(
        (r, i) =>
          `<li data-i="${i}"><span>${escapeHtml(r.name)}</span><span class="tag ${r.game}">${r.game === 'mtg' ? 'MTG' : `PKM ${r.lang?.toUpperCase()}`}</span></li>`
      )
      .join('');
    $('results').onclick = async (ev) => {
      const li = ev.target.closest('li');
      if (!li) return;
      const r = results[li.dataset.i];
      const card = await fetch('/api/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ game: r.game, key: r.key }),
      }).then((x) => x.json());
      if (card.error) return alert(card.error);
      addCard(card);
      // Liegt genau eine nicht erkannte Karte im Bild, bekommt sie diese Zuordnung.
      const unknown = lens?.tracks.filter((t) => t.state !== 'done') || [];
      if (unknown.length === 1) lens.assign(unknown[0].id, card);
    };
  }, 200);
});
$('searchForm').addEventListener('submit', (e) => e.preventDefault());

// ---------- OBS-Dialog ----------
const obsFields = ['oSource', 'oHost', 'oPort', 'oPassword', 'oFps', 'oDebug'];
function obsUrl() {
  const p = new URLSearchParams({ source: $('oSource').value || 'Webcam' });
  if ($('oHost').value !== 'localhost') p.set('host', $('oHost').value);
  if ($('oPort').value !== '4455') p.set('port', $('oPort').value);
  if ($('oPassword').value) p.set('password', $('oPassword').value);
  if ($('oFps').value !== '5') p.set('fps', $('oFps').value);
  if ($('game').value !== 'auto') p.set('game', $('game').value);
  if ($('oDebug').checked) p.set('debug', '1');
  return `${location.origin}/overlay.html?${p}`;
}
obsFields.forEach((id) => $(id).addEventListener('input', () => ($('oUrl').textContent = obsUrl())));
$('obsBtn').addEventListener('click', () => {
  $('oUrl').textContent = obsUrl();
  $('obsDialog').showModal();
});
$('oCopy').addEventListener('click', async () => {
  await navigator.clipboard.writeText(obsUrl());
  $('oCopy').textContent = 'Kopiert ✓';
  setTimeout(() => ($('oCopy').textContent = 'URL kopieren'), 1500);
});

// ---------- Schleifen ----------
function draw() {
  if (state) {
    const t = fitTransform(state.width, state.height, renderer.w, renderer.h, 'contain');
    renderer.draw(state, t, performance.now(), prefs.debug ? state.detections : null);
  }
  requestAnimationFrame(draw);
}

async function processLoop() {
  for (;;) {
    const t0 = performance.now();
    if (video.readyState >= 2 && video.videoWidth) {
      try {
        state = lens.process(video, video.videoWidth, video.videoHeight);
        updateDiag();
      } catch (err) {
        console.error(err);
      }
    }
    await new Promise((r) => setTimeout(r, Math.max(15, 125 - (performance.now() - t0))));
  }
}

async function main() {
  const cameraReady = startCamera(prefs.camera).catch((err) => startCamera().catch(showError));
  hint.textContent = 'Lade Bilderkennung (OpenCV + Tesseract) …';
  const reader = new CardReader();
  const [{ cv }] = await Promise.all([loadOpenCv(), reader.init(), cameraReady]);
  lens = new CardLens(cv, reader, { game: $('game').value });
  lens.addEventListener('card', (e) => addCard(e.detail.track.card));
  lens.addEventListener('debug', (e) => {
    const { card, text } = e.detail;
    const c = $('debugCard');
    c.width = card.width;
    c.height = card.height;
    c.getContext('2d').drawImage(card, 0, 0);
    $('debugText').textContent = `Name: ${text.nameText}\nFußzeile: ${text.bottomText}\nKonfidenz: ${Math.round(text.nameConfidence)}`;
  });
  if (video.srcObject) hint.textContent = '';
  requestAnimationFrame(draw);
  processLoop();
}

main().catch(showError);
