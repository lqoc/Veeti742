// Grok Bot · phone web app (Android + iPhone). Plain JS, no framework, no build step.
// Two ways to run: the desk's own address (#t= login, full controls) or the deed.page website
// (#d= link, read-only files the desk publishes).
(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ------------------------------------------------------------ login: {URL}#t={APP_TOKEN} or website {URL}#d={DATA_PATH}
  let TOKEN = null, DATA = null;
  (function () {
    const mt = location.hash.match(/[#&]t=([^&]+)/), md = location.hash.match(/[#&]d=([^&]+)/);
    try {
      if (mt) { TOKEN = decodeURIComponent(mt[1]); localStorage.setItem("desk_token", TOKEN); }
      if (md) { DATA = decodeURIComponent(md[1]); localStorage.setItem("desk_data", DATA); }
    } catch (_) {}
    if (mt || md) history.replaceState(null, "", location.pathname + "#/home");
    try { TOKEN = TOKEN || localStorage.getItem("desk_token"); DATA = DATA || localStorage.getItem("desk_data"); } catch (_) {}
  })();
  // read-only website: opened from deed.page (or with a #d= link and no desk login)
  const STATIC = !!DATA && (/\.deed\.page$/.test(location.hostname) || !TOKEN);
  const READ_ONLY = "This website is read-only. Change it in the desk app (the login link from your VM).";
  const UA = navigator.userAgent || "";
  const IS_IOS = /iPhone|iPad|iPod/.test(UA);
  const IS_ANDROID = /Android/.test(UA);
  let installEvt = null;
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installEvt = e; });

  const S = {
    state: null, chart: null, range: "LIVE", activity: null, actNext: null, account: null, settings: null,
    decisions: {}, coin: {}, audit: {}, notif: null, replies: [], lastOk: null, offline: false, retryAt: 0,
    exec: null, authFail: !TOKEN && !STATIC, badData: false, stale: false,
  };
  try { S.range = localStorage.getItem("desk_range") || "LIVE"; } catch (_) {}

  // ------------------------------------------------------------ API
  async function staticApi(path, opts) {
    if (opts.method && opts.method !== "GET") { const e = new Error(READ_ONLY); e.status = 405; throw e; }
    const u = new URL(path, location.origin), p = u.pathname, q = u.searchParams;
    const last = p.split("/").pop();
    const map = [
      [/^\/api\/state$/, "state.json"], [/^\/api\/chart$/, `chart-${(q.get("range") || "LIVE").toUpperCase()}.json`],
      [/^\/api\/activity$/, "activity.json"], [/^\/api\/coin\//, `coin/${last}.json`], [/^\/api\/audit\//, `audit/${last}.json`],
      [/^\/api\/agents\/\w+\/decisions$/, `decisions-${p.split("/")[3]}.json`], [/^\/api\/account$/, "account.json"],
      [/^\/api\/settings$/, "settings.json"], [/^\/api\/notifications$/, "notifications.json"], [/^\/api\/search$/, "coins.json"],
    ];
    const hit = map.find(([re]) => re.test(p));
    if (!hit) { const e = new Error(READ_ONLY); e.status = 404; throw e; }
    let r;
    try { r = await fetch(`/d/${encodeURIComponent(DATA)}/${hit[1]}?t=${Date.now()}`, { cache: "no-store" }); }
    catch (e) { markOffline(); throw e; }
    if (r.status === 404) { const e = new Error("not on the website"); e.status = 404; throw e; }
    if (!r.ok) { markOffline(); throw new Error("HTTP " + r.status); }
    let j = await r.json();
    markOnline();
    if (p === "/api/activity") {
      const lim = +(q.get("limit") || 30), before = +(q.get("before") || 0);
      const rows = before ? j.rows.filter((x) => x.id < before) : j.rows;
      const page = rows.slice(0, lim);
      j = { rows: page, next: rows.length > lim ? page[page.length - 1].id : null };
    }
    if (p === "/api/search") {
      const t = (q.get("q") || "").toLowerCase();
      j = { rows: j.rows.filter((c) => c.mint === q.get("q") || (c.symbol || "").toLowerCase().includes(t) || (c.name || "").toLowerCase().includes(t)).slice(0, 10) };
    }
    if (p === "/api/state") checkFresh();
    return j;
  }

  async function checkFresh() {
    try {
      const r = await fetch(`/d/${encodeURIComponent(DATA)}/meta.json?t=${Date.now()}`, { cache: "no-store" });
      const m = await r.json();
      const age = Date.now() / 1000 - m.published_at;
      const el = $("#offline");
      S.stale = age > Math.max(240, 3 * (m.publish_s || 75));
      if (S.stale) {
        el.textContent = `Desk not publishing · last update ${new Date(m.published_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
        el.hidden = false;
      } else if (!S.offline) el.hidden = true;
    } catch (_) {}
  }

  async function api(path, opts = {}) {
    if (STATIC) return staticApi(path, opts);
    let r;
    try {
      r = await fetch(path, {
        method: opts.method || "GET",
        headers: { Authorization: "Bearer " + TOKEN, "Content-Type": "application/json" },
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        cache: "no-store",
      });
    } catch (e) { markOffline(); throw e; }
    if (r.status === 401) { S.authFail = true; route(); throw new Error("unauthorized"); }
    if (r.status >= 500 && r.status !== 503) { markOffline(); throw new Error("HTTP " + r.status); }
    const j = await r.json().catch(() => ({}));
    markOnline();
    if (!r.ok) { const e = new Error(j.error || j.detail || "HTTP " + r.status); e.status = r.status; throw e; }
    return j;
  }
  const post = (p, body) => api(p, { method: "POST", body: body || {} });

  // ------------------------------------------------------------ offline row
  let offTimer = null, backoff = 3;
  function markOnline() {
    S.lastOk = Date.now();
    backoff = 3;
    if (S.offline) { S.offline = false; if (!S.stale) $("#offline").hidden = true; clearInterval(offTimer); }
  }
  function markOffline() {
    if (!S.offline) {
      S.offline = true;
      S.retryAt = Date.now() + backoff * 1000;
      clearInterval(offTimer);
      offTimer = setInterval(drawOffline, 1000);
    }
    drawOffline();
  }
  function drawOffline() {
    const el = $("#offline");
    const n = Math.max(0, Math.ceil((S.retryAt - Date.now()) / 1000));
    const last = S.lastOk ? new Date(S.lastOk).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "never";
    el.textContent = `Desk offline · retrying in ${n}s · last update ${last}`;
    el.hidden = false;
    if (n <= 0) {
      backoff = Math.min(backoff * 2, 30);
      S.retryAt = Date.now() + backoff * 1000;
      refreshState();
    }
  }

  // ------------------------------------------------------------ formatting
  const usd = (v, d = 2) => (v == null || isNaN(v) ? "–" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const sgnUsd = (v) => (v == null ? "–" : (v >= 0 ? "+" : "-") + "$" + Math.abs(v).toFixed(2));
  const pct = (v, d = 2) => (v == null ? "–" : (v >= 0 ? "+" : "") + v.toFixed(d) + "%");
  const compact = (v) => {
    if (v == null) return "n/a";
    const a = Math.abs(v);
    if (a >= 1e9) return "$" + (v / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    if (a >= 1e3) return "$" + (v / 1e3).toFixed(1) + "K";
    return "$" + v.toFixed(2);
  };
  const px = (v) => (v == null ? "–" : "$" + window.Charts.fmtPx(v));
  const hm = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const short = (a) => (a ? a.slice(0, 4) + "..." + a.slice(-3) : "?");
  const initials = (s) => esc((s || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase() || "?");
  const logo = (img, sym, badge) => `<span class="logo" ${img ? `style="background-image:url('${esc(img)}')"` : ""}>${img ? "" : initials(sym)}${badge || ""}</span>`;

  // ------------------------------------------------------------ odometer
  function odoBuild(text) {
    return [...text].map((ch) => /\d/.test(ch)
      ? `<span class="d"><span class="strip" style="transform:translateY(-${+ch * 1.05}em)">${"0123456789".split("").map((n) => `<span>${n}</span>`).join("")}</span></span>`
      : `<span class="c">${esc(ch)}</span>`).join("");
  }
  function odoSet(el, text) {
    const prev = el._t;
    el._t = text;
    if (!prev || reduced() || prev.replace(/\d/g, "0") !== text.replace(/\d/g, "0")) { el.innerHTML = odoBuild(text); fit(el); return; }
    const strips = $$(".strip", el);
    const nd = [...text].filter((c) => /\d/.test(c));
    const od = [...prev].filter((c) => /\d/.test(c));
    let k = 0;
    for (let i = nd.length - 1; i >= 0; i--) {   // right to left, 30ms stagger
      if (nd[i] !== od[i]) {
        strips[i].style.transitionDelay = k * 30 + "ms";
        strips[i].style.transform = `translateY(-${+nd[i] * 1.05}em)`;
        k++;
      }
    }
  }
  function fit(el) {
    el.style.fontSize = "";
    const parent = el.parentElement;
    if (!parent) return;
    requestAnimationFrame(() => {
      let size = parseFloat(getComputedStyle(el).fontSize);
      const max = parent.clientWidth - 92;
      while (el.scrollWidth > max && size > 40) { size -= 2; el.style.fontSize = size + "px"; }
    });
  }

  // ------------------------------------------------------------ icons
  const ICON = {
    scanner: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>',
    auditor: '<svg viewBox="0 0 24 24"><path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/></svg>',
    analyst: '<svg viewBox="0 0 24 24"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
    executor: '<svg viewBox="0 0 24 24"><path d="M13 2L4 14h7l-1 8 9-12h-7z"/></svg>',
    risk: '<svg viewBox="0 0 24 24"><path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/></svg>',
    search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>',
    bell: '<svg viewBox="0 0 24 24"><path d="M6 8a6 6 0 1112 0c0 7 3 8 3 8H3s3-1 3-8M10 20a2 2 0 004 0"/></svg>',
    check: '<svg viewBox="0 0 12 12"><path d="M2 6.5l2.5 2.5L10 3.5"/></svg>',
    x: '<svg viewBox="0 0 12 12"><path d="M3 3l6 6M9 3l-6 6"/></svg>',
    back: '<svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg>',
    shield: '<svg viewBox="0 0 24 24"><path d="M12 2l9 3.5V12c0 5.3-3.8 9.3-9 10-5.2-.7-9-4.7-9-10V5.5z"/></svg>',
  };
  const COLOR = { scanner: "#64d2ff", auditor: "#0a84ff", analyst: "#ffd60a", executor: "#00d35a", risk: "#ff6b9a" };
  const NAMES = { scanner: "Scanner", auditor: "Auditor", analyst: "Analyst", executor: "Executor", risk: "Risk" };

  // ------------------------------------------------------------ router
  let current = null, timers = [];
  function clearTimers() { timers.forEach(clearInterval); timers = []; }
  function every(ms, fn) { timers.push(setInterval(fn, ms)); }

  function route() {
    const view = $("#view");
    clearTimers();
    if ((S.authFail || !TOKEN) && !STATIC || S.badData) {
      $("#tabbar").hidden = true;
      view.className = "view";
      view.innerHTML = `<div style="padding-top:30vh;text-align:center"><img src="/icons/icon-192.png" width="72" height="72" style="border-radius:18px" alt="">
        <h2 style="font:700 24px var(--display)">Grok Bot</h2>
        <p class="muted" style="max-width:310px;margin:0 auto;line-height:1.45">${S.badData ? "This website link is incomplete or old. Open the full link (it ends with #d=…) once more." :
          `Open your login link once on this phone, in Chrome or Samsung Internet:<br><span style="font-family:var(--mono);font-size:13px">{URL}#t=…</span><br>The desk prints it (LOGIN-LINK.bat on Windows, <span style="font-family:var(--mono);font-size:13px">azure.sh link</span> on Linux).`}</p></div>`;
      return;
    }
    $("#tabbar").hidden = false;
    const h = location.hash.replace(/^#\/?/, "") || "home";
    const [name, arg] = h.split("/");
    current = name;
    $$("#tabbar a").forEach((a) => a.classList.toggle("on", a.dataset.tab === name || (name === "coin" || name === "audit" || name === "notifications") && a.dataset.tab === "home"));
    view.className = "view" + (name === "home" ? " has-goal" : "");
    window.scrollTo(0, 0);
    ({ home: viewHome, agents: viewAgents, activity: viewActivity, account: viewAccount, coin: viewCoin, audit: viewAudit, notifications: viewNotifications }[name] || viewHome)(arg ? decodeURIComponent(arg) : null);
  }
  window.addEventListener("hashchange", route);

  // ------------------------------------------------------------ shared pieces
  function paperBadge() { return S.state && S.state.mode === "PAPER" ? '<span class="pill paper">PAPER</span>' : ""; }

  function actRow(r) {
    const kind = r.kind;
    const col = kind === "bought" ? "#0a84ff" : kind === "sold" ? "#00d35a" : kind === "stopped" ? "#ff453a" : "#636366";
    const badge = `<span class="badge" style="background:${col}">${kind === "rejected" ? ICON.x : ICON.check}</span>`;
    const v1cls = kind === "sold" ? (String(r.value).startsWith("-") ? "down" : "up") : kind === "stopped" ? "down" : kind === "rejected" ? "muted" : "";
    const href = kind === "rejected" ? `#/audit/${r.mint}` : `#/coin/${r.mint}`;
    const t2 = kind === "bought" ? r.subtitle : `${r.subtitle} · ${hm(r.ts)}`;
    return `<a class="row link" href="${href}">${logo(r.image, r.symbol || r.title.split(" ")[1], badge)}
      <div class="mid"><div class="t1">${esc(r.title)}</div><div class="t2">${esc(t2)}</div></div>
      <div class="rt"><div class="v1 ${v1cls}">${esc(r.value)}</div>${r.value2 ? `<div class="v2">${esc(r.value2)}</div>` : ""}</div></a>`;
  }

  function skeleton(n, h) { return Array.from({ length: n }, () => `<div class="sk" style="height:${h}px;margin:10px 0"></div>`).join(""); }

  function toast(text, ms = 2400) {
    const t = $("#toast");
    t.textContent = text; t.hidden = false;
    clearTimeout(t._h); t._h = setTimeout(() => (t.hidden = true), ms);
  }

  function sheet(html, onMount) {
    const el = $("#sheet");
    el.innerHTML = `<div class="panel">${html}</div>`;
    el.hidden = false;
    el.onclick = (e) => { if (e.target === el || e.target.closest("[data-close]")) closeSheet(); };
    if (onMount) onMount(el);
  }
  function closeSheet() { const el = $("#sheet"); el.hidden = true; el.innerHTML = ""; }

  function confirmType(title, text, word, onOk) {
    sheet(`<h3>${esc(title)}</h3><p>${text}</p><input class="confirm" autocapitalize="characters" autocomplete="off" placeholder="Type ${esc(word)}">
      <div class="err danger" style="min-height:20px;margin-top:8px"></div>
      <button class="cta green" data-ok>Confirm</button><button class="cta gray" data-close>Cancel</button>`, (el) => {
      const inp = $("input", el);
      setTimeout(() => inp.focus(), 50);
      $("[data-ok]", el).onclick = async () => {
        if (inp.value.trim() !== word) { $(".err", el).textContent = `Type ${word} exactly.`; return; }
        try { await onOk(); closeSheet(); } catch (e) { $(".err", el).textContent = e.message; }
      };
    });
  }

  function confirmTwice(t1, t2, onOk) {
    sheet(`<h3>${esc(t1)}</h3><button class="cta red" data-ok>Turn off</button><button class="cta gray" data-close>Keep it on</button>`, (el) => {
      $("[data-ok]", el).onclick = () => {
        sheet(`<h3>Are you sure?</h3><p>${esc(t2)}</p><button class="cta red" data-ok2>Yes, turn it off</button><button class="cta gray" data-close>Cancel</button>`, (el2) => {
          $("[data-ok2]", el2).onclick = async () => { await onOk(); closeSheet(); };
        });
      };
    });
  }

  function openUrl(u) {
    if (!u) return;
    if (u.startsWith("/")) { location.href = u; return; }
    window.open(u, "_blank", "noopener");
  }

  // ------------------------------------------------------------ mode switch (Home + Account) and results export
  function switchMode(target, after) {
    if (STATIC) { toast(READ_ONLY, 4000); return; }
    const cur = S.state ? S.state.mode : (S.account || {}).mode;
    if (target === cur) return;
    if (target === "LIVE") {
      const venue = (S.state || S.account || {}).venue;
      confirmType("Switch to LIVE", venue === "wallet"
        ? "The agents will buy and sell with real USDC from your imported wallet (score tiers $0.50–$4, max 3 open, keeps $1 USDC). Type LIVE to confirm."
        : "Buy on FOMO buttons become real trades. Type LIVE to confirm.", "LIVE",
        async () => { await post("/api/mode", { mode: "LIVE", confirm: "LIVE" }); await refreshState(); if (after) after(); });
    } else {
      post("/api/mode", { mode: "PAPER" }).then(async () => { toast("Paper trading"); await refreshState(); if (after) after(); })
        .catch((err) => toast(err.message, 4000));
    }
  }

  async function exportResults(hours) {
    try {
      toast("Preparing results...");
      const r = STATIC ? await fetch(`/d/${encodeURIComponent(DATA)}/export.json?t=${Date.now()}`, { cache: "no-store" })
        : await fetch(`/api/export?hours=${hours || 24}`, { headers: { Authorization: "Bearer " + TOKEN }, cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const blob = await r.blob();
      const m = (r.headers.get("content-disposition") || "").match(/filename="([^"]+)"/);
      const name = m ? m[1] : "grokbot-results.json";
      const file = new File([blob], name, { type: "application/json" });
      if (navigator.canShare && navigator.canShare({ files: [file] })) { await navigator.share({ files: [file], title: name }); return; }
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = name;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    } catch (e) { if (e.name !== "AbortError") toast("Export failed: " + e.message, 4000); }
  }

  // ------------------------------------------------------------ HOME
  function viewHome() {
    const v = $("#view");
    v.innerHTML = `
      <div class="nav"><a class="avatar" href="#/account" style="background-image:url('/icons/avatar.png')" aria-label="Account"></a>
        <a class="title" href="#/account">Grok Bot <span class="chev">›</span></a><div class="grow"></div>
        <button class="iconbtn" data-search aria-label="Search">${ICON.search}</button>
        <a class="iconbtn" href="#/notifications" aria-label="Notifications">${ICON.bell}<span class="dot" id="h-dot" hidden></span></a></div>
      <div class="ticker" id="h-ticker"></div>
      <div class="seg mode home-mode" id="h-mode"><button data-mode="LIVE">Live</button><button data-mode="PAPER">Paper</button></div>
      <a class="card" id="h-wallet" href="#/account" hidden style="display:flex;align-items:center;gap:10px;margin:6px 0 10px"><div style="flex:1"><div style="font-weight:600">Import your wallet</div><div class="muted" style="font-size:14px">Needed before LIVE can trade from it. Paper trading runs without it.</div></div><span class="chev">›</span></a>
      <div class="eyebrow" id="h-eyebrow">&nbsp;</div>
      <div class="balance-row"><div class="big odo" id="h-bal"><div class="sk" style="height:66px;width:220px"></div></div><span id="h-badge"></span></div>
      <div class="change" id="h-change"></div>
      <div class="breakdown" id="h-break"></div>
      <div class="chart-wrap"><canvas id="h-chart"></canvas><div class="chart-empty" id="h-empty" hidden>Waiting for the first trade</div></div>
      <div class="ranges" id="h-ranges">${["LIVE", "1H", "4H", "1D", "1W", "ALL"].map((r) => `<button data-range="${r}" class="${r === S.range ? "on" : ""}">${r}</button>`).join("")}</div>
      <div class="section-h"><h2>Agents</h2><span id="h-running"></span></div>
      <div class="carousel" id="h-agents">${skeleton(1, 300)}</div>
      <div class="section-h"><h2>Activity</h2><a href="#/activity">See all</a></div>
      <div class="list" id="h-act">${skeleton(3, 50)}</div>
      <div class="goal" id="h-goal"></div>`;
    $("[data-search]", v).onclick = openSearch;
    $("#h-mode").onclick = (e) => { const b = e.target.closest("[data-mode]"); if (b) switchMode(b.dataset.mode, drawHome); };
    $("#h-ranges").onclick = (e) => {
      const b = e.target.closest("[data-range]"); if (!b) return;
      S.range = b.dataset.range;
      try { localStorage.setItem("desk_range", S.range); } catch (_) {}
      $$("#h-ranges button").forEach((x) => x.classList.toggle("on", x === b));
      loadChart();
    };
    if (S.state) drawHome();
    refreshState(); loadChart(); loadActivityHome();
    every(10000, () => { refreshState(); loadChart(); });
    every(20000, loadActivityHome);
    window.onresize = () => { if (current === "home") drawChart(); };
  }

  function drawHome() {
    const st = S.state;
    if (!st || current !== "home") return;
    const b = st.breakdown;
    $("#h-dot").hidden = !st.unread;
    $("#h-wallet").hidden = STATIC || !!(st.wallet && st.wallet.pubkey);
    $$("#h-mode button").forEach((b) => { b.className = b.dataset.mode === st.mode ? "on " + st.mode.toLowerCase() : ""; });
    $("#h-eyebrow").textContent = `Equity · started with ${usd(b.start)}`;
    odoSet($("#h-bal"), usd(st.balance));
    $("#h-badge").innerHTML = paperBadge();
    const up = b.pnl >= 0;
    $("#h-change").innerHTML = `<span class="${up ? "up" : "down"}">${up ? "▲" : "▼"} ${sgnUsd(b.pnl)} (${pct(b.pnl_pct)})</span>
      <span class="pill ${b.multiplier >= 1 ? "solid-green" : "red"}">${b.multiplier.toFixed(2)}×</span>
      <span class="muted" style="font-size:15px">today ${pct(st.today_pct)}</span>`;
    $("#h-break").textContent = `realized ${sgnUsd(b.realized)} · unrealized ${sgnUsd(b.unrealized)} · fees −$${Math.abs(b.fees).toFixed(2)} · top-ups ${sgnUsd(b.topups)}`;
    $("#h-running").textContent = `${st.running} running`;
    $("#h-ranges").classList.toggle("down", !up);
    const tk = st.ticker || [];
    $("#h-ticker").innerHTML = tk.length ? `<div class="track">${(tk.concat(tk)).map((t) => `<a class="t" href="#/coin/${t.mint}"><b>${esc(t.symbol)}</b> ${px(t.price)} <span class="pill ${t.change >= 0 ? "green" : "red"}" style="height:22px;font-size:12px">${pct(t.change, 1)}</span></a>`).join("")}</div>` : "";
    drawCards($("#h-agents"));
    drawGoal();
  }

  function drawGoal() {
    const st = S.state; const el = $("#h-goal"); if (!el || !st) return;
    const m = st.breakdown.multiplier, base = st.breakdown.start + st.breakdown.topups;
    const ms = [1.25, 1.5, 2, 3, 4, 5];
    const next = ms.find((x) => x > m);
    // milestones evenly spaced, like a level track; below 1.25x sits at the left edge
    const anchors = [[0, 0], [1.25, 3], [1.5, 21.8], [2, 40.6], [3, 59.4], [4, 78.2], [5, 97]];
    const posOf = (x) => {
      x = Math.max(0, Math.min(5, x));
      for (let i = 1; i < anchors.length; i++) if (x <= anchors[i][0]) {
        const [a, pa] = anchors[i - 1], [b, pb] = anchors[i];
        return pa + ((x - a) / (b - a)) * (pb - pa);
      }
      return 97;
    };
    const done = m >= 2;
    el.innerHTML = `<div class="gi"><div class="gh"><b>Goal: 2×</b><span>${done ? "✓ Done · " + m.toFixed(2) + "× now" : next ? `next ${next}× · ${usd(next * base - st.balance)} to go` : ""}</span></div>
      <div class="track"><div class="base"></div><div class="prog" style="width:${posOf(m)}%"></div>
      ${ms.map((x) => `<span class="ms ${m >= x ? "hit" : ""}" style="left:${posOf(x)}%"></span>`).join("")}
      <span class="me" style="left:${posOf(m)}%"></span></div>
      <div class="labels">${ms.map((x) => `<span style="left:${posOf(x)}%">${x}×</span>`).join("")}</div></div>`;
  }

  async function loadChart() {
    try { S.chart = await api(`/api/chart?range=${S.range}`); drawChart(); } catch (_) {}
  }
  function drawChart() {
    const c = $("#h-chart"); if (!c || !S.chart) return;
    const st = S.state;
    const empty = !S.chart.marks.length && st && !st.open_positions && !st.breakdown.realized;
    $("#h-empty").hidden = !empty;
    window.Charts.line(c, empty ? { points: [], start: S.chart.start, marks: [], range: S.range }
      : { points: S.chart.points, start: S.chart.start, marks: S.chart.marks, range: S.range });
  }

  async function loadActivityHome() {
    try {
      const a = await api("/api/activity?limit=5");
      const el = $("#h-act"); if (!el) return;
      el.innerHTML = a.rows.length ? a.rows.map(actRow).join("") : `<div class="empty">Nothing yet tonight</div>`;
    } catch (_) {}
  }

  async function refreshState() {
    try {
      S.state = await api("/api/state");
      if (current === "home") drawHome();
      if (current === "agents") drawAgentsTab();
    } catch (e) { if (STATIC && e.status === 404 && !S.state) { S.badData = true; route(); } }
  }

  // ------------------------------------------------------------ AGENT CARDS
  function cardShell(a, statusText, body, foot) {
    const c = COLOR[a.name];
    return `<article class="acard" data-agent="${a.name}"><div class="head"><span class="ico" style="background:${c}">${ICON[a.name]}</span>
      <div><h3>${NAMES[a.name]}</h3><div class="st" style="color:${a.paused ? "#ff9f0a" : c}"><i></i><span style="color:var(--sec)">${esc(a.paused ? "paused" : statusText)}</span></div></div></div>
      <div class="body">${body}</div>${foot ? `<div class="foot">${foot}</div>` : ""}</article>`;
  }

  function cardHtml(a) {
    if (a.name === "scanner") {
      const feed = (a.feed || []).map((f) => f.verdict === "pass"
        ? `<div class="r pass"><span class="n">${esc(f.name || f.symbol || short(f.mint))} · liq ${compact(f.liquidity_usd)}</span><span class="why">→ auditor</span></div>`
        : `<div class="r kill"><span class="n">${esc(f.name || f.symbol || short(f.mint))}</span><span class="why">${esc(f.reason)}</span></div>`).join("");
      return cardShell(a, `${a.pools} pools`, feed ? `<div class="feed">${feed}</div>` : `<div class="muted">Listening for graduations</div>`,
        `<span>${a.waiting} in the waiting room</span><span>${a.connected ? "live" : "reconnecting"}</span>`);
    }
    if (a.name === "auditor") {
      const strips = Array.from({ length: 8 }, (_, i) => {
        const s = (a.strips || [])[i];
        const cls = a.running && (!s) ? (i === (a.strips || []).length ? "run" : "") : s ? (s.pass ? "ok" : "red") : "";
        return `<span class="${cls}"></span>`;
      }).join("");
      return cardShell(a, `${a.taken_apart} taken apart`,
        `<div class="strips">${strips}</div><div style="font-size:17px;font-weight:600">${a.coin ? esc(a.coin) + " · " : ""}<span class="${/→/.test(a.status) ? "up" : a.running ? "" : "down"}">${esc(a.status)}</span></div>`,
        `<span>${a.rejected} rejected</span>${a.mint ? `<a class="linkbtn" href="#/audit/${a.mint}">Open</a>` : ""}`);
    }
    if (a.name === "analyst") {
      const l = a.last;
      const body = `<div class="ringrow"><canvas class="ring" data-score="${l ? l.score : ""}" data-green="${a.green_from}"></canvas>
        <div><div style="font-weight:700;font-size:18px">${l ? esc(l.symbol || l.name || short(l.mint)) : "–"}</div>
        <div class="${l && l.passed ? "up" : "muted"}" style="font-size:15px">${a.scoring ? "scoring..." : l ? (l.passed ? "passed" : esc((l.failed || [])[0] || "rejected")) : "waiting for a coin"}</div>
        <div class="faint" style="font-size:13px;margin-top:2px">${a.mode === "rules" ? "rules mode" : "Jev"} · green from ${a.green_from}</div></div></div>`;
      return cardShell(a, "Scoring", body, `<span>${a.count} scored tonight</span>`);
    }
    if (a.name === "executor") {
      const ex = S.exec && S.exec.mint ? S.exec : a.executing;
      const last = a.last;
      let bar = "";
      if (ex && ex.status === "executing") bar = `<div class="bar" data-exec><div class="fill"></div><div class="lbl">Executing... 0%</div></div>`;
      else if (ex && ex.status === "filled") bar = `<div class="bar done"><div class="lbl">✓ Filled</div></div>`;
      const line = ex ? `${esc(ex.name || "")} · $${(+ex.size).toFixed(2)} · ${ex.pct}% of stack`
        : last ? `${esc(last.symbol || last.name || "")} · $${(+last.size_usd).toFixed(2)} · ${last.stack_pct || 0}% of stack` : "No trade yet";
      return cardShell(a, `${a.trades} trades`, `<div style="font-size:16px">${line}</div>${bar}`,
        `<span>${a.size_mode === "tiers" ? "score tiers $0.50–$4" : a.stack_pct + "% of stack"}</span><span>${a.venue === "wallet" ? "your wallet" : "FOMO"}</span>`);
    }
    // risk
    const open = (a.open || []).map((p) => `<div class="r"><span class="n">${esc(p.symbol || p.name)}</span><span class="${p.pnl >= 0 ? "up" : "down"}">${sgnUsd(p.pnl)}</span></div>`).join("");
    const last = a.last ? `<div style="font-size:16px">${esc(a.last_text)} · ${esc(a.last.symbol || a.last.name)} <span class="${a.last.pnl_usd >= 0 ? "up" : "down"}">${sgnUsd(a.last.pnl_usd)}</span></div>` : "";
    return cardShell(a, a.status, `${open ? `<div class="feed">${open}</div>` : ""}${last || (open ? "" : '<div class="muted">Nothing open</div>')}
      <div class="pills"><span class="pill mono red">stop -${a.stop_pct}%</span>${a.take_profit_pct > 0 ? `<span class="pill mono green">target +${a.take_profit_pct}%</span>` : ""}<span class="pill mono green">trail ${a.trail_pct}%</span></div>`,
      `<span>${a.count} exits tonight</span>${a.locked ? "<span>locked while open</span>" : ""}`);
  }

  function drawCards(el) {
    const st = S.state; if (!el || !st) return;
    const keep = el.scrollLeft;
    el.innerHTML = st.agents.map(cardHtml).join("");
    el.scrollLeft = keep;
    $$("canvas.ring", el).forEach((c) => window.Charts.ring(c, c.dataset.score === "" ? null : +c.dataset.score, +c.dataset.green));
    runExecBar();
  }

  function runExecBar() {
    const bar = $("[data-exec]"); if (!bar) return;
    const ex = S.exec || (S.state && S.state.agents[3].executing) || {};
    const started = (ex.started || Date.now() / 1000) * 1000;
    const paper = S.state && S.state.mode === "PAPER";
    cancelAnimationFrame(runExecBar._r);
    const tick = () => {
      const b = $("[data-exec]"); if (!b) return;
      const el = Date.now() - started;
      const p = paper ? Math.min(99, (el / 1200) * 100) : Math.min(95, 100 * (1 - Math.exp(-el / 8000)));
      $(".fill", b).style.width = p + "%";
      $(".lbl", b).textContent = `Executing... ${Math.round(p)}%`;
      runExecBar._r = requestAnimationFrame(tick);
    };
    tick();
  }

  // ------------------------------------------------------------ AGENTS TAB
  function viewAgents() {
    $("#view").innerHTML = `<div class="nav"><div class="title">Agents</div><div class="grow"></div><span class="muted" id="ag-run"></span></div><div id="ag-list">${skeleton(5, 160)}</div>`;
    refreshState().then(loadDecisions);
    every(10000, refreshState);
    every(15000, loadDecisions);
  }
  async function loadDecisions() {
    await Promise.all(["scanner", "auditor", "analyst", "executor", "risk"].map(async (n) => {
      try { S.decisions[n] = (await api(`/api/agents/${n}/decisions`)).rows; } catch (_) {}
    }));
    drawAgentsTab();
  }
  function drawAgentsTab() {
    const el = $("#ag-list"); if (!el || !S.state) return;
    $("#ag-run").textContent = `${S.state.running} running`;
    el.innerHTML = S.state.agents.map((a) => {
      const locked = a.name === "risk" && a.locked;
      const down = a.beat_age > 120;
      const rows = (S.decisions[a.name] || []).map((d) => `<div class="dec"><span class="tm">${d.ts ? hm(d.ts) : ""}</span>
        <span class="cn">${d.mint ? `<a href="${a.name === "auditor" ? "#/audit/" : "#/coin/"}${d.mint}">${esc(d.symbol || d.name || short(d.mint))}</a> <a class="faint" href="${esc(d.link)}" target="_blank" rel="noopener" aria-label="Open on FOMO">↗</a>` : ""}</span>
        <span class="vd ${esc(d.verdict)}">${esc(d.verdict)}</span><span class="rs">${esc(d.reason)}</span></div>`).join("");
      return `<section class="agent-full"><div class="top"><span class="ico" style="width:40px;height:40px;border-radius:20px;display:grid;place-items:center;background:${COLOR[a.name]}">${ICON[a.name].replace("<svg", '<svg style="width:22px;height:22px;fill:none;stroke:#000;stroke-width:2.2"')}</span>
        <div class="mid"><h3>${NAMES[a.name]}</h3><div class="muted" style="font-size:14px"><span class="statusdot ${a.paused ? "paused" : down ? "down" : ""}"></span>${a.paused ? "paused" : down ? "not responding" : "running"} · ${a.count} today</div></div>
        ${STATIC ? "" : `<button class="switch-hit" data-toggle="${a.name}" ${locked ? 'data-locked="1"' : ""} aria-label="Pause or resume"><span class="switch ${a.paused ? "" : "on"} ${locked ? "locked" : ""}"></span></button>`}</div>
        <div style="margin-top:10px">${rows || '<div class="empty" style="padding:14px">No decisions yet</div>'}</div>
        <a class="linkbtn" href="#/account/${a.name}">Settings ›</a></section>`;
    }).join("");
    el.onclick = async (e) => {
      const t = e.target.closest("[data-toggle]"); if (!t) return;
      const name = t.dataset.toggle, a = S.state.agents.find((x) => x.name === name);
      if (t.dataset.locked && !a.paused) { toast("Risk can't be paused while a position is open"); return; }
      try { await post(`/api/agents/${name}/${a.paused ? "resume" : "pause"}`); await refreshState(); } catch (err) { toast(err.message); }
    };
  }

  // ------------------------------------------------------------ ACTIVITY
  function viewActivity() {
    $("#view").innerHTML = `<div class="nav"><div class="title">Activity</div></div><div class="list" id="act">${skeleton(6, 50)}</div><button class="more" id="act-more" hidden>Load more</button>`;
    loadActivity(true);
    every(20000, () => loadActivity(true));
    $("#act-more").onclick = () => loadActivity(false);
  }
  async function loadActivity(fresh) {
    try {
      const a = await api(`/api/activity${!fresh && S.actNext ? "?before=" + S.actNext : ""}`);
      S.activity = fresh ? a.rows : (S.activity || []).concat(a.rows);
      S.actNext = a.next;
      const el = $("#act"); if (!el) return;
      el.innerHTML = S.activity.length ? S.activity.map(actRow).join("") : `<div class="empty">Nothing yet tonight</div>`;
      $("#act-more").hidden = !a.next;
    } catch (_) {}
  }

  // ------------------------------------------------------------ NOTIFICATIONS
  async function viewNotifications() {
    $("#view").innerHTML = `<div class="nav"><a class="back" href="#/home">${ICON.back}Home</a></div><h2 style="font:700 28px var(--display);margin:4px 0 12px">Notifications</h2><div class="list" id="nt">${skeleton(4, 50)}</div>`;
    try {
      const n = await api("/api/notifications");
      $("#nt").innerHTML = n.rows.length ? n.rows.map((r) => `<div class="row link" data-url="${esc(r.url || "")}"><div class="mid"><div class="t1" style="white-space:normal">${esc(r.body)}</div><div class="t2">${esc(r.title)} · ${hm(r.ts)}</div></div>${r.url ? `<span class="chev">›</span>` : ""}</div>`).join("")
        : `<div class="empty">No pushes yet</div>`;
      $("#nt").onclick = (e) => { const r = e.target.closest("[data-url]"); if (r && r.dataset.url) openUrl(r.dataset.url); };
      post("/api/notifications/read");
    } catch (_) {}
  }

  // ------------------------------------------------------------ SEARCH
  function openSearch() {
    sheet(`<h3>Search</h3><div class="search"><input placeholder="Name, symbol or mint" autocomplete="off" autocapitalize="off"></div><div class="list" id="sr"></div><button class="cta gray" data-close>Close</button>`, (el) => {
      const inp = $("input", el);
      setTimeout(() => inp.focus(), 50);
      let h;
      inp.oninput = () => {
        clearTimeout(h);
        h = setTimeout(async () => {
          const q = inp.value.trim();
          if (!q) { $("#sr").innerHTML = ""; return; }
          const r = await api(`/api/search?q=${encodeURIComponent(q)}`);
          $("#sr").innerHTML = r.rows.length ? r.rows.map((c) => `<a class="row link" href="#/coin/${c.mint}" data-close>${logo(c.image, c.symbol)}<div class="mid"><div class="t1">${esc(c.symbol || c.name || short(c.mint))}</div><div class="t2">${esc(c.name || "")} · ${short(c.mint)}</div></div></a>`).join("")
            : (q.length >= 32 ? `<a class="row link" href="#/coin/${esc(q)}" data-close><div class="mid"><div class="t1">Open ${short(q)}</div></div></a>` : `<div class="empty">No coin like that yet</div>`);
        }, 200);
      };
    });
  }

  // ------------------------------------------------------------ COIN SCREEN
  function viewCoin(mint) {
    $("#view").innerHTML = `<div class="nav"><a class="back" href="#/home">${ICON.back}Home</a><div class="grow"></div><span id="c-badge"></span></div><div id="coin">${skeleton(1, 60)}${skeleton(1, 300)}${skeleton(2, 80)}</div>`;
    if (S.coin[mint]) renderCoin(mint, S.coin[mint]);   // last good numbers first
    loadCoin(mint);
    every(15000, () => loadCoin(mint));
  }
  async function loadCoin(mint) {
    let c;
    try { c = await api(`/api/coin/${encodeURIComponent(mint)}`); }
    catch (e) { if (e.status === 404 && current === "coin" && !S.coin[mint]) $("#coin").innerHTML = `<div class="empty">This coin isn't on the website. The website shows the coins from the last hours of activity.</div>`; return; }
    S.coin[mint] = c;
    renderCoin(mint, c);
  }
  function renderCoin(mint, c) {
    if (current !== "coin" || !location.hash.endsWith(mint)) return;
    $("#c-badge").innerHTML = c.mode === "PAPER" ? '<span class="pill paper">PAPER</span>' : "";
    const p = c.position;
    const since = p ? p.since_entry_pct : c.p24;
    const shield = c.shield ? `<span class="chip ${c.shield === "8/8" ? "" : "red"}">${ICON.shield}${c.shield}</span>` : "";
    let posHtml = "";
    if (p) {
      const L = p.lines;
      const left = Math.max(0, L.timeout_at - Date.now() / 1000);
      const pnl = p.value - p.size_usd;
      posHtml = `<div class="card"><div class="line"><div><div style="font-weight:600">Your position</div><div class="muted" style="font-size:14px">${usd(p.size_usd)} in · Executor${p.mode === "LIVE" ? " · your wallet" : ""}</div></div>
        <div class="rt" style="text-align:right"><div style="font-weight:700">${usd(p.value)}</div><div class="${pnl >= 0 ? "up" : "down"}" style="font-size:14px">${sgnUsd(pnl)}</div></div></div></div>
        <div class="card"><div class="line"><span>Stop line</span><span class="down">${px(L.stop)} · -${p.stop_pct}%</span></div>
        <div class="line"><span>Trail line</span><span class="${L.trail ? "up" : "muted"}">${L.trail ? px(L.trail) : "starts at " + px(L.trail_starts)}</span></div>
        <div class="line"><span>Timeout</span><span class="muted">${Math.floor(left / 3600)}h ${Math.floor((left % 3600) / 60)}m left</span></div>
        ${p.tx ? `<div class="line"><span>Buy transaction</span><a class="linkbtn" href="${esc(p.tx)}" target="_blank" rel="noopener">Solscan ↗</a></div>` : ""}</div>`;
    }
    const wsell = !STATIC && p && p.mode === "LIVE" && p.venue === "wallet";
    const psell = !STATIC && p && p.mode === "PAPER";
    $("#coin").innerHTML = `<div class="coin-head"><span class="logo" ${c.image ? `style="background-image:url('${esc(c.image)}')"` : ""}></span>
        <div style="flex:1;min-width:0"><h1>${esc(c.name || short(mint))}</h1><div class="muted" style="font-size:15px">${esc(c.symbol || "")} · Solana${c.age_h != null ? " · " + (c.age_h < 1 ? Math.round(c.age_h * 60) + "m" : Math.round(c.age_h) + "h") + " old" : ""}</div></div>${shield}</div>
      <div class="price-big" style="margin-top:14px">${px(c.price)}</div>
      <div class="change ${since >= 0 ? "up" : "down"}">${since == null ? "" : (since >= 0 ? "▲ " : "▼ ") + Math.abs(since).toFixed(1) + "% " + (p ? "since entry" : "24h")}</div>
      <div class="candle-wrap"><canvas id="c-candles"></canvas></div>
      <div class="stats"><div><div class="k">Market cap</div><div class="v">${compact(c.mcap)}</div></div><div><div class="k">Liquidity</div><div class="v">${compact(c.liq)}</div></div>
        <div><div class="k">Holders</div><div class="v">${c.holders != null ? c.holders.toLocaleString() : "n/a"}</div></div><div><div class="k">24h volume</div><div class="v">${compact(c.v24)}</div></div></div>
      ${posHtml}
      ${wsell ? `<button class="cta red" data-sell="${p.id}">Sell now from wallet</button>` : ""}
      ${psell ? `<button class="cta gray" data-sell="${p.id}">Close PAPER position</button>` : ""}
      <a class="cta ${p ? (wsell ? "outline" : "red") : "green"}" href="${esc(c.coin_url)}" target="_blank" rel="noopener">${p ? "Sell on FOMO" : "Buy on FOMO"}</a>
      <a class="cta gray" href="#/audit/${esc(mint)}">Auditor · take apart</a>`;
    const lines = [];
    if (p) lines.push({ price: p.lines.current, color: "#ff9f0a", dash: true, label: "Risk trailing stop" });
    window.Charts.candles($("#c-candles"), { candles: c.candles, lines, entry: p ? { t: p.opened_at, price: p.entry_price } : null });
    $$("[data-sell]").forEach((b) => (b.onclick = () => confirmType("Sell " + (c.symbol || ""), p.mode === "LIVE" ? "Sells the whole position from your wallet at market (Jupiter), now." : "Closes the PAPER position at the current price.", "SELL",
      async () => { await post(`/api/positions/${b.dataset.sell}/sell`); toast("Selling..."); setTimeout(() => loadCoin(mint), 4000); })));
  }

  // ------------------------------------------------------------ AUDITOR SCREEN
  const LAYERS = ["Mint authority", "Freeze authority", "Dev wallet", "Top 10 holders", "Liquidity", "Pool age", "Bundled wallets", "Delegations"];
  const anim = { mint: null, results: {}, at: 0, busy: false, run: null };

  function viewAudit(mint) {
    $("#view").innerHTML = `<div class="nav"><a class="back" href="javascript:history.back()">${ICON.back}Back</a></div><div id="aud">${skeleton(1, 330)}${skeleton(4, 50)}</div>`;
    anim.mint = mint; anim.results = {}; anim.at = 0; anim.busy = false; anim.run = null;
    if (S.audit[mint]) renderAudit(mint, S.audit[mint], false);
    loadAudit(mint, !S.audit[mint]);
  }
  async function loadAudit(mint, play) {
    let a;
    try { a = await api(`/api/audit/${encodeURIComponent(mint)}`); }
    catch (e) { if (e.status === 404 && current === "audit" && !S.audit[mint]) $("#aud").innerHTML = `<div class="empty">No coin taken apart yet</div>`; return; }
    S.audit[mint] = a;
    renderAudit(mint, a, play);
  }
  function renderAudit(mint, a, play) {
    if (current !== "audit" || anim.mint !== mint) return;
    const sym = a.symbol || a.name || short(mint);
    const has = a.layers.some((l) => l.pass !== null);
    $("#aud").innerHTML = `<div class="coin-head">${logo(a.image, sym).replace("logo", "logo")}<div><h1>${esc(sym)}</h1><div class="muted" style="font-size:15px">Auditor · 8 layers</div></div></div>
      <div class="discs" id="discs">${LAYERS.map((_, i) => `<div class="disc" data-n="${i + 1}" style="top:${10 + i * 38}px;z-index:${10 - i}"><span class="mk"></span></div>`).join("")}<div class="beam" id="beam"></div></div>
      <div class="verdict" id="verdict">${has ? "" : '<div class="vt muted">No coin taken apart yet</div>'}</div>
      <div class="list layer-rows" id="lrows">${LAYERS.map((n, i) => `<div class="row" data-row="${i + 1}"><span class="n">${i + 1}</span><div class="mid"><div class="t1">${n}</div></div><span class="val faint">–</span></div>`).join("")}</div>
      <div class="list" style="margin-top:14px"><div class="field"><div class="lbl">Take apart before buying</div><button class="switch-hit" id="aud-toggle"><span class="switch ${a.enabled ? "on" : ""}"></span></button></div></div>
      ${has || STATIC ? "" : `<button class="cta gray" id="aud-run">Take apart now</button>`}
      <a class="cta green" href="${esc(a.coin_url)}" target="_blank" rel="noopener">Open on FOMO</a>`;
    $("#aud-toggle").onclick = () => {
      if (STATIC) { toast(READ_ONLY, 4000); return; }
      if (a.enabled) confirmTwice("Stop taking coins apart?", "Coins will go to the Analyst without the 8 safety layers.", async () => { await post("/api/settings", { key: "auditor.enabled", value: false }); loadAudit(mint, false); });
      else post("/api/settings", { key: "auditor.enabled", value: true }).then(() => loadAudit(mint, false));
    };
    const runBtn = $("#aud-run");
    if (runBtn) runBtn.onclick = async () => { runBtn.disabled = true; runBtn.textContent = "Taking apart..."; anim.results = {}; anim.at = 0; await post(`/api/audit/${encodeURIComponent(mint)}/run`); beamWait(1); };
    a.layers.forEach((l) => { if (l.pass !== null) anim.results[l.n] = l; else if (l.value === "not checked" && has) anim.results[l.n] = l; });
    anim.verdict = a.verdict;
    if (play && has) { anim.at = 0; stepBeam(); } else if (has) { for (let n = 1; n <= 8; n++) paintLayer(n); paintVerdict(); }
  }
  function beamWait(n) {
    const beam = $("#beam"); if (!beam) return;
    beam.style.opacity = 1; beam.style.top = 10 + (n - 1) * 38 + 22 + "px";
  }
  function paintLayer(n) {
    const r = anim.results[n]; if (!r) return;
    const disc = $(`.disc[data-n="${n}"]`), row = $(`[data-row="${n}"]`);
    if (!disc || !row) return;
    const cls = r.pass === true ? "ok" : r.pass === false ? "red" : "skip";
    disc.className = "disc " + cls;
    $(".mk", disc).innerHTML = r.pass === true ? ICON.check : r.pass === false ? ICON.x : "";
    row.className = "row " + cls;
    $(".val", row).className = "val";
    $(".val", row).textContent = r.value;
  }
  function paintVerdict() {
    const v = anim.verdict, el = $("#verdict"); if (!el || !v) return;
    el.innerHTML = `<div class="vt ${v.passed ? "up" : "down"}">${esc(v.title)}</div><div class="vs">${esc(v.sub)}</div>`;
    const beam = $("#beam"); if (beam) beam.style.opacity = 0;
  }
  // cyan beam: 350ms per layer top to bottom, then 150ms to green/red. Waits on a layer the server hasn't sent.
  function stepBeam() {
    if (anim.busy) return;
    const n = anim.at + 1;
    if (n > 8) { paintVerdict(); return; }
    const r = anim.results[n];
    if (reduced()) { for (let k = 1; k <= 8; k++) paintLayer(k); paintVerdict(); anim.at = 8; return; }
    if (!r) { beamWait(n); return; }
    if (r.pass === null) { for (let k = n; k <= 8; k++) paintLayer(k); anim.at = 8; paintVerdict(); return; }
    anim.busy = true;
    beamWait(n);
    setTimeout(() => {
      paintLayer(n);
      setTimeout(() => { anim.busy = false; anim.at = n; if (r.pass === false) { for (let k = n + 1; k <= 8; k++) paintLayer(k); anim.at = 8; paintVerdict(); } else stepBeam(); }, 150);
    }, 350);
  }

  // ------------------------------------------------------------ ACCOUNT
  async function viewAccount(focus) {
    $("#view").innerHTML = `<div class="nav"><div class="title">Account</div></div><div id="acc">${skeleton(6, 56)}</div>`;
    if (S.account && S.settings && S.state) drawAccount(focus);   // last good numbers first
    try {
      [S.account, S.settings] = await Promise.all([api("/api/account"), api("/api/settings")]);
      if (!S.state) S.state = await api("/api/state");
    } catch (_) { return; }
    drawAccount(focus);
  }

  function settingRow(s) {
    const id = "set-" + s.key.replace(/\./g, "-");
    if (s.type === "bool") return `<div class="field"><div class="lbl">${esc(s.label)}</div><button class="switch-hit" data-bool="${s.key}"><span class="switch ${s.value ? "on" : ""}"></span></button></div>`;
    if (s.type === "choice") return `<div class="setting"><div class="top"><span>${esc(s.label)}</span></div><div class="seg" data-choice="${s.key}">${s.choices.map((c) => `<button class="${c === s.value ? "on" : ""}" data-v="${c}">${c}</button>`).join("")}</div><div class="err"></div></div>`;
    if (s.type === "str") return `<div class="setting"><div class="top"><span>${esc(s.label)}</span></div><input type="text" class="wide" style="width:100%;height:38px;border-radius:8px;border:0;background:var(--card2);padding:0 10px" data-str="${s.key}" value="${esc(s.value)}" autocapitalize="off" autocomplete="off"><div class="err"></div></div>`;
    const step = s.step || (s.type === "int" ? 1 : +(Math.max((s.max - s.min) / 1000, 0.01)).toPrecision(1));
    return `<div class="setting"><div class="top"><span>${esc(s.label)}</span><span><input type="number" inputmode="decimal" id="${id}" data-num="${s.key}" value="${s.value}" min="${s.min}" max="${s.max}" step="any"><span class="unit">${esc(s.unit)}</span></span></div>
      <input type="range" data-slide="${s.key}" min="${s.min}" max="${s.max}" step="${step}" value="${s.value}"><div class="err"></div></div>`;
  }

  function settingRead(s) {
    const v = s.type === "bool" ? (s.value ? "on" : "off") : `${s.value}${s.unit && s.type !== "choice" && s.type !== "str" ? " " + s.unit : ""}`;
    return `<div class="field"><div class="lbl">${esc(s.label)}</div><span class="muted">${esc(v)}</span></div>`;
  }

  function drawAccountStatic(a) {
    const grouped = {};
    S.settings.settings.forEach((s) => { if (!s.guarded) (grouped[s.group] = grouped[s.group] || []).push(s); });
    const titles = { scanner: "Scanner", auditor: "Auditor", analyst: "Analyst", executor: "Executor", risk: "Risk", wallet: "Wallet trading", desk: "Desk" };
    $("#acc").innerHTML = `
      <p class="warn" style="margin:8px 4px">${READ_ONLY}</p>
      <div class="group-h">Mode</div>
      <div class="seg mode"><button class="${a.mode === "PAPER" ? "on paper" : ""}">PAPER</button><button class="${a.mode === "LIVE" ? "on live" : ""}">LIVE</button></div>
      <div class="group-h">Your wallet</div>
      <div class="list plain">${a.wallet ? `<div class="field"><div class="lbl">Address<small>${esc(short(a.wallet.pubkey))}</small></div></div>
        <div class="field"><div class="lbl">USDC</div><span>${(a.wallet_balances || {}).usdc != null ? usd(a.wallet_balances.usdc) : "–"}</span></div>` :
        `<div class="field"><div class="lbl">No wallet on the desk<small>Import it in the desk app: the trycloudflare link in "Grok Bot links.txt" on your VM's Desktop, Account › Import wallet. Never on this website.</small></div></div>`}</div>
      <div class="group-h">FOMO</div>
      <div class="list plain">${a.profile_url ? `<a class="field" href="${esc(a.profile_url)}" target="_blank" rel="noopener"><div class="lbl">Your FOMO profile</div><span class="chev">›</span></a>` : ""}
        <button class="field" style="width:100%;text-align:left" id="invite"><div class="lbl">Invite a friend</div><span class="chev">›</span></button></div>
      <div class="group-h">Results</div>
      <div class="list plain"><div class="field"><div class="lbl">Export results (last 24h)<small>Every coin, why it stopped, and what its price did over the next 6h.</small></div><button class="linkbtn" id="export-web">Download</button></div></div>
      <div class="group-h">Settings</div>
      ${Object.keys(grouped).map((g) => `<details class="sgroup"><summary>${titles[g] || g}<span class="muted">${grouped[g].length}</span></summary><div class="list plain">${grouped[g].map(settingRead).join("")}</div></details>`).join("")}`;
    $("#export-web").onclick = () => exportResults(24);
    $("#invite").onclick = async () => {
      const url = S.account.signup_url;
      if (navigator.share) { try { await navigator.share({ url }); return; } catch (_) { return; } }
      try { await navigator.clipboard.writeText(url); toast("Link copied"); } catch (_) { toast(url, 5000); }
    };
  }

  function pushRow(a, standalone) {
    const canPush = "PushManager" in window && "serviceWorker" in navigator && "Notification" in window;
    let right;
    if (canPush && (!IS_IOS || standalone)) right = `<button class="linkbtn" id="push-on">Enable notifications</button>`;
    else if (IS_IOS) right = `<span class="muted" style="font-size:13px;max-width:55%;text-align:right">Add to Home Screen first (Share › Add to Home Screen)</span>`;
    else right = `<span class="muted" style="font-size:13px;max-width:55%;text-align:right">This browser can't show notifications. Use Chrome or Samsung Internet.</span>`;
    const install = standalone ? "" : `<div class="field"><div class="lbl">Install on this phone<small>${installEvt ? "Adds Grok Bot to your home screen like an app." :
      IS_ANDROID ? "Chrome: ⋮ menu › Add to Home screen. Samsung Internet: ≡ menu › Add page to › Home screen." : "Share › Add to Home Screen."}</small></div>${installEvt ? `<button class="linkbtn" id="install">Install</button>` : ""}</div>`;
    return `${install}<div class="field"><div class="lbl">Notifications<small>${a.push_subs} device${a.push_subs === 1 ? "" : "s"} subscribed</small></div>${right}</div>`;
  }

  function drawAccount(focus) {
    const a = S.account, st = S.state;
    if (STATIC) { drawAccountStatic(a); return; }
    const grouped = {};
    S.settings.settings.forEach((s) => { if (!s.guarded) (grouped[s.group] = grouped[s.group] || []).push(s); });
    const standalone = window.navigator.standalone || window.matchMedia("(display-mode: standalone)").matches;
    const w = a.wallet, wb = a.wallet_balances || {};
    const titles = { scanner: "Scanner", auditor: "Auditor", analyst: "Analyst", executor: "Executor", risk: "Risk", wallet: "Wallet trading", desk: "Desk" };
    $("#acc").innerHTML = `
      <div class="group-h">Mode</div>
      <div class="seg mode" id="mode"><button class="${a.mode === "PAPER" ? "on paper" : ""}" data-mode="PAPER">PAPER</button><button class="${a.mode === "LIVE" ? "on live" : ""}" data-mode="LIVE">LIVE</button></div>
      <p class="muted" style="font-size:13px;margin:8px 4px">${a.mode === "PAPER" ? `PAPER simulates every trade. LIVE ${a.venue === "wallet" ? "buys and sells from your imported wallet" : "sends Buy on FOMO buttons"}. ${a.live_allowed ? "" : "LIVE unlocks after 1 full PAPER night (6h+, closed by the morning report)."}` : "LIVE: real money."}</p>
      ${a.ticket ? `<p class="${a.ticket.level === "warn" ? "danger" : "warn"}" style="font-size:14px;margin:4px">${esc(a.ticket.text)}</p>` : ""}

      <div class="group-h">Strategy</div>
      <div class="seg" id="strategy"><button data-s="sniper" class="${st.strategy === "sniper" ? "on" : ""}">Sniper</button><button data-s="strict" class="${st.strategy !== "sniper" ? "on" : ""}">Strict</button></div>
      <p class="muted" style="font-size:13px;margin:8px 4px">${st.strategy === "sniper"
        ? "Sniper: looks at a coin about 1 minute after it graduates, scores its first 5 minutes, takes profit at +" + st.take_profit_pct + "%, stops at -" + st.stop_pct + "%, trails after +8% and gives up after 20 minutes. Stops buying for the day after a $5 loss."
        : "Strict: the original rules. Waits 10 minutes, needs $40K liquidity, scores the first hour, rides winners with a trailing stop."}</p>

      <div class="group-h">Your wallet</div>
      <div class="list plain">${w ? `
        <div class="field"><div class="lbl">Address<small>${esc(w.pubkey)}</small></div><a class="linkbtn" href="${esc(a.wallet_link)}" target="_blank" rel="noopener">Solscan ↗</a></div>
        <div class="field"><div class="lbl">USDC</div><span>${wb.usdc != null ? usd(wb.usdc) : "–"}</span></div>
        <div class="field"><div class="lbl">SOL (fees)</div><span>${wb.sol != null ? wb.sol.toFixed(4) + " · " + usd(wb.sol_usd) : "–"}</span></div>
        <div class="field"><button class="linkbtn danger" id="w-remove">Remove wallet</button></div>`
        : `<div class="field"><div class="lbl">No wallet imported<small>Trades from your own Solana wallet: USDC in, USDC out, through Jupiter.</small></div></div>
           <div class="field"><button class="cta green" style="margin:0" id="w-import">Import wallet</button></div>`}</div>

      <div class="group-h">FOMO</div>
      <div class="list plain">
        ${a.profile_url ? `<a class="field" href="${esc(a.profile_url)}" target="_blank" rel="noopener"><div class="lbl">Your FOMO profile<small>@${esc(a.fomo_handle.replace(/^@/, ""))}</small></div><span class="chev">›</span></a>` : ""}
        <button class="field" style="width:100%;text-align:left" id="invite"><div class="lbl">Invite a friend</div><span class="chev">›</span></button>
      </div>

      <div class="group-h">Notifications</div>
      <div class="list plain">
        ${pushRow(a, standalone)}
        <div class="field"><div class="lbl">Test</div><button class="linkbtn" id="push-test">Send test push</button></div>
      </div>

      <div class="group-h">Talk to the desk</div>
      <div class="list plain"><form class="cmd" id="cmd"><input placeholder="status · why X · set stop to -8%" autocomplete="off" autocapitalize="off"><button>Send</button></form>
        <div class="replies" id="replies">${S.replies.map(replyHtml).join("")}</div></div>

      <div class="group-h">Results</div>
      <div class="list plain"><div class="field"><div class="lbl">Export results<small>Every coin, why it stopped, and what its price did over the next 6h. Send the file back to Claude to tune the settings.</small></div></div>
        <div class="seg" id="export" style="margin:0 14px 12px"><button data-hours="3">3h</button><button data-hours="6">6h</button><button data-hours="24">24h</button><button data-hours="168">7d</button></div></div>

      <div class="group-h">Analyst brain</div>
      <div class="list plain"><div class="field"><div class="lbl">Jev key<small>${a.jev ? "set · analyst mode " + esc(a.analyst_mode) : "not set · rules mode"}</small></div><button class="linkbtn" id="jev">${a.jev ? "Remove" : "Add key"}</button></div></div>

      <div class="group-h">Settings</div>
      ${Object.keys(grouped).map((g) => `<details class="sgroup" id="grp-${g}" ${focus === g ? "open" : ""}><summary>${titles[g] || g}<span class="muted">${grouped[g].length}</span></summary><div class="list plain">${grouped[g].map(settingRow).join("")}</div></details>`).join("")}

      <div class="group-h">Danger zone</div>
      <div class="list plain">
        <div class="field"><div class="lbl">Sell everything now</div><button class="linkbtn danger" id="sell-all">Sell all</button></div>
        <div class="field"><div class="lbl">Reset the run<small>"started with" = now</small></div><button class="linkbtn danger" id="reset">Reset</button></div>
      </div>
      <p class="faint" style="font-size:12px;margin:18px 4px">${esc(a.public_url || "")} · RPC ${esc(a.rpc_url)}</p>`;
    bindAccount();
    if (focus) { const el = $("#grp-" + focus); if (el) { el.open = true; el.scrollIntoView({ block: "start" }); } }
  }

  function replyHtml(r) {
    if (r.me) return `<div class="reply me">${esc(r.text)}</div>`;
    const btns = (r.buttons || []).map((b) => b.url ? `<a href="${esc(b.url)}" ${b.url.startsWith("/") ? "" : 'target="_blank" rel="noopener"'}>${esc(b.label)}</a>`
      : b.export ? `<button data-export="${esc(b.export)}">${esc(b.label)}</button>` : `<button data-action="${esc(b.action)}">${esc(b.label)}</button>`).join("");
    return `<div class="reply">${esc(r.text)}${btns ? `<div class="btns">${btns}</div>` : ""}</div>`;
  }

  function bindAccount() {
    $("#mode").onclick = (e) => {
      const b = e.target.closest("[data-mode]"); if (!b || b.dataset.mode === S.account.mode) return;
      switchMode(b.dataset.mode, () => viewAccount());
    };
    $("#export").onclick = (e) => { const b = e.target.closest("[data-hours]"); if (b) exportResults(+b.dataset.hours); };
    $("#strategy").onclick = async (e) => {
      const b = e.target.closest("[data-s]"); if (!b || b.dataset.s === S.state.strategy) return;
      try { const r = await post("/api/strategy", { strategy: b.dataset.s }); toast(`${b.dataset.s === "sniper" ? "Sniper" : "Strict"} on · ${r.changed.length} settings changed`); S.state = await api("/api/state"); viewAccount(); }
      catch (err) { toast(err.message, 4000); }
    };
    /* WALLET-IMPORT-START */
    const imp = $("#w-import");
    if (imp) imp.onclick = () => sheet(`<h3>Import wallet</h3>
      <p>Paste the private key of a <b>fresh wallet you use only for this bot</b>, holding only what you can afford to lose. Phantom: Settings › Manage Accounts › the account › Show Private Key. Never paste a recovery phrase.</p>
      <textarea class="secret" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="base58 private key"></textarea>
      <p class="warn">The key is encrypted on your desk server and never shown again. Anyone with access to that server can use it.</p>
      <div class="err danger" style="min-height:20px"></div>
      <button class="cta green" data-ok>Import</button><button class="cta gray" data-close>Cancel</button>`, (el) => {
      $("[data-ok]", el).onclick = async () => {
        const ta = $("textarea", el);
        try { const r = await post("/api/wallet/import", { secret: ta.value }); ta.value = ""; closeSheet(); toast("Imported " + short(r.pubkey)); viewAccount(); }
        catch (err) { $(".err", el).textContent = err.message; }
      };
    });
    /* WALLET-IMPORT-END */
    const rm = $("#w-remove");
    if (rm) rm.onclick = () => confirmType("Remove wallet", "Deletes the encrypted key from the desk. LIVE switches back to PAPER. Your funds stay in the wallet.", "REMOVE",
      async () => { await post("/api/wallet/remove", { confirm: "REMOVE" }); viewAccount(); });
    $("#invite").onclick = async () => {
      const url = S.account.signup_url;
      if (navigator.share) { try { await navigator.share({ url }); return; } catch (_) { return; } }
      try { await navigator.clipboard.writeText(url); toast("Link copied"); } catch (_) { toast(url, 5000); }
    };
    const ins = $("#install");
    if (ins) ins.onclick = async () => { if (!installEvt) return; installEvt.prompt(); try { await installEvt.userChoice; } catch (_) {} installEvt = null; viewAccount(); };
    const pon = $("#push-on");
    if (pon) pon.onclick = enablePush;
    $("#push-test").onclick = async () => { try { const r = await post("/api/push/test"); toast(r.webpush ? `Sent to ${r.webpush.sent} device(s)` : "Sent in-app"); } catch (e) { toast(e.message); } };
    $("#cmd").onsubmit = async (e) => {
      e.preventDefault();
      const inp = $("#cmd input"), text = inp.value.trim(); if (!text) return;
      inp.value = "";
      S.replies.push({ me: true, text });
      $("#replies").innerHTML = S.replies.map(replyHtml).join("");
      try { S.replies.push(await post("/api/command", { text })); } catch (err) { S.replies.push({ text: err.message }); }
      S.replies = S.replies.slice(-12);
      $("#replies").innerHTML = S.replies.map(replyHtml).join("");
    };
    $("#replies").onclick = async (e) => {
      const ex = e.target.closest("[data-export]"); if (ex) { exportResults(+ex.dataset.export); return; }
      const b = e.target.closest("[data-action]"); if (!b) return;
      confirmType("Sell now", "Sells this position at market now.", "SELL", async () => { await post(b.dataset.action); toast("Selling..."); });
    };
    $("#jev").onclick = () => {
      if (S.account.jev) { post("/api/jev", { key: null }).then(() => viewAccount()); return; }
      sheet(`<h3>Jev key</h3><p>From console.typesafe.ai › Keys. It is shown once there, copy it at once. Stored on the desk only.</p><input class="confirm" type="password" autocomplete="off"><div class="err danger" style="min-height:20px"></div><button class="cta green" data-ok>Save and test</button><button class="cta gray" data-close>Cancel</button>`, (el) => {
        $("[data-ok]", el).onclick = async () => {
          const r = await post("/api/jev", { key: $("input", el).value });
          if (r.test && r.test.error) $(".err", el).textContent = "Saved, but the test call failed: " + r.test.error;
          else { closeSheet(); toast("Jev connected"); viewAccount(); }
        };
      });
    };
    $("#sell-all").onclick = () => confirmType("Sell everything", "Exits every open position now (PAPER and LIVE).", "SELL", async () => { const r = await post("/api/sell_all", { confirm: "SELL" }); toast(`Selling ${r.selling.length}`); });
    $("#reset").onclick = () => confirmType("Reset the run", "Sets 'started with' to your equity now. PAPER also resets its cash to the PAPER start stack.", "RESET", async () => { await post("/api/run/reset", { confirm: "RESET" }); toast("Run reset"); });

    const acc = $("#acc");
    const save = async (key, value, holder) => {
      const err = holder && $(".err", holder);
      try {
        const r = await post("/api/settings", { key, value });
        if (err) err.textContent = "";
        if (r.changed[0]) toast(`${key.split(".")[1].replace(/_/g, " ")} ${r.changed[0].text}`);
        const s = S.settings.settings.find((x) => x.key === key); if (s && r.changed[0]) s.value = r.changed[0].new;
        if (r.ticket) toast(r.ticket.text, 4000);
        return true;
      } catch (e) { if (err) err.textContent = e.message; else toast(e.message); return false; }
    };
    acc.addEventListener("input", (e) => {
      const sl = e.target.closest("[data-slide]");
      if (sl) { const n = $(`[data-num="${sl.dataset.slide}"]`); if (n) n.value = sl.value; }
    });
    acc.addEventListener("change", async (e) => {
      const t = e.target;
      if (t.dataset.slide) await save(t.dataset.slide, t.value, t.closest(".setting"));
      else if (t.dataset.num) {
        const ok = await save(t.dataset.num, t.value, t.closest(".setting"));
        const sl = $(`[data-slide="${t.dataset.num}"]`); if (sl && ok) sl.value = t.value;
      } else if (t.dataset.str) await save(t.dataset.str, t.value, t.closest(".setting"));
    });
    acc.addEventListener("click", async (e) => {
      const bt = e.target.closest("[data-bool]");
      if (bt) {
        const s = S.settings.settings.find((x) => x.key === bt.dataset.bool);
        const go = async () => { if (await save(s.key, !s.value)) { s.value = !s.value; $(".switch", bt).classList.toggle("on", s.value); } };
        if (s.key === "auditor.enabled" && s.value) confirmTwice("Stop taking coins apart?", "Coins will go to the Analyst without the 8 safety layers.", go);
        else go();
        return;
      }
      const ch = e.target.closest("[data-choice] button");
      if (ch) {
        const seg = ch.parentElement;
        if (await save(seg.dataset.choice, ch.dataset.v, seg.closest(".setting"))) $$("button", seg).forEach((b) => b.classList.toggle("on", b === ch));
      }
    });
  }

  function b64ToBytes(b64) {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }
  async function enablePush() {
    try {
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) { toast("This browser has no web push"); return; }
      const perm = await Notification.requestPermission();
      if (perm !== "granted") { toast("Notifications not allowed"); return; }
      const reg = await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
      const { key } = await api("/api/push/key");
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
      await post("/api/push", sub.toJSON());
      toast("Notifications on");
      viewAccount();
    } catch (e) { toast("Push failed: " + e.message); }
  }

  // ------------------------------------------------------------ live stream (SSE over fetch, token in header)
  let refreshPending = null;
  function soon(fn, ms = 400) { clearTimeout(refreshPending); refreshPending = setTimeout(fn, ms); }
  function onEvent(ev) {
    const d = ev.data || {};
    switch (ev.kind) {
      case "audit_layer":
        if (current === "audit" && anim.mint === d.mint) {
          if (anim.run !== d.run) { anim.run = d.run; anim.results = {}; anim.at = 0; anim.verdict = null; }
          if (d.state === "done") { anim.results[d.n] = { n: d.n, pass: d.pass, value: d.value }; stepBeam(); }
          else if (anim.at + 1 === d.n && !anim.busy) beamWait(d.n);
        }
        soon(refreshState);
        break;
      case "audit":
        if (current === "audit" && anim.mint === d.mint) setTimeout(() => loadAudit(d.mint, false), 800 * 8);
        soon(refreshState);
        break;
      case "signal":
        if (d.status === "executing") S.exec = Object.assign({}, d);
        else S.exec = null;
        soon(refreshState, 100);
        break;
      case "fill":
        S.exec = null;
        soon(() => { refreshState(); if (current === "home") { loadChart(); loadActivityHome(); } }, 100);
        break;
      case "exit": case "activity":
        soon(() => { refreshState(); if (current === "home") { loadChart(); loadActivityHome(); } if (current === "activity") loadActivity(true); });
        break;
      case "push":
        toast(d.body, 3500);
        soon(refreshState);
        break;
      case "price":
        if (current === "coin" && location.hash.endsWith(d.mint)) soon(() => loadCoin(d.mint), 200);
        break;
      default:
        soon(refreshState, 800);
    }
  }
  async function stream() {
    if (STATIC) return;   // the website has no live stream: it re-reads the published files every 10-20s
    for (;;) {
      if (!TOKEN || S.authFail) { await sleep(5000); continue; }
      try {
        const r = await fetch("/api/stream", { headers: { Authorization: "Bearer " + TOKEN }, cache: "no-store" });
        if (r.status === 401) { S.authFail = true; route(); continue; }
        if (!r.ok || !r.body) throw new Error("stream " + r.status);
        markOnline();
        const reader = r.body.getReader(), dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
            chunk.split("\n").forEach((line) => { if (line.startsWith("data: ")) { try { onEvent(JSON.parse(line.slice(6))); } catch (_) {} } });
          }
        }
      } catch (_) { markOffline(); }
      await sleep(3000);
    }
  }

  // ------------------------------------------------------------ boot
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  route();
  stream();
})();
