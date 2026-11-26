// RXT Marketing dashboard: a small single-page app with no build step.
// All data comes from /api/*, which signs requests to GitHub on the server.
(function () {
  "use strict";

  const $ = (sel, el = document) => el.querySelector(sel);
  const page = $("#page");
  const state = { me: null, config: null, cache: {}, timers: [] };

  // ---------------------------------------------------------------- utilities

  function esc(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  async function api(path, opts = {}) {
    const r = await fetch(path, {
      method: opts.method || "GET",
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      credentials: "same-origin",
    });
    let data = null;
    try { data = await r.json(); } catch { /* empty */ }
    if (r.status === 401 && !opts.allow401) { showSignIn(); throw new Error("please sign in"); }
    if (!r.ok) throw new Error((data && data.error) || `request failed (${r.status})`);
    return data;
  }

  async function cached(path, seconds = 30) {
    const hit = state.cache[path];
    if (hit && Date.now() - hit.at < seconds * 1000) return hit.data;
    const data = await api(path);
    state.cache[path] = { at: Date.now(), data };
    return data;
  }

  function bust() { state.cache = {}; }

  function toast(msg, ms = 3500) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { t.hidden = true; }, ms);
  }

  const nf = new Intl.NumberFormat("en-IN");
  const num = (v) => (v === null || v === undefined || Number.isNaN(v) ? "–" : nf.format(Math.round(v)));
  const num1 = (v) => (v === null || v === undefined || Number.isNaN(v) ? "–" : (Math.round(v * 10) / 10).toLocaleString("en-IN"));
  const cap = (s) => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1) : "");

  // India time (or whatever config.schedule.utc_offset says).
  let offsetMin = 330;
  function setOffset(off) {
    const m = /^([+-])(\d{2}):(\d{2})$/.exec(off || "+05:30");
    if (m) offsetMin = (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3]));
  }
  const tzLabel = () => (offsetMin === 330 ? "IST" : `UTC${offsetMin >= 0 ? "+" : "-"}${String(Math.floor(Math.abs(offsetMin) / 60)).padStart(2, "0")}:${String(Math.abs(offsetMin) % 60).padStart(2, "0")}`);
  function local(d) {
    const t = new Date(typeof d === "string" ? Date.parse(d) : d);
    return new Date(t.getTime() + offsetMin * 60e3); // read with getUTC*
  }
  const pad = (n) => String(n).padStart(2, "0");
  const localDay = (d = new Date()) => local(d).toISOString().slice(0, 10);
  const localHM = (d) => { const l = local(d); return `${pad(l.getUTCHours())}:${pad(l.getUTCMinutes())}`; };
  const localMinutes = (d = new Date()) => { const l = local(d); return l.getUTCHours() * 60 + l.getUTCMinutes(); };
  const hmToMin = (s) => { const [h, m] = String(s).split(":").map(Number); return h * 60 + m; };
  function fmtDate(d, withTime = true) {
    if (!d) return "–";
    const l = local(d);
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const s = `${l.getUTCDate()} ${months[l.getUTCMonth()]}`;
    return withTime ? `${s}, ${pad(l.getUTCHours())}:${pad(l.getUTCMinutes())}` : s;
  }
  function ago(d) {
    const s = (Date.now() - Date.parse(d)) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  }
  function dayLabel(day) {
    if (!day) return "";
    const today = localDay();
    const y = localDay(Date.now() - 86400e3);
    if (day === today) return "Today";
    if (day === y) return "Yesterday";
    return fmtDate(`${day}T06:30:00Z`, false);
  }

  function clearTimers() {
    state.timers.forEach(clearInterval);
    state.timers = [];
  }

  // ---------------------------------------------------------------- tooltip

  const tip = $("#tooltip");
  function showTip(html, ev) {
    tip.innerHTML = html;
    tip.hidden = false;
    moveTip(ev);
  }
  function moveTip(ev) {
    const pad = 14;
    let x = ev.clientX + pad;
    let y = ev.clientY + pad;
    const r = tip.getBoundingClientRect();
    if (x + r.width > window.innerWidth - 8) x = ev.clientX - r.width - pad;
    if (y + r.height > window.innerHeight - 8) y = ev.clientY - r.height - pad;
    tip.style.left = `${Math.max(8, x)}px`;
    tip.style.top = `${Math.max(8, y)}px`;
  }
  function hideTip() { tip.hidden = true; }
  document.addEventListener("pointerover", (ev) => {
    const el = ev.target.closest("[data-tip]");
    if (el) showTip(el.getAttribute("data-tip"), ev);
  });
  document.addEventListener("pointermove", (ev) => { if (!tip.hidden) moveTip(ev); });
  document.addEventListener("pointerout", (ev) => { if (ev.target.closest("[data-tip]")) hideTip(); });

  // ---------------------------------------------------------------- modal

  function openModal(html, onOpen) {
    $("#modal-body").innerHTML = html;
    $("#modal").hidden = false;
    document.body.style.overflow = "hidden";
    if (onOpen) onOpen($("#modal-body"));
    $(".modal-close").focus();
  }
  function closeModal() {
    const v = $("#modal-body video");
    if (v) { v.pause(); if (v.src.startsWith("blob:")) URL.revokeObjectURL(v.src); }
    $("#modal").hidden = true;
    $("#modal-body").innerHTML = "";
    document.body.style.overflow = "";
  }
  $(".modal-close").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#modal").hidden) closeModal(); });

  function confirmBox({ title, body, ok = "Confirm", danger = false }) {
    return new Promise((resolve) => {
      openModal(`<h2 id="modal-title">${esc(title)}</h2><div class="stack" style="gap:12px">${body}</div>
        <div class="row" style="margin-top:18px;justify-content:flex-end">
          <button class="btn btn-ghost" data-act="no">Cancel</button>
          <button class="btn ${danger ? "btn-danger" : ""}" data-act="yes">${esc(ok)}</button></div>`, (el) => {
        el.querySelector('[data-act="no"]').onclick = () => { closeModal(); resolve(false); };
        el.querySelector('[data-act="yes"]').onclick = () => { closeModal(); resolve(true); };
      });
      $(".modal-close").onclick = () => { closeModal(); resolve(false); };
    });
  }

  // ---------------------------------------------------------------- sign in

  async function boot() {
    try {
      state.config = await api("/api/config");
    } catch (e) {
      state.config = {};
    }
    setupSignIn();
    const errMatch = /signin-error=([^&]+)/.exec(location.hash);
    if (errMatch) {
      history.replaceState(null, "", "#top");
      showLanding();
      openSignIn(decodeURIComponent(errMatch[1]));
      return;
    }
    try {
      state.me = await api("/api/me", { allow401: true });
      startApp();
    } catch {
      showLanding();
    }
  }

  // The landing page is the signed-out face of the site. Every link that needs data
  // (the nav pills, the hero buttons) opens the sign-in sheet and goes there afterwards.
  let wanted = null;

  function showLanding() {
    clearTimers();
    closeModal();
    document.body.classList.remove("app", "menu-open");
    document.body.classList.add("landing");
    document.documentElement.classList.add("landing-lock");
    page.innerHTML = "";
    const h = location.hash;
    if (h.startsWith("#/") && h.length > 2) wanted = h;
  }

  function openSignIn(error) {
    $("#signin-error").hidden = !error;
    if (error) $("#signin-error").textContent = error;
    $("#signin").hidden = false;
    loadGoogle();
    setTimeout(() => $(".sheet-close").focus(), 0);
  }

  function closeSignIn() {
    $("#signin").hidden = true;
  }

  function setupSignIn() {
    const c = state.config || {};
    $("#github-btn").hidden = !c.github;
    $("#dev-btn").hidden = !c.devLogin;
    $("#signin-setup").hidden = !!c.googleClientId || c.github || c.devLogin;
    $("#dev-btn").onclick = async () => {
      try { state.me = await api("/api/auth/dev", { method: "POST", body: {} }); startApp(); } catch (e) { signInError(e.message); }
    };
    $(".sheet-close").onclick = closeSignIn;
    $("#signin").addEventListener("click", (e) => { if (e.target.id === "signin") closeSignIn(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#signin").hidden) closeSignIn(); });
    document.addEventListener("click", (e) => {
      if (state.me) return;
      const a = e.target.closest("a[href]");
      if (!a) return;
      const href = a.getAttribute("href");
      if (a.hasAttribute("data-signin") || href.startsWith("#/")) {
        e.preventDefault();
        if (href.startsWith("#/")) wanted = href;
        openSignIn();
      }
    });
  }

  function loadGoogle() {
    const c = state.config || {};
    if (!c.googleClientId || loadGoogle.loaded) return;
    loadGoogle.loaded = true;
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client";
    s.async = true;
    s.onload = () => {
      google.accounts.id.initialize({ client_id: c.googleClientId, callback: onGoogle, ux_mode: "popup", auto_select: false });
      google.accounts.id.renderButton($("#google-btn"), { theme: "filled_black", size: "large", text: "signin_with", shape: "rectangular", width: 280 });
    };
    s.onerror = () => signInError("Could not load Google sign-in. Check your connection and try again.");
    document.head.appendChild(s);
  }

  function showSignIn(error) {
    showLanding();
    openSignIn(error);
  }

  function signInError(msg) {
    $("#signin-error").textContent = msg;
    $("#signin-error").hidden = false;
  }

  async function onGoogle(resp) {
    try {
      state.me = await api("/api/auth/google", { method: "POST", body: { credential: resp.credential }, allow401: true });
      startApp();
    } catch (e) {
      signInError(e.message);
    }
  }

  function startApp() {
    closeSignIn();
    document.body.classList.remove("landing");
    document.body.classList.add("app");
    document.documentElement.classList.remove("landing-lock");
    if (wanted) { history.replaceState(null, "", wanted); wanted = null; }
    else if (!location.hash.startsWith("#/")) history.replaceState(null, "", "#/");
    const me = state.me;
    $("#me").innerHTML = `<div class="who">${me.picture ? `<img src="${esc(me.picture)}" alt="" referrerpolicy="no-referrer">` : ""}
      <span class="name" title="${esc(me.email || me.github)}">${esc(me.name || me.email)}</span></div>
      <span class="badge ${me.role === "admin" ? "brand" : ""}">${esc(cap(me.role))}</span>
      <button class="btn btn-ghost btn-sm" id="logout">Sign out</button>`;
    $("#logout").onclick = async () => {
      await api("/api/auth/logout", { method: "POST", body: {} }).catch(() => {});
      if (window.google?.accounts?.id) google.accounts.id.disableAutoSelect();
      state.me = null;
      bust();
      history.replaceState(null, "", "#top");
      showLanding();
    };
    route();
  }

  const canPost = () => state.me && (state.me.role === "admin" || state.me.role === "editor");
  const isAdmin = () => state.me && state.me.role === "admin";

  // ---------------------------------------------------------------- router

  const pages = {};
  window.addEventListener("hashchange", () => { if (state.me) route(); });

  function route() {
    clearTimers();
    hideTip();
    const name = (location.hash.replace(/^#\/?/, "").split(/[/?]/)[0]) || "overview";
    const fn = pages[name] || pages.overview;
    document.querySelectorAll(".sidebar a").forEach((a) => a.classList.toggle("active", a.dataset.page === (pages[name] ? name : "overview")));
    page.innerHTML = `<div class="empty"><span class="spinner"></span> Loading…</div>`;
    Promise.resolve(fn()).catch((e) => {
      page.innerHTML = `<div class="callout bad"><strong>Could not load this page</strong>${esc(e.message)}</div>`;
    });
    page.focus({ preventScroll: true });
    window.scrollTo(0, 0);
    updateAlertCount();
  }

  // The red number next to "Alerts" in the menu: open bot alerts (GitHub issues labelled bot-alert).
  function updateAlertCount() {
    cached("/api/alerts", 60).then((a) => {
      const el = $("#alert-count");
      if (!el) return;
      el.textContent = a.open.length;
      el.hidden = !a.open.length;
    }).catch(() => {});
  }

  // ---------------------------------------------------------------- shared data logic

  // Which calendar slot a time falls in (for older log entries without one).
  function slotOf(at, schedule) {
    const m = localMinutes(at);
    let best = null;
    for (const [name, s] of Object.entries(schedule.slots || {})) {
      const [a, b] = s.window.map(hmToMin);
      if (m >= a - 90 && m <= b + 90) best = best || name;
    }
    return best || "extra";
  }
  const whereDay = (p) => p.local_day || (p.at ? localDay(p.at) : p.day);
  const whereSlot = (p, schedule) => p.slot || (p.at ? slotOf(p.at, schedule) : "");

  function today(overview) {
    const day = localDay();
    const posts = (overview.published || []).filter((p) => whereDay(p) === day);
    const done = new Set(posts.map((p) => whereSlot(p, overview.schedule)));
    return { day, posts, done };
  }

  const activeRun = (runs, wf) => (runs || []).find((r) => (!wf || r.workflow === wf) && r.status !== "completed");

  // Mirrors slots.due() and main.publish() in the bot, so people see what a run will do
  // before they press the button. The bot makes the final call.
  function predict(overview, { slot, format, force }) {
    const s = overview.schedule || {};
    const maxDay = s.max_per_day || 3;
    const gapMin = s.min_gap_minutes || 150;
    const t = today(overview);
    const now = localMinutes();
    const latest = overview.latest;
    const waiting = !overview.autoPublish && latest && whereDay(latest) === t.day && !latest.posted ? latest.slot : null;
    const slots = Object.entries(s.slots || {}).map(([name, v]) => ({ name, start: hmToMin(v.window[0]), end: hmToMin(v.window[1]), window: v.window }));
    const lastAt = t.posts.map((p) => p.at).filter(Boolean).sort().pop();
    const gap = lastAt ? (Date.now() - Date.parse(lastAt)) / 60e3 : Infinity;
    const out = { runs: false, slot: null, why: "", notes: [], warnings: [], publishes: overview.autoPublish };

    if (slot && slot !== "next") {
      if (t.done.has(slot) && !force) {
        out.why = `The ${slot} slot already posted today. Tick "Post anyway" to post it again.`;
      } else {
        out.runs = true;
        out.slot = slot;
        out.why = t.done.has(slot) ? `Makes another ${slot} post (the ${slot} slot already posted today).` : `Makes the ${slot} slot's post now.`;
        if (t.posts.length >= maxDay && !force) {
          out.publishes = false;
          out.warnings.push(`There are already ${t.posts.length} posts today (the limit is ${maxDay}), so the bot will make the post but not publish it. Tick "Post anyway" to publish it too.`);
        }
      }
    } else if (t.posts.length >= maxDay && !force) {
      out.why = `There are already ${t.posts.length} posts today (the limit is ${maxDay}). Tick "Post anyway" for an extra post.`;
    } else if (gap < gapMin && !force) {
      out.why = `The last post went out ${Math.round(gap)} min ago and the bot waits ${gapMin} min between posts. Wait ${Math.ceil(gapMin - gap)} more min, or tick "Post anyway".`;
    } else {
      const open = slots.filter((x) => !t.done.has(x.name) && x.name !== waiting);
      const current = open.find((x) => x.start <= now && now < x.end);
      const next = open.find((x) => now < x.end);
      if (current) { out.runs = true; out.slot = current.name; out.why = `Makes the ${current.name} slot's post now (its window is open until ${current.window[1]} ${tzLabel()}).`; }
      else if (next) { out.runs = true; out.slot = next.name; out.why = `Makes the ${next.name} slot's post now, early (its window opens at ${next.window[0]} ${tzLabel()}).`; }
      else if (force) { out.runs = true; out.slot = "extra"; out.why = "Every slot today is done or closed, so this makes an extra post."; }
      else out.why = `Every slot today is done or closed. Tick "Post anyway" for an extra post.`;
    }

    if (out.runs) {
      const reelsToday = t.posts.filter((p) => p.format === "reel").length;
      const reelCfg = overview.reels || {};
      if (format === "reel" || (format === "auto" && (reelCfg.slots || []).includes(out.slot))) {
        out.notes.push("Reels take about 10 to 15 minutes to make, check and upload.");
        if (format === "reel" && reelsToday >= (reelCfg.per_day || 1)) out.warnings.push(`A Reel already went out today (the plan is ${reelCfg.per_day || 1} a day).`);
      } else out.notes.push("Carousels usually take about 5 minutes to make and publish.");
      out.notes.push("The bot picks the topic and the content type (educational, news, general trends or promotional) from what has been working, and keeps promotional posts to one a day.");
      if (force) out.warnings.push(`"Post anyway" skips the bot's own daily limits (${maxDay} a day, ${gapMin} min apart, one post per slot). Instagram's own cap of about 100 API posts a day still applies.`);
      if (!overview.autoPublish) out.publishes = false;
      if (!overview.autoPublish) out.notes.push("Manual approval is on, so the post will wait on the Overview page for someone to preview it and press Publish.");
      if (latest && !latest.posted && whereDay(latest) === t.day) out.warnings.push(`An unpublished draft ("${latest.topic}") is waiting. Making a new post replaces it as the latest draft.`);
      const busy = activeRun(overview.runs);
      if (busy) out.notes.push(`A ${busy.workflow} run is in progress, so this one will wait in line until it finishes.`);
    }
    return out;
  }

  function score(m, weights) {
    if (!m) return null;
    const w = Object.keys(weights || {}).length ? weights : { reach: 1, shares: 5, saved: 3, comments: 2, likes: 1 };
    return Object.entries(w).reduce((s, [k, v]) => s + v * (Number(m[k]) || 0), 0);
  }

  function runLabel(r) {
    const wf = { calendar: "Content calendar", publish: "Publish", weekly: "Weekly recap", health: "Health check" }[r.workflow] || r.name;
    const secs = r.started_at && r.updated_at ? (Date.parse(r.updated_at) - Date.parse(r.started_at)) / 1000 : null;
    const quick = r.workflow === "calendar" && r.event === "schedule" && r.status === "completed" && r.conclusion === "success" && secs !== null && secs < 100;
    let badge;
    if (r.status !== "completed") badge = `<span class="badge info"><span class="dot pulse"></span>${r.status === "queued" || r.status === "waiting" || r.status === "pending" ? "Waiting" : "Running"}</span>`;
    else if (r.conclusion === "success") badge = quick ? `<span class="badge">Checked</span>` : `<span class="badge good">Done</span>`;
    else if (r.conclusion === "skipped" || r.conclusion === "cancelled") badge = `<span class="badge">${cap(r.conclusion)}</span>`;
    else badge = `<span class="badge bad">Failed</span>`;
    const how = r.event === "schedule" ? "on schedule" : r.event === "workflow_dispatch" ? `started by hand` : r.event;
    return { wf, badge, how, quick, secs };
  }

  function runRow(r) {
    const l = runLabel(r);
    return `<div class="run" data-run="${r.id}">
      ${l.badge}
      <div><div class="title">${esc(l.wf)}${l.quick ? ' <span class="muted small">no post due</span>' : ""}</div>
      <div class="sub">${esc(fmtDate(r.created_at))} ${tzLabel()} · ${esc(l.how)}${l.secs ? ` · ${Math.max(1, Math.round(l.secs / 60))} min` : ""}</div>
      <div class="run-steps"></div></div>
      <div class="row"><button class="btn btn-ghost btn-sm" data-steps="${r.id}">Steps</button><a class="btn btn-ghost btn-sm" href="${esc(r.url)}" target="_blank" rel="noopener">GitHub</a></div>
    </div>`;
  }

  function stepIcon(s) {
    if (s.status !== "completed") return s.status === "in_progress" ? '<span class="spinner"></span>' : '<span class="muted">○</span>';
    return { success: '<span style="color:var(--good)">✓</span>', skipped: '<span class="muted">–</span>', failure: '<span style="color:var(--bad)">✕</span>', cancelled: '<span class="muted">⊘</span>' }[s.conclusion] || "•";
  }

  async function loadSteps(container, id) {
    const box = container.querySelector(".run-steps");
    box.innerHTML = '<span class="spinner"></span>';
    try {
      const { jobs } = await api(`/api/run?id=${id}`);
      box.innerHTML = jobs.map((j) => `<ul class="steps">${j.steps.filter((s) => !/^(Set up job|Complete job|Post )/.test(s.name))
        .map((s) => `<li><span class="ic">${stepIcon(s)}</span><span class="${s.conclusion === "skipped" ? "muted" : ""}">${esc(s.name)}</span></li>`).join("")}</ul>`).join("") || '<p class="muted small">No steps yet.</p>';
    } catch (e) {
      box.innerHTML = `<p class="error small">${esc(e.message)}</p>`;
    }
  }

  function wireRuns(root) {
    root.querySelectorAll("[data-steps]").forEach((b) => {
      b.onclick = () => {
        const row = b.closest(".run");
        const box = row.querySelector(".run-steps");
        if (box.innerHTML.trim()) { box.innerHTML = ""; return; }
        loadSteps(row, b.dataset.steps);
      };
    });
  }

  // ---------------------------------------------------------------- Overview

  pages.overview = async function () {
    const o = await cached("/api/overview", 20);
    setOffset(o.schedule.utc_offset);
    const t = today(o);
    const s = o.schedule;
    const now = localMinutes();
    const draft = o.latest && !o.latest.posted ? o.latest : null;
    const busy = activeRun(o.runs);
    const lastPost = (o.published || []).slice(-1)[0];
    const failed = (o.runs || []).filter((r) => r.status === "completed" && r.conclusion === "failure" && Date.now() - Date.parse(r.created_at) < 2 * 86400e3);

    const slotCards = Object.entries(s.slots || {}).map(([name, v]) => {
      const p = t.posts.find((x) => whereSlot(x, s) === name);
      const [a, b] = v.window.map(hmToMin);
      const open = !p && now >= a && now < b;
      const status = p ? `<span class="badge good">Posted ${p.at ? localHM(p.at) : ""}</span>`
        : open ? '<span class="badge info">Open now</span>' : now >= b ? '<span class="badge warn">Missed</span>' : '<span class="badge">Later</span>';
      return `<div class="slot ${p ? "done" : open ? "now" : ""}">
        <div class="slot-name">${esc(name)} ${status}</div>
        <div class="muted small">${esc(v.window[0])}–${esc(v.window[1])} ${tzLabel()}</div>
        ${p ? `<div class="small"><b>${esc(p.topic)}</b><br><span class="muted">${esc(cap(p.content_type || p.angle || ""))}${p.format ? " · " + esc(cap(p.format)) : ""}</span></div>` : ""}
      </div>`;
    }).join("");
    const extras = t.posts.filter((x) => !(s.slots || {})[whereSlot(x, s)]);

    page.innerHTML = `
      <div class="page-head"><div><h1>Today, <em>at a glance</em></h1><p class="muted">${esc(o.brand || "RXT")} ${o.handle ? "· " + esc(o.handle) : ""} · times in ${tzLabel()}</p></div>
        ${canPost() ? '<a class="btn" href="#/new">New post</a>' : ""}</div>
      ${o.alerts && o.alerts.length ? `<div class="callout bad" style="margin-bottom:16px"><strong>${o.alerts.length} open alert${o.alerts.length > 1 ? "s" : ""} from the bot</strong>
        <span>${o.alerts.slice(0, 3).map((a) => `${esc(alertName(a.title))} <span class="muted small">(${esc(ago(a.updated_at))})</span>`).join(" · ")} · <a href="#/alerts">See alerts</a></span></div>`
      : failed.length ? `<div class="callout bad" style="margin-bottom:16px"><strong>${failed.length} run${failed.length > 1 ? "s" : ""} failed in the last 2 days</strong><span><a href="#/runs">See Runs</a> for details.</span></div>` : ""}
      <div class="tiles">
        <div class="tile wide"><div class="label">Publishing mode</div>
          <div style="margin-top:8px"><div class="seg" id="mode" role="radiogroup" aria-label="Publishing mode">
            <button data-v="auto" role="radio" aria-checked="${o.autoPublish}" class="${o.autoPublish ? "on" : ""}" ${isAdmin() ? "" : "disabled"}>Auto-publish</button>
            <button data-v="manual" role="radio" aria-checked="${!o.autoPublish}" class="${o.autoPublish ? "" : "on"}" ${isAdmin() ? "" : "disabled"}>Manual approval</button></div></div>
          <div class="sub" style="margin-top:6px">${o.autoPublish ? "Posts go to Instagram by themselves" : "Each post waits here for a preview and Publish"}${isAdmin() ? "" : " · admins can change this"}</div></div>
        <div class="tile"><div class="label">Posted today</div><div class="value">${t.posts.length} <span class="muted" style="font-size:1rem">of ${s.max_per_day || 3}</span></div><div class="sub">${esc(dayLabel(t.day))}</div></div>
        <div class="tile"><div class="label">Last post</div><div class="value" style="font-size:1.05rem;margin-top:6px">${lastPost ? esc(ago(lastPost.at)) : "–"}</div><div class="sub">${lastPost ? esc(lastPost.topic) : ""}</div></div>
        <div class="tile"><div class="label">Bot right now</div><div class="value" style="font-size:1.05rem;margin-top:6px">${busy ? `<span class="badge info"><span class="dot pulse"></span>${esc(runLabel(busy).wf)}</span>` : '<span class="badge">Idle</span>'}</div><div class="sub">${busy ? "started " + esc(ago(busy.created_at)) : "checks every 30 min in each window"}</div></div>
        <div class="tile" id="followers-tile" ${o.liveEnabled ? "" : "hidden"}><div class="label">Followers</div><div class="value" id="followers">–</div><div class="sub">live from Instagram</div></div>
      </div>
      ${draft ? `<div class="card" id="draft"><div class="card-head"><h2>Waiting to publish</h2><span class="badge warn">Draft</span></div>
        <div class="row" style="align-items:flex-start;gap:16px">
          <div style="width:120px" class="thumb" id="draft-thumb"></div>
          <div style="flex:1;min-width:200px;gap:8px" class="stack">
            <div><b>${esc(draft.topic)}</b><div class="muted small">${esc(cap(draft.format || "carousel"))} · ${esc(cap(draft.content_type || ""))} · made for ${esc(draft.slot || "today")} ${esc(dayLabel(draft.local_day || draft.day))}</div></div>
            <div class="row"><button class="btn btn-sm" id="draft-view">Preview${canPost() ? " and publish" : ""}</button></div>
          </div></div></div>` : ""}
      <div class="card"><div class="card-head"><h2>Today's calendar</h2><span class="muted small">${esc(dayLabel(t.day))}</span></div>
        <div class="slots">${slotCards}</div>
        ${extras.length ? `<p class="small muted" style="margin:10px 0 0">Plus ${extras.length} extra post${extras.length > 1 ? "s" : ""} made with "Post anyway".</p>` : ""}
      </div>
      <div class="grid grid-2" style="margin-top:16px">
        <div class="card"><div class="card-head"><h2>Latest posts</h2><a href="#/posts" class="small">All posts</a></div><div class="post-grid" id="latest-posts" style="grid-template-columns:repeat(3,minmax(0,1fr))"><div class="empty"><span class="spinner"></span></div></div></div>
        <div class="card"><div class="card-head"><h2>Recent runs</h2><a href="#/runs" class="small">All runs</a></div><div id="recent-runs">${(o.runs || []).filter((r) => !runLabel(r).quick).slice(0, 5).map(runRow).join("") || '<p class="empty">No runs yet.</p>'}</div></div>
      </div>`;

    wireRuns($("#recent-runs"));
    document.querySelectorAll("#mode button").forEach((b) => {
      b.onclick = async () => {
        const enable = b.dataset.v === "auto";
        if (!isAdmin() || enable === o.autoPublish) return;
        const ok = await confirmBox(enable
          ? { title: "Switch to auto-publish?", body: "<p>The bot will publish each new post to Instagram as soon as it is made, up to 3 a day.</p>" + (draft ? "<p class=\"muted small\">The draft waiting now is not published by this; publish it from the Overview if you want it out.</p>" : ""), ok: "Switch to auto-publish" }
          : { title: "Switch to manual approval?", body: "<p>The bot keeps making posts on schedule, but each one waits on this Overview page. Someone previews it and presses <b>Publish</b> to send it to Instagram.</p><div class=\"callout warn\"><strong>Approve each draft before the next slot</strong>Only the newest draft can be published: when the next slot makes a new post, an unapproved draft is replaced.</div><p class=\"muted small\">This sets the AUTO_PUBLISH repository variable to false.</p>", ok: "Switch to manual approval" });
        if (!ok) return;
        try {
          await api("/api/autopublish", { method: "POST", body: { enabled: enable } });
          toast(enable ? "Auto-publish is on" : "Manual approval is on");
          bust();
          route();
        } catch (e) { toast(e.message, 6000); }
      };
    });
    if (draft) {
      const files = await cached("/api/posts", 30).catch(() => null);
      if (files) {
        const imgs = mediaFor(draft, files);
        $("#draft-thumb").innerHTML = imgs[0] ? `<img src="${esc(imgs[0])}" alt="">` : "";
        $("#draft-thumb").onclick = $("#draft-view").onclick = () => openPost({ ...draft, draft: true }, files, canPost() ? () => publishDraft(o, draft) : null);
      }
    }
    // latest posts + followers, after the main render
    cached("/api/posts", 30).then((p) => {
      const list = postList(p, o.schedule).slice(0, 3);
      const live = state.cache["/api/live"]?.data;
      $("#latest-posts").innerHTML = list.map((x) => postCard(x, p, live, true)).join("") || '<p class="empty">No posts yet.</p>';
      wirePostCards($("#latest-posts"), list, p);
    }).catch((e) => { $("#latest-posts").innerHTML = `<p class="error small">${esc(e.message)}</p>`; });
    if (o.liveEnabled) cached("/api/live", 300).then((l) => { if (l.account) $("#followers").textContent = num(l.account.followers_count); }).catch(() => {});
    if (busy) state.timers.push(setInterval(() => { bust(); if (location.hash.replace(/^#\/?/, "") === "" && $("#modal").hidden) route(); }, 30000));
  };

  async function publishDraft(o, draft) {
    publishDraft.force = false;
    const t = today(o);
    const slotDone = draft.slot && t.done.has(draft.slot);
    const full = t.posts.length >= (o.schedule.max_per_day || 3);
    const oldDay = (draft.local_day || draft.day) !== t.day;
    const needsForce = slotDone || full || (!draft.slot && t.posts.length > 0);
    const body = `<p><b>${esc(draft.topic)}</b> will go to Instagram now (${esc(draft.format || "carousel")}).</p>
      ${oldDay ? `<div class="callout warn">This draft was made for ${esc(dayLabel(draft.local_day || draft.day))}. Check it still fits today.</div>` : ""}
      ${needsForce ? `<div class="callout warn"><strong>Today's limit is already used</strong>${slotDone ? `The ${esc(draft.slot)} slot already posted today.` : full ? `There are already ${t.posts.length} posts today.` : "A post already went out today."}
        <label class="check" style="margin-top:6px"><input type="checkbox" id="pub-force"><span>Post anyway</span></label></div>` : ""}`;
    const ok = await confirmBox({ title: "Publish this post?", body, ok: "Publish to Instagram" });
    if (!ok) return;
    const force = needsForce ? !!publishDraft.force : false;
    try {
      const res = await api("/api/publish", { method: "POST", body: { force } });
      toast("Publishing started. It takes a few minutes.");
      bust();
      location.hash = `#/runs?watch=publish&since=${encodeURIComponent(res.started_at)}${res.run?.id ? "&id=" + res.run.id : ""}`;
    } catch (e) { toast(e.message, 6000); }
  }
  // the checkbox lives inside the confirm box, which is removed on close: remember its value
  document.addEventListener("change", (e) => { if (e.target.id === "pub-force") publishDraft.force = e.target.checked; });

  // ---------------------------------------------------------------- New post

  pages.new = async function () {
    const o = await cached("/api/overview", 15);
    setOffset(o.schedule.utc_offset);
    if (!canPost()) {
      page.innerHTML = `<h1>Make a <em>new post</em></h1><div class="callout">Your role is <b>viewer</b>, so you can see everything but not make posts. Ask an admin to make you an editor.</div>`;
      return;
    }
    const slots = Object.entries(o.schedule.slots || {});
    page.innerHTML = `
      <div class="page-head"><div><h1>Make a <em>new post</em></h1><p class="muted">Make a post right now instead of waiting for the calendar.</p></div></div>
      <div class="grid grid-2">
        <form class="card" id="new-form">
          <fieldset><legend>Slot</legend><div class="choices">
            <label class="choice"><input type="radio" name="slot" value="next" checked><span>Next open slot<small>recommended</small></span></label>
            ${slots.map(([n, v]) => `<label class="choice"><input type="radio" name="slot" value="${esc(n)}"><span>${esc(cap(n))}<small>${esc(v.window[0])}–${esc(v.window[1])}</small></span></label>`).join("")}
          </div></fieldset>
          <fieldset><legend>Format</legend><div class="choices">
            <label class="choice"><input type="radio" name="format" value="auto" checked><span>Let the bot decide<small>${(o.reels?.slots || []).length ? "Reel in the " + esc((o.reels.slots || []).join(", ")) + " slot" : "uses the plan"}</small></span></label>
            <label class="choice"><input type="radio" name="format" value="carousel"><span>Carousel<small>7 slides</small></span></label>
            <label class="choice"><input type="radio" name="format" value="reel"><span>Reel<small>20–35 s video</small></span></label>
          </div></fieldset>
          <fieldset><legend>Limits</legend>
            <label class="check"><input type="checkbox" name="force"><span><b>Post anyway</b><br><span class="muted small">Skip the bot's daily limits: ${o.schedule.max_per_day || 3} posts a day, ${o.schedule.min_gap_minutes || 150} min apart, one per slot.</span></span></label>
          </fieldset>
          <button class="btn" type="submit" id="go">Make the post</button>
        </form>
        <div class="stack">
          <div class="card" id="prediction"></div>
          <div class="card"><h2>Today so far</h2><div id="today-mini"></div></div>
        </div>
      </div>`;

    const t = today(o);
    $("#today-mini").innerHTML = t.posts.length ? `<table><tbody>${t.posts.map((p) => `<tr><td>${p.at ? localHM(p.at) : ""}</td><td>${esc(cap(whereSlot(p, o.schedule)))}</td><td>${esc(p.topic)}</td><td>${esc(cap(p.format || "carousel"))}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">Nothing posted yet today.</p>';

    const form = $("#new-form");
    const read = () => ({ slot: form.slot.value, format: form.format.value, force: form.force.checked });
    const update = () => {
      const p = predict(o, read());
      $("#prediction").innerHTML = `<h2>What will happen</h2>
        <div class="callout ${p.runs ? (p.publishes ? "good" : "info") : "warn"}"><strong>${p.runs ? (p.publishes ? "A new post will be made and published" : "A new post will be made, not published yet") : "Nothing will be posted"}</strong>${esc(p.why)}</div>
        ${p.warnings.length ? `<div class="callout warn" style="margin-top:10px"><strong>Heads up</strong><ul>${p.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
        ${p.notes.length ? `<ul class="small muted" style="margin:12px 0 0;padding-left:18px">${p.notes.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}`;
      $("#go").disabled = !p.runs;
      $("#go").textContent = p.runs ? (p.publishes ? "Make and publish the post" : "Make the post") : "Nothing to post";
      return p;
    };
    form.addEventListener("change", update);
    update();
    form.onsubmit = async (e) => {
      e.preventDefault();
      const input = read();
      const p = update();
      if (!p.runs) return;
      const ok = await confirmBox({
        title: p.publishes ? "Make and publish a post now?" : "Make a post now?",
        body: `<p>${esc(p.why)}</p><dl class="kv"><dt>Slot</dt><dd>${esc(cap(input.slot === "next" ? p.slot + " (next open)" : input.slot))}</dd><dt>Format</dt><dd>${esc(input.format === "auto" ? "Bot decides" : cap(input.format))}</dd><dt>Post anyway</dt><dd>${input.force ? "Yes" : "No"}</dd></dl>
          ${p.warnings.length ? `<div class="callout warn"><ul>${p.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
          ${p.publishes ? '<p class="small muted">It goes to Instagram automatically when it is ready.</p>' : ""}`,
        ok: p.publishes ? "Make and publish" : "Make the post",
      });
      if (!ok) return;
      $("#go").disabled = true;
      $("#go").innerHTML = '<span class="spinner"></span> Starting…';
      try {
        const res = await api("/api/trigger", { method: "POST", body: input });
        bust();
        location.hash = `#/runs?watch=calendar&since=${encodeURIComponent(res.started_at)}${res.run?.id ? "&id=" + res.run.id : ""}`;
      } catch (err) {
        toast(err.message, 7000);
        update();
      }
    };
  };

  // ---------------------------------------------------------------- Posts

  function folderOf(p) { return p.folder || p.day; }

  function mediaFor(p, files) {
    const list = (files.folders || {})[folderOf(p)] || [];
    const base = `${files.rawBase}posts/${folderOf(p)}/`;
    const slides = list.filter((f) => /^slide_\d+\.(jpe?g|png)$/i.test(f)).sort((a, b) => parseInt(a.slice(6), 10) - parseInt(b.slice(6), 10));
    if (slides.length) return slides.map((f) => base + f);
    const cover = list.find((f) => /^cover\.(jpe?g|png)$/i.test(f));
    return cover ? [base + cover] : [];
  }

  function postList(files, schedule) {
    const out = (files.published || []).map((p) => ({ ...p, posted: true, slot: whereSlot(p, schedule || {}) })).reverse();
    const l = files.latest;
    if (l && !l.posted) out.unshift({ ...l, draft: true });
    return out;
  }

  function metricsFor(p, files, live) {
    const measured = p.media_id && files.insights ? files.insights[p.media_id] : null;
    const l = p.media_id && live && live.posts ? live.posts[p.media_id] : null;
    return { measured: measured && measured.metrics ? measured.metrics : null, live: l, permalink: (measured && measured.permalink) || (l && l.permalink) };
  }

  function postCard(p, files, live, compact) {
    const img = mediaFor(p, files)[0];
    const m = metricsFor(p, files, live);
    const nums = m.measured || m.live;
    return `<button class="post-card" data-key="${esc(folderOf(p))}">
      <div class="thumb">${img ? `<img src="${esc(img)}" alt="" loading="lazy">` : ""}
        <span class="fmt badge ${p.draft ? "warn" : "brand"}">${p.draft ? "Draft" : esc(cap(p.format || "carousel"))}</span></div>
      <div class="body"><div class="topic">${esc(p.topic)}</div>
        ${compact ? "" : `<div class="meta">${esc(dayLabel(whereDay(p)))}${p.slot ? " · " + esc(cap(p.slot)) : ""} · ${esc(cap(p.content_type || p.angle || ""))}</div>`}
        ${nums && !compact ? `<div class="nums"><span><b>${num(nums.reach)}</b> reach</span><span><b>${num(nums.saved)}</b> saves</span><span><b>${num(nums.shares)}</b> shares</span></div>` : ""}
      </div></button>`;
  }

  function wirePostCards(root, list, files) {
    root.querySelectorAll(".post-card").forEach((el) => {
      el.onclick = () => openPost(list.find((p) => folderOf(p) === el.dataset.key), files);
    });
  }

  pages.posts = async function () {
    const [files, o] = await Promise.all([cached("/api/posts", 30), cached("/api/overview", 30)]);
    setOffset(o.schedule.utc_offset);
    const all = postList(files, o.schedule);
    const types = [...new Set(all.map((p) => p.content_type).filter(Boolean))];
    page.innerHTML = `
      <div class="page-head"><div><h1>Every <em>post</em></h1><p class="muted">${all.length} post${all.length === 1 ? "" : "s"}. Click one to see its slides or Reel, caption and numbers.</p></div></div>
      <div class="filters">
        <select id="f-format" aria-label="Format"><option value="">All formats</option><option value="carousel">Carousels</option><option value="reel">Reels</option></select>
        <select id="f-type" aria-label="Content type"><option value="">All content types</option>${types.map((t) => `<option>${esc(t)}</option>`).join("")}</select>
        <select id="f-slot" aria-label="Slot"><option value="">All slots</option>${Object.keys(o.schedule.slots || {}).map((s) => `<option>${esc(s)}</option>`).join("")}</select>
        <span class="muted small" id="live-note"></span>
      </div>
      <div class="post-grid" id="grid"></div>`;
    let live = state.cache["/api/live"]?.data || null;
    const draw = () => {
      const f = { format: $("#f-format").value, type: $("#f-type").value, slot: $("#f-slot").value };
      const list = all.filter((p) => (!f.format || (p.format || "carousel") === f.format) && (!f.type || p.content_type === f.type) && (!f.slot || p.slot === f.slot));
      $("#grid").innerHTML = list.map((p) => postCard(p, files, live)).join("") || '<p class="empty">No posts match.</p>';
      wirePostCards($("#grid"), list, files);
    };
    ["#f-format", "#f-type", "#f-slot"].forEach((s) => $(s).addEventListener("change", draw));
    draw();
  };

  function openPost(p, files, onPublish) {
    if (!p) return;
    const imgs = mediaFor(p, files);
    const isReel = (p.format || "") === "reel";
    const live = state.cache["/api/live"]?.data;
    const m = metricsFor(p, files, live);
    const metricRows = (x, label) => x ? `<h3 style="margin-top:14px">${label}</h3><dl class="kv">
        <dt>Reach</dt><dd>${num(x.reach)}</dd><dt>Saves</dt><dd>${num(x.saved)}</dd><dt>Shares</dt><dd>${num(x.shares)}</dd>
        <dt>Likes</dt><dd>${num(x.likes)}</dd><dt>Comments</dt><dd>${num(x.comments)}</dd>
        ${x.views !== undefined ? `<dt>Views</dt><dd>${num(x.views)}</dd>` : ""}
        ${x.ig_reels_avg_watch_time !== undefined ? `<dt>Avg watch</dt><dd>${num1(x.ig_reels_avg_watch_time / 1000)} s</dd>` : ""}</dl>` : "";
    const reasons = p.plan_reasons ? `<h3 style="margin-top:14px">Why the bot chose this</h3><dl class="kv">${Object.entries(p.plan_reasons).map(([k, v]) => `<dt>${esc(cap(k.replace(/_/g, " ")))}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : "";
    openModal(`<div class="detail">
      <div class="viewer">
        <div class="frame ${isReel ? "reel" : ""}" id="frame">${imgs[0] ? `<img src="${esc(imgs[0])}" alt="Slide 1">` : '<span class="muted">No preview</span>'}</div>
        ${!isReel && imgs.length > 1 ? `<div class="strip">${imgs.map((u, i) => `<button data-i="${i}" class="${i ? "" : "on"}" aria-label="Slide ${i + 1}"><img src="${esc(u)}" alt=""></button>`).join("")}</div>` : ""}
        ${isReel ? `<div class="row"><button class="btn btn-sm" id="play">▶ Play the Reel</button><span class="muted small" id="reel-status">Loads the video from GitHub (kept 30 days)</span></div>` : ""}
      </div>
      <div>
        <h2 id="modal-title">${esc(p.topic)}</h2>
        <div class="tags" style="margin-bottom:12px">
          ${p.draft ? '<span class="badge warn">Draft, not published</span>' : '<span class="badge good">Published</span>'}
          <span class="badge brand">${esc(cap(p.format || "carousel"))}</span>
          ${p.content_type ? `<span class="badge">${esc(p.content_type)}</span>` : ""}
          ${p.slot ? `<span class="badge">${esc(p.slot)}</span>` : ""}
          ${p.forced ? '<span class="badge warn">posted anyway</span>' : ""}
        </div>
        <dl class="kv">
          <dt>When</dt><dd>${p.at ? esc(fmtDate(p.at)) + " " + tzLabel() : esc(dayLabel(whereDay(p)))}</dd>
          ${p.hook ? `<dt>Hook</dt><dd>${esc(p.hook)}</dd>` : ""}
          ${p.hook_style ? `<dt>Hook style</dt><dd>${esc(p.hook_style)}</dd>` : ""}
          ${p.topic_area ? `<dt>Topic area</dt><dd>${esc(p.topic_area)}</dd>` : ""}
          ${p.template && p.template !== "reel" ? `<dt>Design</dt><dd>${esc(p.template)}</dd>` : ""}
          ${p.trend && p.trend.headline ? `<dt>Trend</dt><dd>${p.trend.link && /^https?:/.test(p.trend.link) ? `<a href="${esc(p.trend.link)}" target="_blank" rel="noopener noreferrer">${esc(p.trend.headline)}</a>` : esc(p.trend.headline)}${p.trend.source ? ` <span class="muted">(${esc(p.trend.source)})</span>` : ""}</dd>` : ""}
          ${m.permalink && /^https:\/\/(www\.)?instagram\.com\//.test(m.permalink) ? `<dt>Instagram</dt><dd><a href="${esc(m.permalink)}" target="_blank" rel="noopener noreferrer">Open post</a></dd>` : ""}
        </dl>
        ${metricRows(m.measured, "Results at 3 days")}
        ${!m.measured ? metricRows(m.live, "Live numbers") : ""}
        ${!m.measured && !m.live && !p.draft ? '<p class="muted small" style="margin-top:12px">Numbers appear here 3 days after posting, when the bot measures each post.</p>' : ""}
        ${reasons}
        ${p.caption ? `<h3 style="margin-top:14px">Caption</h3><div class="caption">${esc(p.caption)}</div>` : ""}
        ${onPublish ? `<div class="row" style="margin-top:16px"><button class="btn" id="approve">Approve and publish</button><span class="muted small">Check every slide${isReel ? " and the Reel" : ""} and the caption first.</span></div>` : ""}
      </div></div>`, (el) => {
      const ap = el.querySelector("#approve");
      if (ap) ap.onclick = () => { closeModal(); onPublish(); };
      el.querySelectorAll(".strip button").forEach((b) => {
        b.onclick = () => {
          el.querySelectorAll(".strip button").forEach((x) => x.classList.remove("on"));
          b.classList.add("on");
          el.querySelector("#frame").innerHTML = `<img src="${esc(imgs[b.dataset.i])}" alt="Slide ${Number(b.dataset.i) + 1}">`;
        };
      });
      const play = el.querySelector("#play");
      if (play) play.onclick = () => playReel(p, el);
    });
  }

  async function playReel(p, el) {
    const status = el.querySelector("#reel-status");
    const btn = el.querySelector("#play");
    btn.disabled = true;
    status.innerHTML = '<span class="spinner"></span> Downloading…';
    try {
      const q = new URLSearchParams();
      if (p.run_id) q.set("run_id", p.run_id);
      if (p.folder) q.set("folder", p.folder);
      const r = await fetch(`/api/reel?${q}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `could not load the Reel (${r.status})`);
      const mp4 = await unzipFirst(await r.arrayBuffer(), /\.mp4$/i);
      const url = URL.createObjectURL(new Blob([mp4], { type: "video/mp4" }));
      el.querySelector("#frame").innerHTML = `<video src="${url}" controls autoplay playsinline></video>`;
      status.textContent = "";
      btn.hidden = true;
    } catch (e) {
      status.innerHTML = `<span class="error">${esc(e.message)}</span>`;
      btn.disabled = false;
    }
  }

  // Reads one file out of a zip (GitHub hands out artifacts as zips).
  async function unzipFirst(buf, pattern) {
    const v = new DataView(buf);
    let eocd = -1;
    for (let i = buf.byteLength - 22; i >= Math.max(0, buf.byteLength - 70000); i--) {
      if (v.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
    }
    if (eocd < 0) throw new Error("the download is not a zip");
    const count = v.getUint16(eocd + 10, true);
    let p = v.getUint32(eocd + 16, true);
    for (let n = 0; n < count; n++) {
      if (v.getUint32(p, true) !== 0x02014b50) break;
      const method = v.getUint16(p + 10, true);
      const csize = v.getUint32(p + 20, true);
      const nameLen = v.getUint16(p + 28, true);
      const extraLen = v.getUint16(p + 30, true);
      const commentLen = v.getUint16(p + 32, true);
      const off = v.getUint32(p + 42, true);
      const name = new TextDecoder().decode(new Uint8Array(buf, p + 46, nameLen));
      p += 46 + nameLen + extraLen + commentLen;
      if (!pattern.test(name)) continue;
      const start = off + 30 + v.getUint16(off + 26, true) + v.getUint16(off + 28, true);
      const data = new Uint8Array(buf, start, csize);
      if (method === 0) return data;
      if (method !== 8) throw new Error("unsupported zip compression");
      const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
      return new Uint8Array(await new Response(stream).arrayBuffer());
    }
    throw new Error("no video in the download");
  }

  // ---------------------------------------------------------------- Analytics

  const DIMS = [
    ["content_type", "Content type"], ["slot", "Slot"], ["format", "Format"],
    ["hook_style", "Hook style"], ["topic_area", "Topic area"], ["template", "Design"],
  ];
  const METRICS = [["reach", "Reach"], ["saved", "Saves"], ["shares", "Shares"], ["score", "Score"]];

  pages.analytics = async function () {
    const a = await cached("/api/analytics", 60);
    setOffset(a.schedule.utc_offset);
    const weights = a.learning.weights || {};
    const measured = Object.values(a.insights || {}).filter((r) => r.metrics && r.metrics.reach !== undefined).map((r) => ({
      ...r, format: r.format || "carousel", slot: r.slot || (r.posted_at ? slotOf(r.posted_at, a.schedule) : ""),
      at: r.posted_at, m: { ...r.metrics, score: score(r.metrics, weights) },
    }));
    const pubById = Object.fromEntries((a.published || []).filter((p) => p.media_id).map((p) => [p.media_id, p]));
    // live per-post numbers were dropped to keep Instagram calls down (Harish, 2026-11-27)
    const ui = { source: "measured", metric: "reach", dim: "content_type", live: null };

    page.innerHTML = `
      <div class="page-head"><div><h1>What's <em>working</em></h1><p class="muted" id="an-sub"></p></div>
        <div class="row"></div></div>
      <div id="an-body"></div>`;

    const rowsFor = () => {
      if (ui.source === "measured") return measured;
      const live = ui.live?.posts || {};
      return Object.entries(live).map(([id, x]) => {
        const p = pubById[id] || {};
        const fmt = p.format || (x.media_type === "VIDEO" ? "reel" : "carousel");
        return { ...p, media_id: id, topic: p.topic || "(not posted by the bot)", format: fmt, slot: whereSlot({ ...p, at: p.at || x.timestamp }, a.schedule), at: p.at || x.timestamp, m: { ...x, score: score(x, weights) }, permalink: x.permalink };
      }).filter((r) => r.m.reach !== undefined);
    };

    const draw = () => {
      document.querySelectorAll("#src button").forEach((b) => b.classList.toggle("on", b.dataset.v === ui.source));
      const rows = rowsFor().sort((x, y) => Date.parse(x.at) - Date.parse(y.at));
      $("#an-sub").textContent = ui.source === "measured"
        ? "Each post is measured once, 3 days after it goes out. This is what the bot learns from."
        : `Live Instagram numbers for the latest 30 posts${ui.live?.fetched_at ? ", fetched " + ago(ui.live.fetched_at) : ""}. Recent posts are still growing.`;
      if (!rows.length) {
        $("#an-body").innerHTML = `<div class="card empty"><h2>No numbers yet</h2><p>${ui.source === "measured"
          ? `The bot measures each post 3 days after it goes out, so the first results show up 3 days after the first post.`
          : "Instagram has no numbers for these posts yet."}</p>
          ${(ui.live?.errors || []).length ? `<p class="error small">${esc(ui.live.errors[0])}</p>` : ""}</div>`;
        return;
      }
      const sum = (k) => rows.reduce((s, r) => s + (Number(r.m[k]) || 0), 0);
      const best = rows.slice().sort((x, y) => y.m.score - x.m.score)[0];
      $("#an-body").innerHTML = `
        <div class="tiles">
          <div class="tile"><div class="label">Posts</div><div class="value">${rows.length}</div><div class="sub">${rows.filter((r) => r.format === "reel").length} Reels</div></div>
          <div class="tile"><div class="label">Total reach</div><div class="value">${num(sum("reach"))}</div><div class="sub">avg ${num(sum("reach") / rows.length)} a post</div></div>
          <div class="tile"><div class="label">Saves</div><div class="value">${num(sum("saved"))}</div><div class="sub">avg ${num1(sum("saved") / rows.length)} a post</div></div>
          <div class="tile"><div class="label">Shares</div><div class="value">${num(sum("shares"))}</div><div class="sub">avg ${num1(sum("shares") / rows.length)} a post</div></div>
          <div class="tile"><div class="label">Best post</div><div class="value" style="font-size:1rem;margin-top:6px">${esc(best.topic || "")}</div><div class="sub">score ${num(best.m.score)}</div></div>
        </div>
        <div class="card"><div class="card-head"><h2 id="by-title"></h2>
          <div class="row"><select id="dim" aria-label="Group by">${DIMS.map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select>
          <div class="seg" id="met">${METRICS.map(([k, l]) => `<button data-v="${k}">${l}</button>`).join("")}</div></div></div>
          <div id="by"></div>
          <details style="margin-top:12px"><summary class="small muted">Show as a table</summary><div id="by-table" class="table-wrap"></div></details>
        </div>
        <div class="card"><div class="card-head"><h2 id="tl-title"></h2><span class="muted small">one bar per post, oldest on the left</span></div><div id="timeline"></div></div>
        <div class="card"><div class="card-head"><h2>Every post</h2></div><div class="table-wrap" id="all-table"></div></div>
        <p class="muted small">Score = ${Object.entries(Object.keys(weights).length ? weights : { reach: 1, shares: 5, saved: 3, comments: 2, likes: 1 }).map(([k, v]) => `${v}× ${k}`).join(" + ")} (the same formula the bot uses to learn).</p>`;
      $("#dim").value = ui.dim;
      $("#dim").onchange = () => { ui.dim = $("#dim").value; drawGroups(rows); };
      document.querySelectorAll("#met button").forEach((b) => { b.onclick = () => { ui.metric = b.dataset.v; drawGroups(rows); drawTimeline(rows); }; });
      drawGroups(rows);
      drawTimeline(rows);
      $("#all-table").innerHTML = `<table><thead><tr><th>Posted</th><th>Topic</th><th>Type</th><th>Slot</th><th>Format</th><th class="num">Reach</th><th class="num">Saves</th><th class="num">Shares</th><th class="num">Likes</th><th class="num">Score</th></tr></thead><tbody>
        ${rows.slice().reverse().map((r) => `<tr><td>${esc(fmtDate(r.at, false))}</td><td>${r.permalink && /^https:\/\/(www\.)?instagram\.com\//.test(r.permalink) ? `<a href="${esc(r.permalink)}" target="_blank" rel="noopener noreferrer">${esc(r.topic)}</a>` : esc(r.topic)}</td><td>${esc(r.content_type || "")}</td><td>${esc(r.slot || "")}</td><td>${esc(r.format)}</td>
          <td class="num">${num(r.m.reach)}</td><td class="num">${num(r.m.saved)}</td><td class="num">${num(r.m.shares)}</td><td class="num">${num(r.m.likes)}</td><td class="num">${num(r.m.score)}</td></tr>`).join("")}</tbody></table>`;
    };

    const drawGroups = (rows) => {
      document.querySelectorAll("#met button").forEach((b) => b.classList.toggle("on", b.dataset.v === ui.metric));
      const mLabel = METRICS.find((x) => x[0] === ui.metric)[1];
      const dLabel = DIMS.find((x) => x[0] === ui.dim)[1];
      $("#by-title").textContent = `Average ${mLabel.toLowerCase()} by ${dLabel.toLowerCase()}`;
      const groups = {};
      for (const r of rows) {
        const k = r[ui.dim] || "unknown";
        (groups[k] ||= []).push(r);
      }
      const g = Object.entries(groups).map(([k, list]) => ({
        k, n: list.length, avg: list.reduce((s, r) => s + (Number(r.m[ui.metric]) || 0), 0) / list.length,
        reach: list.reduce((s, r) => s + (Number(r.m.reach) || 0), 0) / list.length,
        saved: list.reduce((s, r) => s + (Number(r.m.saved) || 0), 0) / list.length,
        shares: list.reduce((s, r) => s + (Number(r.m.shares) || 0), 0) / list.length,
        score: list.reduce((s, r) => s + (Number(r.m.score) || 0), 0) / list.length,
      })).sort((a, b) => b.avg - a.avg);
      const max = Math.max(...g.map((x) => x.avg), 1);
      $("#by").innerHTML = `<div class="bars">${g.map((x) => `<div class="bar-row">
          <span class="lbl" title="${esc(x.k)}">${esc(cap(x.k))}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${(x.avg / max) * 100}%"></div>
            <span class="bar-hit" data-tip="<b>${esc(cap(x.k))}</b><br>${num1(x.avg)} avg ${esc(mLabel.toLowerCase())}<br>${x.n} post${x.n > 1 ? "s" : ""}"></span></div>
          <span class="val">${ui.metric === "reach" || ui.metric === "score" ? num(x.avg) : num1(x.avg)} <span class="n">· ${x.n}</span></span></div>`).join("")}</div>
          ${g.some((x) => x.n < 3) ? '<p class="muted small" style="margin:10px 0 0">Groups with only 1 or 2 posts can swing a lot; give them time.</p>' : ""}`;
      $("#by-table").innerHTML = `<table><thead><tr><th>${esc(dLabel)}</th><th class="num">Posts</th><th class="num">Avg reach</th><th class="num">Avg saves</th><th class="num">Avg shares</th><th class="num">Avg score</th></tr></thead><tbody>
        ${g.map((x) => `<tr><td>${esc(x.k)}</td><td class="num">${x.n}</td><td class="num">${num(x.reach)}</td><td class="num">${num1(x.saved)}</td><td class="num">${num1(x.shares)}</td><td class="num">${num(x.score)}</td></tr>`).join("")}</tbody></table>`;
    };

    const drawTimeline = (rows) => {
      const mLabel = METRICS.find((x) => x[0] === ui.metric)[1];
      $("#tl-title").textContent = `${mLabel} per post`;
      const W = 900, H = 220, L = 44, B = 24, T = 8;
      const vals = rows.map((r) => Number(r.m[ui.metric]) || 0);
      const niceMax = (() => { const m = Math.max(...vals, 1); const p = Math.pow(10, Math.floor(Math.log10(m))); return Math.ceil(m / p) * p; })();
      const n = rows.length;
      const slot = (W - L) / n;
      const bw = Math.max(2, Math.min(28, slot - 2));
      const y = (v) => T + (H - T - B) * (1 - v / niceMax);
      const ticks = [0, niceMax / 2, niceMax];
      const r4 = Math.min(4, bw / 2);
      const bars = rows.map((r, i) => {
        const v = vals[i];
        const x = L + i * slot + (slot - bw) / 2;
        const top = y(v);
        const h = Math.max(0, H - B - top);
        const path = h > r4 ? `M${x},${H - B}V${top + r4}Q${x},${top} ${x + r4},${top}H${x + bw - r4}Q${x + bw},${top} ${x + bw},${top + r4}V${H - B}Z` : `M${x},${H - B}V${top}H${x + bw}V${H - B}Z`;
        const t = `<b>${esc(r.topic || "")}</b><br>${esc(fmtDate(r.at, false))} · ${esc(r.slot || "")} · ${esc(r.format)}<br>${num(v)} ${esc(mLabel.toLowerCase())}`;
        return `<rect class="col-hit" x="${L + i * slot}" y="${T}" width="${slot}" height="${H - T - B}" data-tip="${esc(t)}"></rect><path class="col" d="${path}"></path>`;
      }).join("");
      const labels = rows.map((r, i) => (n <= 12 || i % Math.ceil(n / 8) === 0) ? `<text class="axis-text" x="${L + i * slot + slot / 2}" y="${H - 6}" text-anchor="middle">${esc(fmtDate(r.at, false))}</text>` : "").join("");
      $("#timeline").innerHTML = `<svg class="chart-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(mLabel)} per post">
        ${ticks.map((v) => `<line class="grid-line" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"></line><text class="axis-text" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${num(v)}</text>`).join("")}
        ${bars}${labels}</svg>`;
    };

    document.querySelectorAll("#src button").forEach((b) => {
      b.onclick = async () => {
        ui.source = b.dataset.v;
        if (ui.source === "live" && !ui.live) {
          $("#an-body").innerHTML = '<div class="empty"><span class="spinner"></span> Asking Instagram…</div>';
          try { ui.live = await cached("/api/live", 300); } catch (e) { $("#an-body").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; return; }
        }
        draw();
      };
    });
    if (ui.source === "live") {
      try { ui.live = await cached("/api/live", 300); } catch (e) { ui.live = { posts: {}, errors: [e.message] }; }
    }
    draw();
  };

  // ---------------------------------------------------------------- Runs

  pages.runs = async function () {
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    const watch = params.get("watch");
    const since = params.get("since");
    let watchId = params.get("id");
    page.innerHTML = `
      <div class="page-head"><div><h1>What the <em>bot</em> is doing</h1><p class="muted">What the bot has been doing on GitHub Actions.</p></div>
        <div class="row"><label class="check small"><input type="checkbox" id="show-quick"><span>Show quick checks with no post due</span></label></div></div>
      <div id="watch"></div>
      <div class="card"><div id="run-list"><div class="empty"><span class="spinner"></span></div></div></div>`;

    const draw = async () => {
      const { runs } = await api("/api/runs");
      const showQuick = $("#show-quick")?.checked;
      const list = runs.filter((r) => showQuick || !runLabel(r).quick);
      $("#run-list").innerHTML = list.map(runRow).join("") || '<p class="empty">No runs yet.</p>';
      wireRuns($("#run-list"));
      if (watch) {
        let r = watchId ? runs.find((x) => String(x.id) === String(watchId)) : null;
        if (!r && since) {
          r = runs.filter((x) => x.workflow === watch && x.event === "workflow_dispatch" && Date.parse(x.created_at) >= Date.parse(since) - 20e3)
            .sort((a, b) => a.created_at.localeCompare(b.created_at))[0];
        }
        if (r) watchId = String(r.id);
        await drawWatch(r);
        return r && r.status === "completed";
      }
      return false;
    };

    const drawWatch = async (r) => {
      const box = $("#watch");
      if (!r) {
        box.innerHTML = `<div class="card" style="margin-bottom:16px"><div class="row"><span class="spinner"></span><b>Starting your ${watch === "publish" ? "publish" : watch === "health" ? "health check" : "post"}…</b></div><p class="muted small" style="margin:6px 0 0">GitHub usually picks it up within a minute.</p></div>`;
        return;
      }
      const l = runLabel(r);
      let steps = "";
      let flat = [];
      try {
        const { jobs } = await api(`/api/run?id=${r.id}`);
        flat = jobs.flatMap((j) => j.steps);
        steps = jobs.map((j) => `<ul class="steps">${j.steps.filter((s) => !/^(Set up job|Complete job|Post )/.test(s.name)).map((s) => `<li><span class="ic">${stepIcon(s)}</span><span class="${s.conclusion === "skipped" ? "muted" : ""}">${esc(s.name)}</span></li>`).join("")}</ul>`).join("");
      } catch { /* steps are optional */ }
      const done = r.status === "completed";
      const gen = flat.find((s) => s.name === "Generate");
      const pub = flat.find((s) => /^Publish/.test(s.name));
      let result = "";
      if (done && r.conclusion === "success" && watch === "health") {
        result = `<div class="callout good" style="margin-top:12px"><strong>Check finished</strong><span><a href="#/alerts">See Alerts</a>: anything it found is listed there.</span></div>`;
      } else if (done && r.conclusion === "success") {
        if (watch === "calendar" && gen && gen.conclusion === "skipped") {
          result = `<div class="callout warn" style="margin-top:12px"><strong>The bot decided not to post</strong>The slot or today's limit was already used. <a href="${esc(r.url)}" target="_blank" rel="noopener">The run's summary</a> says why. You can try again with "Post anyway".</div>`;
        } else if (watch === "calendar" && pub && pub.conclusion === "skipped") {
          result = `<div class="callout info" style="margin-top:12px"><strong>The post is made and waiting</strong>Auto-publish is paused, so it is a draft now. <a href="#/">Publish it from the Overview</a>.</div>`;
        } else {
          result = `<div class="callout good" style="margin-top:12px"><strong>Finished</strong><span><a href="#/posts">See it in Posts</a>. If it is not there, <a href="${esc(r.url)}" target="_blank" rel="noopener">the run's log</a> says why the bot held it back.</span></div>`;
        }
      } else if (done) {
        result = `<div class="callout bad" style="margin-top:12px"><strong>The run ${esc(r.conclusion || "failed")}</strong><span><a href="${esc(r.url)}" target="_blank" rel="noopener">Open it on GitHub</a> to see what went wrong.</span></div>`;
      }
      box.innerHTML = `<div class="card" style="margin-bottom:16px"><div class="card-head"><h2>Your ${watch === "publish" ? "publish" : watch === "health" ? "health check" : "new post"}</h2>${l.badge}</div>
        <p class="muted small" style="margin:0">${esc(l.wf)} · started ${esc(ago(r.created_at))} · <a href="${esc(r.url)}" target="_blank" rel="noopener">GitHub</a></p>${steps}${result}</div>`;
      if (done) bust();
    };

    $("#show-quick").onchange = draw;
    const finished = await draw();
    if (watch && !finished) {
      const id = setInterval(async () => {
        try { if (await draw()) clearInterval(id); } catch { /* try again next tick */ }
      }, 8000);
      state.timers.push(id);
    } else if (!watch) {
      state.timers.push(setInterval(() => draw().catch(() => {}), 30000));
    }
  };

  // ---------------------------------------------------------------- Alerts

  function alertName(title) {
    return String(title || "").replace(/^Bot alert:\s*/, "");
  }

  function alertKind(title) {
    if (/token/i.test(title)) return { label: "Token", cls: "bad" };
    if (/missed/i.test(title)) return { label: "Missed post", cls: "warn" };
    return { label: "Run failed", cls: "bad" };
  }

  // When each token runs out. Reminders are emailed 14 days ahead by the nightly health check.
  function tokenCard(tokens) {
    const d = tokens.dashboard ? new Date(tokens.dashboard) : null;
    const days = d ? Math.floor((d - Date.now()) / 86400e3) : null;
    const badge = !d ? '<span class="badge">Not known yet</span>'
      : days < 0 ? '<span class="badge bad">Expired</span>'
      : days < 14 ? `<span class="badge bad">${days} day${days === 1 ? "" : "s"} left</span>`
      : days < 45 ? `<span class="badge warn">${days} days left</span>`
      : `<span class="badge good">${days} days left</span>`;
    return `<div class="card" style="margin-bottom:16px"><div class="card-head"><h2>Token reminders</h2><span class="muted small">emailed 14 days before expiry</span></div>
      <div class="token-row"><div class="token-what"><b>Dashboard GitHub token</b><div class="muted small">Lets this site show runs, start posts and switch the publishing mode.</div></div>
        <div class="token-when">${d ? "Expires " + esc(fmtDate(d.toISOString(), false)) + " " + d.getUTCFullYear() : "Saved after the next page load"}</div>${badge}</div>
    </div>`;
  }

  pages.alerts = async function () {
    page.innerHTML = `
      <div class="page-head"><div><h1>Bot <em>alerts</em></h1><p class="muted">When a post, Reel or run fails, the dashboard's token is about to expire, or a slot passes without a post. Each alert is also emailed to you as a GitHub issue.</p></div>
        ${canPost() ? '<button class="btn btn-ghost" id="run-health">Run health check now</button>' : ""}</div>
      <div id="alerts"><div class="empty"><span class="spinner"></span></div></div>`;
    const draw = async () => {
      const a = await cached("/api/alerts", 30);
      const box = $("#alerts");
      if (!a.enabled) {
        box.innerHTML = `<div class="callout warn"><strong>The dashboard cannot read alerts yet</strong>
          <span>Edit the dashboard's GitHub token and set <b>Issues</b> to <b>Read and write</b> (step 1 of the setup guide in dashboard/README.md). The alert emails still reach you in the meantime.</span></div>` + `<div style="margin-top:16px">${tokenCard(a.tokens || {})}</div>`;
        return;
      }
      const card = (i) => {
        const k = alertKind(i.title);
        return `<div class="card alert-card" style="margin-bottom:16px">
          <div class="card-head"><h2>${esc(alertName(i.title))}</h2><span class="badge ${k.cls}">${esc(k.label)}</span></div>
          <p class="muted small" style="margin:0 0 10px">First seen ${esc(fmtDate(i.created_at))} ${tzLabel()}${i.comments ? ` · happened again ${i.comments} time${i.comments > 1 ? "s" : ""}, last ${esc(ago(i.updated_at))}` : ""}</p>
          <div class="md">${window.renderMarkdown(i.latest ? i.latest.body.replace(/^\*\*It happened again\.\*\*\s*/, "") : i.body)}</div>
          <div class="row" style="margin-top:12px">
            ${canPost() ? `<button class="btn btn-sm" data-close="${i.number}" data-title="${esc(alertName(i.title))}">Mark fixed</button>` : ""}
            <a class="btn btn-ghost btn-sm" href="${esc(i.url)}" target="_blank" rel="noopener">Open on GitHub</a></div>
        </div>`;
      };
      box.innerHTML = (a.open.length ? a.open.map(card).join("")
        : `<div class="callout good" style="margin-bottom:16px"><strong>All clear</strong><span>No open alerts. The daily health check runs at about 22:57 ${tzLabel()}.</span></div>`)
        + tokenCard(a.tokens || {})
        + `<div class="card"><div class="card-head"><h2>Recently fixed</h2></div>${a.closed.length
          ? `<div class="table-wrap"><table><thead><tr><th>Alert</th><th>Started</th><th>Fixed</th><th></th></tr></thead><tbody>${a.closed.map((i) => `<tr>
            <td>${esc(alertName(i.title))}</td><td style="white-space:nowrap">${esc(fmtDate(i.created_at))}</td><td style="white-space:nowrap">${esc(fmtDate(i.closed_at))}</td>
            <td><a class="small" href="${esc(i.url)}" target="_blank" rel="noopener">GitHub</a></td></tr>`).join("")}</tbody></table></div>`
          : '<p class="empty">Nothing yet.</p>'}</div>`;
      box.querySelectorAll("[data-close]").forEach((b) => {
        b.onclick = async () => {
          const ok = await confirmBox({ title: "Mark this alert fixed?", body: `<p>This closes “${esc(b.dataset.title)}” on GitHub. If the problem happens again, the bot opens a new alert.</p>`, ok: "Mark fixed" });
          if (!ok) return;
          try {
            await api("/api/alerts/close", { method: "POST", body: { number: Number(b.dataset.close) } });
            toast("Alert closed");
            bust();
            route();
          } catch (e) { toast(e.message, 7000); }
        };
      });
    };
    if ($("#run-health")) {
      $("#run-health").onclick = async () => {
        try {
          const res = await api("/api/health", { method: "POST" });
          toast("Health check started. Any problem shows up here in about a minute.");
          location.hash = `#/runs?watch=health&since=${encodeURIComponent(res.started_at)}${res.run?.id ? "&id=" + res.run.id : ""}`;
        } catch (e) { toast(e.message, 6000); }
      };
    }
    await draw();
    state.timers.push(setInterval(() => { delete state.cache["/api/alerts"]; draw().catch(() => {}); }, 60000));
  };

  // ---------------------------------------------------------------- Reports

  pages.reports = async function () {
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    const docs = await cached("/api/docs", 60);
    const tab = params.get("doc") || (docs.weekly.length ? "weekly/" + docs.weekly[0] : "report");
    page.innerHTML = `
      <div class="page-head"><div><h1>The bot's <em>reports</em></h1><p class="muted">Written by the bot: the Monday recap, what is working, and today's trends.</p></div></div>
      <div class="filters">
        <div class="seg" id="doc-tabs">
          <button data-v="weekly">Weekly recap</button><button data-v="report">What works</button><button data-v="trends">Trends</button></div>
        <select id="week" aria-label="Week" ${docs.weekly.length ? "" : "hidden"}>${docs.weekly.map((w) => `<option value="weekly/${esc(w)}">Week ending ${esc(w.replace(/\.md$/, ""))}</option>`).join("")}</select>
      </div>
      <div class="card md" id="doc"></div>`;
    const show = async (name) => {
      const kind = name.startsWith("weekly") ? "weekly" : name;
      document.querySelectorAll("#doc-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.v === kind));
      $("#week").hidden = kind !== "weekly" || !docs.weekly.length;
      if (kind === "weekly" && !docs.weekly.length) {
        $("#doc").innerHTML = '<div class="empty"><h2>No weekly recap yet</h2><p>The first one is written on Monday morning (about 05:47 IST) and also emailed as a GitHub issue.</p></div>';
        return;
      }
      if (kind === "weekly" && name === "weekly") name = "weekly/" + docs.weekly[0];
      if (kind === "weekly") $("#week").value = name;
      $("#doc").innerHTML = '<div class="empty"><span class="spinner"></span></div>';
      try {
        const d = await cached(`/api/doc?name=${encodeURIComponent(name)}`, 60);
        $("#doc").innerHTML = window.renderMarkdown(d.text);
      } catch (e) {
        $("#doc").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
      }
    };
    document.querySelectorAll("#doc-tabs button").forEach((b) => { b.onclick = () => show(b.dataset.v); });
    $("#week").onchange = () => show($("#week").value);
    show(tab);
  };

  // ---------------------------------------------------------------- Activity log

  pages.audit = async function () {
    page.innerHTML = `<div class="page-head"><div><h1>Who did <em>what</em></h1><p class="muted">Who signed in and what they did from this dashboard. Kept for a year.</p></div></div>
      <div class="card"><div id="audit"><div class="empty"><span class="spinner"></span></div></div><div id="more" style="margin-top:10px"></div></div>`;
    const rows = [];
    const load = async (cursor) => {
      const d = await api(`/api/audit${cursor ? "?cursor=" + encodeURIComponent(cursor) : ""}`);
      if (d.disabled) {
        $("#audit").innerHTML = '<div class="callout warn"><strong>The activity log is not switched on</strong>Bind a KV namespace called AUDIT in Cloudflare (step 5 of the setup guide).</div>';
        return;
      }
      rows.push(...d.entries);
      const label = (e) => {
        const det = e.details || {};
        if (e.action === "started a post") return `Started a post · slot ${esc(det.slot)}, format ${esc(det.format)}${det.force ? ", post anyway" : ""}`;
        if (e.action === "published the draft") return `Published the draft “${esc(det.topic)}”${det.force ? " (post anyway)" : ""}`;
        if (e.action === "marked an alert fixed") return `Marked alert fixed · ${esc(det.title || "")}`;
        if (e.action === "sign-in refused") return `<span class="error">Sign-in refused</span> · ${esc(det.reason || "")}`;
        return esc(cap(e.action));
      };
      $("#audit").innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>When (${tzLabel()})</th><th>Who</th><th>What</th><th></th></tr></thead><tbody>
        ${rows.map((e) => `<tr><td style="white-space:nowrap">${esc(fmtDate(e.at))}</td><td>${esc(e.name || e.who)}<div class="muted small">${esc(e.who)}${e.role ? " · " + esc(e.role) : ""}</div></td><td>${label(e)}</td>
          <td>${e.details?.run ? `<a class="small" href="#/runs?watch=${e.action === "published the draft" ? "publish" : "calendar"}&id=${esc(e.details.run)}">run</a>` : ""}</td></tr>`).join("")}</tbody></table></div>` : '<p class="empty">Nothing yet.</p>';
      $("#more").innerHTML = d.cursor ? '<button class="btn btn-ghost btn-sm">Load more</button>' : "";
      if (d.cursor) $("#more button").onclick = () => load(d.cursor);
    };
    await load();
  };

  boot();
})();
