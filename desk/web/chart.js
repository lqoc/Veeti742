// Hand-drawn canvas charts. No chart library. devicePixelRatio aware.
(function () {
  const C = {
    green: "#00d35a", red: "#ff453a", blue: "#0a84ff", orange: "#ff9f0a", cyan: "#64d2ff", yellow: "#ffd60a",
    ter: "rgba(235,235,245,0.3)", sec: "rgba(235,235,245,0.6)", card2: "#2c2c2e", sep: "rgba(84,84,88,0.55)",
  };
  const FONT = '600 12px -apple-system, "SF Pro Text", system-ui, sans-serif';
  const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function setup(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const r = canvas.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  }

  function rr(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // ---------------------------------------------------------------- line chart (Home)
  function drawLine(canvas, d, t, markAlpha) {
    const { ctx, w, h } = setup(canvas);
    const pts = d.points || [];
    const start = d.start;
    const padL = 16, padR = 16, top = 78, bottom = h - 14;
    let vals = pts.map((p) => p[1]);
    if (start != null) vals = vals.concat([start]);
    if (!vals.length) vals = [0, 1];
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < 1e-9) { hi += Math.max(1, Math.abs(hi) * 0.01); lo -= Math.max(1, Math.abs(lo) * 0.01); }
    const pad = (hi - lo) * 0.12;
    lo -= pad; hi += pad;
    const t0 = pts.length ? pts[0][0] : 0, t1 = pts.length ? pts[pts.length - 1][0] : 1;
    const X = (ts) => padL + (t1 === t0 ? (w - padL - padR) : ((ts - t0) / (t1 - t0)) * (w - padL - padR));
    const Y = (v) => bottom - ((v - lo) / (hi - lo)) * (bottom - top);
    // start line
    if (start != null) {
      ctx.save();
      ctx.setLineDash([4, 5]);
      ctx.strokeStyle = C.ter;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, Y(start)); ctx.lineTo(w, Y(start));
      ctx.stroke();
      ctx.restore();
    }
    if (pts.length < 2) return;
    // animated last point (slides in over 250ms)
    const shown = pts.map((p) => [X(p[0]), Y(p[1])]);
    if (t < 1 && d.prevLast) {
      const a = [X(d.prevLast[0]), Y(d.prevLast[1])];
      const b = shown[shown.length - 1];
      shown[shown.length - 1] = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    }
    const last = pts[pts.length - 1][1];
    const color = start != null && last < start ? C.red : C.green;
    // gradient fill under the line
    const g = ctx.createLinearGradient(0, top, 0, bottom);
    g.addColorStop(0, color === C.green ? "rgba(0,211,90,0.28)" : "rgba(255,69,58,0.28)");
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.beginPath();
    ctx.moveTo(shown[0][0], bottom);
    shown.forEach((p) => ctx.lineTo(p[0], p[1]));
    ctx.lineTo(shown[shown.length - 1][0], bottom);
    ctx.closePath();
    ctx.fillStyle = g;
    ctx.fill();
    ctx.beginPath();
    shown.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();
    // last point
    const lp = shown[shown.length - 1];
    ctx.beginPath(); ctx.arc(lp[0], lp[1], 7, 0, Math.PI * 2);
    ctx.fillStyle = color === C.green ? "rgba(0,211,90,0.25)" : "rgba(255,69,58,0.25)"; ctx.fill();
    ctx.beginPath(); ctx.arc(lp[0], lp[1], 3.5, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
    // marks above the line; never overlap: a later mark moves up a row
    const valAt = (ts) => {
      for (let i = 1; i < pts.length; i++) if (pts[i][0] >= ts) {
        const [ta, va] = pts[i - 1], [tb, vb] = pts[i];
        return tb === ta ? vb : va + ((ts - ta) / (tb - ta)) * (vb - va);
      }
      return last;
    };
    ctx.font = FONT;
    const placed = [];
    (d.marks || []).slice().sort((a, b) => a.ts - b.ts).forEach((m) => {
      if (m.ts < t0 || m.ts > t1 + 60) return;
      const cx = X(Math.min(m.ts, t1));
      const cy = Y(valAt(m.ts));
      const col = m.kind === "buy" ? C.blue : m.kind === "win" ? C.green : C.red;
      const label = (m.kind === "buy" ? "↑ " : m.kind === "win" ? "" : "↓ ") + m.text;
      const tw = ctx.measureText(label).width + 18, th = 24;
      let x = Math.min(Math.max(cx - tw / 2, 4), w - tw - 4);
      let row = 0, y;
      for (;;) {
        y = cy - 18 - th - row * (th + 4);
        const hit = placed.some((p) => p.row === row && !(x + tw < p.x || x > p.x + p.w));
        if (!hit || row > 5) break;
        row++;
      }
      if (y < 2) y = 2 + row * 0;
      placed.push({ x, w: tw, row });
      const alpha = markAlpha.get(m) ?? 1;
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.beginPath(); ctx.arc(cx, cy, 4, 0, Math.PI * 2); ctx.fillStyle = col; ctx.fill();
      rr(ctx, x, y, tw, th, 12); ctx.fillStyle = col; ctx.fill();
      ctx.fillStyle = m.kind === "win" ? "#000" : "#fff";
      ctx.textBaseline = "middle";
      ctx.fillText(label, x + 9, y + th / 2 + 0.5);
      ctx.restore();
    });
  }

  function line(canvas, d) {
    const prev = canvas._prev;
    const markKey = (m) => m.kind + m.ts + m.text;
    const oldMarks = new Set(prev ? prev.marks.map(markKey) : []);
    const alpha = new Map();
    const fresh = (d.marks || []).filter((m) => prev && !oldMarks.has(markKey(m)));
    const grew = prev && prev.points.length && d.points.length >= prev.points.length &&
      d.points[d.points.length - 1][0] > prev.points[prev.points.length - 1][0] && prev.range === d.range;
    canvas._prev = { points: d.points.slice(), marks: (d.marks || []).slice(), range: d.range };
    if (reduced() || (!grew && !fresh.length)) { drawLine(canvas, d, 1, alpha); return; }
    const dd = Object.assign({}, d, { prevLast: grew ? prev.points[prev.points.length - 1] : null });
    const t0 = performance.now();
    cancelAnimationFrame(canvas._raf);
    const step = (now) => {
      const el = now - t0;
      const t = Math.min(1, el / 250);
      const ease = 1 - Math.pow(1 - t, 3);
      fresh.forEach((m) => alpha.set(m, Math.min(1, el / 200)));
      drawLine(canvas, dd, ease, alpha);
      if (el < 260) canvas._raf = requestAnimationFrame(step);
    };
    canvas._raf = requestAnimationFrame(step);
  }

  // ---------------------------------------------------------------- candles (Coin screen)
  function fmtPx(v) {
    if (v == null) return "";
    if (v >= 1) return v.toFixed(4);
    if (v < 1e-5) return v.toPrecision(4).replace("e-", "e-");
    const s = v.toPrecision(4);
    return s.includes("e") ? Number(v).toFixed(10).replace(/0+$/, "") : s;
  }

  function candles(canvas, d) {
    const { ctx, w, h } = setup(canvas);
    const cs = d.candles || [];
    ctx.font = FONT;
    const widest = Math.max(0, ...cs.slice(-1).map((c) => ctx.measureText(fmtPx(c.c)).width),
      ...(d.lines || []).map((l) => (l.price ? ctx.measureText(fmtPx(l.price)).width : 0)));
    const padL = 10, padR = Math.max(64, widest + 30), top = 14;
    const volH = h * 0.18, priceBottom = h - volH - 14;
    if (!cs.length) {
      ctx.fillStyle = C.sec; ctx.font = '15px -apple-system, system-ui'; ctx.textAlign = "center";
      ctx.fillText("Collecting candles", w / 2, h / 2);
      return;
    }
    let lo = Math.min(...cs.map((c) => c.l)), hi = Math.max(...cs.map((c) => c.h));
    (d.lines || []).forEach((l) => { if (l.price && l.price > lo * 0.5 && l.price < hi * 2) { lo = Math.min(lo, l.price); hi = Math.max(hi, l.price); } });
    if (hi - lo < hi * 1e-6) { hi *= 1.01; lo *= 0.99; }
    const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
    const n = Math.max(cs.length, 30);
    const slot = (w - padL - padR) / n;
    const bw = Math.max(2, slot * 0.66);
    const X = (i) => padL + (n - cs.length + i) * slot + slot / 2;
    const Y = (v) => priceBottom - ((v - lo) / (hi - lo)) * (priceBottom - top);
    // grid
    ctx.strokeStyle = "rgba(84,84,88,0.25)"; ctx.lineWidth = 1;
    for (let i = 0; i <= 3; i++) { const y = top + ((priceBottom - top) * i) / 3; ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR + 8, y); ctx.stroke(); }
    const vmax = Math.max(1e-9, ...cs.map((c) => c.v || 0));
    cs.forEach((c, i) => {
      const up = c.c >= c.o;
      const col = up ? C.green : C.red;
      const x = X(i);
      ctx.strokeStyle = col; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, Y(c.h)); ctx.lineTo(x, Y(c.l)); ctx.stroke();
      const y1 = Y(Math.max(c.o, c.c)), y2 = Y(Math.min(c.o, c.c));
      ctx.fillStyle = col;
      ctx.fillRect(x - bw / 2, y1, bw, Math.max(1.5, y2 - y1));
      const vh = ((c.v || 0) / vmax) * (volH - 4);
      ctx.globalAlpha = 0.55;
      ctx.fillRect(x - bw / 2, h - vh - 2, bw, vh);
      ctx.globalAlpha = 1;
    });
    ctx.font = FONT; ctx.textBaseline = "middle";
    (d.lines || []).forEach((l) => {
      if (!l.price) return;
      const y = Y(l.price);
      if (y < top - 4 || y > priceBottom + 4) return;
      ctx.save();
      ctx.setLineDash(l.dash ? [5, 4] : []);
      ctx.strokeStyle = l.color; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR + 8, y); ctx.stroke();
      ctx.restore();
      if (l.label) { ctx.fillStyle = l.color; ctx.fillText(l.label, padL + 2, y - 9); }
      const txt = fmtPx(l.price), tw = ctx.measureText(txt).width + 12;
      rr(ctx, w - padR + 10, y - 10, tw, 20, 6); ctx.fillStyle = l.color; ctx.fill();
      ctx.fillStyle = "#000"; ctx.fillText(txt, w - padR + 16, y + 0.5);
    });
    // entry marker
    if (d.entry) {
      let idx = cs.findIndex((c) => c.t >= d.entry.t - 59);
      if (idx < 0) idx = cs.length - 1;
      const x = X(idx), y = Y(d.entry.price);
      const label = "Executor buy", tw = ctx.measureText(label).width + 14;
      ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fillStyle = C.blue; ctx.fill();
      const bx = Math.min(Math.max(x - tw / 2, 2), w - padR - tw);
      rr(ctx, bx, Math.min(y + 10, priceBottom - 2), tw, 20, 10); ctx.fillStyle = C.blue; ctx.fill();
      ctx.fillStyle = "#fff"; ctx.fillText(label, bx + 7, Math.min(y + 10, priceBottom - 2) + 10.5);
    }
    // current price
    const lastC = cs[cs.length - 1];
    const y = Y(lastC.c), txt = fmtPx(lastC.c), tw = ctx.measureText(txt).width + 12;
    rr(ctx, w - padR + 10, y - 10, tw, 20, 6); ctx.fillStyle = lastC.c >= lastC.o ? C.green : C.red; ctx.fill();
    ctx.fillStyle = "#000"; ctx.fillText(txt, w - padR + 16, y + 0.5);
  }

  // ---------------------------------------------------------------- score ring (Analyst)
  function ring(canvas, score, greenFrom) {
    const { ctx, w, h } = setup(canvas);
    const r = Math.min(w, h) / 2 - 6, cx = w / 2, cy = h / 2;
    ctx.lineWidth = 9; ctx.lineCap = "round";
    ctx.strokeStyle = C.card2;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
    if (score != null) {
      ctx.strokeStyle = score >= greenFrom ? C.green : C.yellow;
      ctx.beginPath(); ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + (Math.PI * 2 * Math.max(0, Math.min(100, score))) / 100); ctx.stroke();
    }
    ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = '700 30px -apple-system, "SF Pro Display", system-ui';
    ctx.fillText(score == null ? "–" : String(score), cx, cy + 1);
  }

  window.Charts = { line, candles, ring, fmtPx };
})();
