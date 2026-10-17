// Zeichnet Rahmen und Info-Panels auf ein (transparentes) Canvas.

const COLORS = {
  mtg: '#ff8a3d',
  pokemon: '#ffd23f',
  pending: '#7dd3fc',
  failed: '#f87171',
};
const eur = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' });
const fmt = (v) => (v == null ? '–' : eur.format(v));
const dateFmt = new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit' });

function bounds(quad) {
  const xs = quad.map((p) => p[0]);
  const ys = quad.map((p) => p[1]);
  return { l: Math.min(...xs), r: Math.max(...xs), t: Math.min(...ys), b: Math.max(...ys) };
}
const rect = (b) => ({ x: b.l, y: b.t, w: b.r - b.l, h: b.b - b.t });
function overlap(a, b) {
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}

/** Abbildung Quellbild -> Canvas (Skalierung + Versatz, z. B. für object-fit: contain). */
export function fitTransform(srcW, srcH, dstW, dstH, mode = 'contain') {
  if (mode === 'fill') return { sx: dstW / srcW, sy: dstH / srcH, ox: 0, oy: 0 };
  const s = Math.min(dstW / srcW, dstH / srcH);
  return { sx: s, sy: s, ox: (dstW - srcW * s) / 2, oy: (dstH - srcH * s) / 2 };
}

export class OverlayRenderer {
  constructor(canvas, { scale = 1, showPending = true } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.scale = scale;
    this.showPending = showPending;
    this.smoothed = new Map(); // trackId -> geglättete Ecken für flüssige Bewegung
    this.sides = new Map(); // trackId -> gewählte Panelseite (stabil halten)
  }

  resize(w, h, dpr = window.devicePixelRatio || 1) {
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    this.dpr = dpr;
    this.w = w;
    this.h = h;
  }

  draw(state, transform, now = performance.now(), debugQuads = null) {
    const { ctx } = this;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);
    if (!state) return;
    const map = ([x, y]) => [x * transform.sx + transform.ox, y * transform.sy + transform.oy];

    if (debugQuads) {
      ctx.strokeStyle = 'rgba(255,255,255,0.35)';
      ctx.lineWidth = 1;
      for (const d of debugQuads) this.poly(d.quad.map(map)), ctx.stroke();
    }

    const alive = new Set();
    const items = [];
    for (const t of state.tracks) {
      alive.add(t.id);
      const target = t.quad.map(map);
      const prev = this.smoothed.get(t.id);
      const quad = prev ? prev.map((p, i) => [p[0] + (target[i][0] - p[0]) * 0.35, p[1] + (target[i][1] - p[1]) * 0.35]) : target;
      this.smoothed.set(t.id, quad);
      items.push({ t, quad, box: bounds(quad) });
    }
    for (const id of this.smoothed.keys()) if (!alive.has(id)) this.smoothed.delete(id);
    for (const id of this.sides.keys()) if (!alive.has(id)) this.sides.delete(id);

    // Erst alle Rahmen, dann die Panels so legen, dass sie weder Karten noch einander verdecken.
    const placed = [];
    for (const it of items.sort((a, b) => a.box.l - b.box.l)) {
      if (it.t.state === 'done' && it.t.card) {
        it.panel = this.placePanel(it, items, placed);
        placed.push(it.panel);
        this.drawFrame(it.quad, it.t, now);
      } else if (this.showPending) this.drawPending(it.quad, it.t, now);
    }
    for (const it of items) if (it.panel) this.drawPanel(it, now);
  }

  placePanel({ t, box }, items, placed) {
    const s = this.scale;
    const w = 270 * s;
    const h = this.panelHeight(t.card);
    const gap = 16 * s;
    const clamp = (r) => {
      const x = Math.max(8, Math.min(r.x, this.w - w - 8));
      const y = Math.max(8, Math.min(r.y, this.h - h - 8));
      return { x, y, w, h };
    };
    const options = {
      right: clamp({ x: box.r + gap, y: box.t }),
      left: clamp({ x: box.l - gap - w, y: box.t }),
      below: clamp({ x: box.l, y: box.b + gap }),
      above: clamp({ x: box.l, y: box.t - gap - h }),
    };
    const cost = (r) =>
      items.reduce((sum, o) => sum + overlap(r, rect(o.box)), 0) + placed.reduce((sum, p) => sum + 2 * overlap(r, p), 0);
    const current = this.sides.get(t.id);
    let side = current && cost(options[current]) === 0 ? current : null;
    if (!side) {
      side = Object.keys(options).reduce((a, b) => (cost(options[b]) < cost(options[a]) ? b : a));
      if (current && cost(options[current]) <= cost(options[side])) side = current; // nicht unnötig springen
    }
    this.sides.set(t.id, side);
    return { ...options[side], side };
  }

  poly(q) {
    const { ctx } = this;
    ctx.beginPath();
    q.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
  }

  drawPending(quad, t, now) {
    const { ctx } = this;
    const color = t.state === 'failed' ? COLORS.failed : COLORS.pending;
    ctx.save();
    ctx.setLineDash([10 * this.scale, 8 * this.scale]);
    ctx.lineDashOffset = -now / 30;
    ctx.lineWidth = 2.5 * this.scale;
    ctx.strokeStyle = color;
    this.poly(quad);
    ctx.stroke();
    ctx.restore();
    const [x, y] = quad[0];
    this.label(t.state === 'failed' ? 'nicht erkannt' : 'erkenne …', x, y - 8 * this.scale, color);
  }

  label(text, x, y, color) {
    const { ctx } = this;
    ctx.font = `600 ${13 * this.scale}px system-ui, sans-serif`;
    const w = ctx.measureText(text).width + 12 * this.scale;
    const h = 20 * this.scale;
    ctx.fillStyle = 'rgba(10,12,18,0.75)';
    ctx.beginPath();
    ctx.roundRect(x, y - h, w, h, 6 * this.scale);
    ctx.fill();
    ctx.fillStyle = color;
    ctx.fillText(text, x + 6 * this.scale, y - 6 * this.scale);
  }

  drawFrame(quad, t, now) {
    const { ctx, scale: s } = this;
    const color = COLORS[t.card.game] || COLORS.pending;
    const appear = Math.min(1, (now - (t.identifiedAt || 0)) / 350);
    ctx.save();
    ctx.globalAlpha = appear;
    ctx.shadowColor = color;
    ctx.shadowBlur = 18 * s;
    ctx.lineWidth = 3.5 * s;
    ctx.strokeStyle = color;
    ctx.lineJoin = 'round';
    this.poly(quad);
    ctx.stroke();
    ctx.restore();
  }

  drawPanel({ t, box, panel: p }, now) {
    const { ctx, scale: s } = this;
    const color = COLORS[t.card.game] || COLORS.pending;
    const appear = Math.min(1, (now - (t.identifiedAt || 0)) / 350);
    ctx.save();
    ctx.globalAlpha = appear;
    // Verbindungslinie von der Kartenkante zur nächsten Panelkante
    const cx = (box.l + box.r) / 2;
    const cy = (box.t + box.b) / 2;
    const from = {
      right: [box.r, box.t + 24 * s],
      left: [box.l, box.t + 24 * s],
      below: [cx, box.b],
      above: [cx, box.t],
    }[p.side];
    const to = [Math.max(p.x, Math.min(from[0], p.x + p.w)), Math.max(p.y, Math.min(from[1], p.y + p.h))];
    if (Math.hypot(to[0] - from[0], to[1] - from[1]) > 4 && !(cx > p.x && cx < p.x + p.w && cy > p.y && cy < p.y + p.h)) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5 * s;
      ctx.beginPath();
      ctx.moveTo(...from);
      ctx.lineTo(...to);
      ctx.stroke();
    }
    this.panel(t.card, p.x, p.y + (1 - appear) * 10 * s, p.w, p.h, color);
    ctx.restore();
  }

  panelHeight(card) {
    const s = this.scale;
    return (card.game === 'pokemon' ? 232 : 214) * s;
  }

  panel(card, x, y, w, h, color) {
    const { ctx, scale: s } = this;
    const pad = 14 * s;
    ctx.fillStyle = 'rgba(12,14,22,0.86)';
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, 12 * s);
    ctx.fill();
    ctx.fillStyle = color;
    ctx.fillRect(x, y + 12 * s, 3 * s, h - 24 * s);

    let cy = y + pad;
    // Kopf: Spiel + Name
    ctx.textBaseline = 'top';
    ctx.font = `700 ${10 * s}px system-ui, sans-serif`;
    ctx.fillStyle = color;
    ctx.fillText(card.game === 'mtg' ? 'MAGIC' : 'POKÉMON', x + pad, cy);
    cy += 14 * s;
    ctx.font = `700 ${17 * s}px system-ui, sans-serif`;
    ctx.fillStyle = '#fff';
    this.fitText(card.name, x + pad, cy, w - pad * 2);
    cy += 22 * s;
    ctx.font = `${12 * s}px system-ui, sans-serif`;
    ctx.fillStyle = 'rgba(255,255,255,0.65)';
    const setLine = `${card.setName || card.setCode} · #${card.number}${card.printCertain ? '' : ' · Druck geschätzt'}`;
    this.fitText(setLine, x + pad, cy, w - pad * 2);
    cy += 22 * s;

    // Preis
    const p = card.prices || {};
    const main = p.trend ?? p.foilTrend;
    ctx.font = `800 ${26 * s}px system-ui, sans-serif`;
    ctx.fillStyle = '#fff';
    ctx.fillText(fmt(main), x + pad, cy);
    const mainW = ctx.measureText(fmt(main)).width;
    ctx.font = `${11 * s}px system-ui, sans-serif`;
    ctx.fillStyle = 'rgba(255,255,255,0.6)';
    ctx.fillText(p.trend == null && p.foilTrend != null ? 'Trend (Foil)' : 'Cardmarket Trend', x + pad + mainW + 8 * s, cy + 4 * s);
    if (p.foilTrend != null && p.trend != null) ctx.fillText(`Foil ${fmt(p.foilTrend)}`, x + pad + mainW + 8 * s, cy + 17 * s);
    cy += 34 * s;

    if (card.game === 'pokemon') {
      ctx.font = `${12 * s}px system-ui, sans-serif`;
      ctx.fillStyle = 'rgba(255,255,255,0.8)';
      ctx.fillText(`ab ${fmt(p.low)} · Ø30T ${fmt(p.avg30)} · Ø7T ${fmt(p.avg7)}`, x + pad, cy);
      cy += 18 * s;
    }

    this.chart(card, x + pad, cy, w - pad * 2, 82 * s, color);
  }

  fitText(text, x, y, maxW) {
    const { ctx } = this;
    let t = text || '';
    if (ctx.measureText(t).width > maxW) {
      while (t.length > 1 && ctx.measureText(`${t}…`).width > maxW) t = t.slice(0, -1);
      t += '…';
    }
    ctx.fillText(t, x, y);
  }

  chart(card, x, y, w, h, color) {
    const { ctx, scale: s } = this;
    const hist = (card.history || []).filter((d) => d.price > 0);
    ctx.font = `${10.5 * s}px system-ui, sans-serif`;
    ctx.fillStyle = 'rgba(255,255,255,0.55)';

    if (hist.length < 2) {
      const p = card.prices || {};
      if (card.game === 'pokemon' && p.avg30 != null) {
        // Ohne eigene Historie: Durchschnittswerte als Balken (30T / 7T / 1T / Trend)
        const bars = [['Ø30T', p.avg30], ['Ø7T', p.avg7], ['Ø1T', p.avg1], ['Trend', p.trend]].filter((b) => b[1] != null);
        const max = Math.max(...bars.map((b) => b[1]));
        const bw = (w - (bars.length - 1) * 8 * s) / bars.length;
        bars.forEach(([lbl, v], i) => {
          const bh = Math.max(2, (v / max) * (h - 30 * s));
          const bx = x + i * (bw + 8 * s);
          ctx.fillStyle = i === bars.length - 1 ? color : 'rgba(255,255,255,0.28)';
          ctx.fillRect(bx, y + h - 14 * s - bh, bw, bh);
          ctx.fillStyle = 'rgba(255,255,255,0.65)';
          ctx.fillText(lbl, bx, y + h - 11 * s);
        });
      } else {
        ctx.fillText('Preisverlauf wird ab heute täglich aufgezeichnet', x, y + h / 2 - 6 * s);
      }
      return;
    }

    const prices = hist.map((d) => d.price);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = max - min || max * 0.1 || 1;
    const t0 = Date.parse(hist[0].date);
    const t1 = Date.parse(hist[hist.length - 1].date);
    const top = y + 14 * s;
    const ch = h - 30 * s;
    const px = (d) => x + ((Date.parse(d.date) - t0) / Math.max(1, t1 - t0)) * w;
    const py = (v) => top + ch - ((v - min) / range) * ch;

    const first = prices[0];
    const last = prices[prices.length - 1];
    const change = ((last - first) / first) * 100;
    ctx.fillStyle = change >= 0 ? '#4ade80' : '#f87171';
    ctx.font = `700 ${11 * s}px system-ui, sans-serif`;
    const days = Math.round((t1 - t0) / 864e5);
    ctx.fillText(`${change >= 0 ? '▲' : '▼'} ${Math.abs(change).toFixed(1)} % in ${days} Tagen`, x, y);

    const grad = ctx.createLinearGradient(0, top, 0, top + ch);
    grad.addColorStop(0, `${color}66`);
    grad.addColorStop(1, `${color}00`);
    ctx.beginPath();
    hist.forEach((d, i) => (i ? ctx.lineTo(px(d), py(d.price)) : ctx.moveTo(px(d), py(d.price))));
    ctx.lineTo(px(hist[hist.length - 1]), top + ch);
    ctx.lineTo(px(hist[0]), top + ch);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    hist.forEach((d, i) => (i ? ctx.lineTo(px(d), py(d.price)) : ctx.moveTo(px(d), py(d.price))));
    ctx.strokeStyle = color;
    ctx.lineWidth = 2 * s;
    ctx.lineJoin = 'round';
    ctx.stroke();

    ctx.font = `${10 * s}px system-ui, sans-serif`;
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.textAlign = 'right';
    ctx.fillText(`max ${fmt(max)} · min ${fmt(min)}`, x + w, y);
    ctx.fillText(dateFmt.format(t1), x + w, top + ch + 4 * s);
    ctx.textAlign = 'left';
    ctx.fillText(dateFmt.format(t0), x, top + ch + 4 * s);
  }
}
