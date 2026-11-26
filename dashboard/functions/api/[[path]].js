// The dashboard's backend: one Cloudflare Pages Function that answers every /api/* request.
// It holds the GitHub token (and the optional Instagram token) so they never reach a browser.
import {
  COOKIE, can, cookieHeader, cookies, githubEnabled, githubIdentity, makeSession, randomState,
  readSession, roleFor, verifyGoogle,
} from "../../lib/auth.js";
import { GitHubError, branch, dispatch, file, gh, repo, runs, setVariable, tree, variable } from "../../lib/github.js";
import { clearTrigger, markTriggered, recent, record, recentlyTriggered } from "../../lib/audit.js";

const SLOTS = ["next", "morning", "midday", "evening"]; // the choices calendar.yml accepts
const FORMATS = ["auto", "reel", "carousel"];

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers },
  });
}

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function isLocal(url) {
  return ["localhost", "127.0.0.1"].includes(url.hostname);
}

async function body(request) {
  try {
    return await request.json();
  } catch {
    throw new HttpError(400, "expected a JSON body");
  }
}

// ---------------------------------------------------------------- routes

const routes = {
  // Public: what the sign-in page needs to show its buttons.
  "GET /api/config": async ({ env, url }) => json({
    googleClientId: env.GOOGLE_CLIENT_ID || "",
    github: githubEnabled(env),
    devLogin: !!(env.DEV_LOGIN && isLocal(url)),
    repo: repo(env),
    branch: branch(env),
  }),

  "POST /api/auth/google": async ({ env, request, url }) => {
    const { credential } = await body(request);
    let identity;
    try {
      identity = await verifyGoogle(env, credential);
    } catch (e) {
      throw new HttpError(401, `Google sign-in failed: ${e.message}`);
    }
    return signIn(env, url, identity);
  },

  // Local testing only: DEV_LOGIN=email in .dev.vars, and only on localhost.
  "POST /api/auth/dev": async ({ env, url }) => {
    if (!env.DEV_LOGIN || !isLocal(url)) throw new HttpError(404, "not found");
    return signIn(env, url, { email: env.DEV_LOGIN.toLowerCase(), name: "Local tester", via: "dev" });
  },

  "GET /api/auth/github": async ({ env, url }) => {
    if (!githubEnabled(env)) throw new HttpError(404, "GitHub sign-in is not set up");
    const state = randomState();
    const to = new URL("https://github.com/login/oauth/authorize");
    to.searchParams.set("client_id", env.GITHUB_OAUTH_CLIENT_ID);
    to.searchParams.set("redirect_uri", `${url.origin}/api/auth/github/callback`);
    to.searchParams.set("scope", "read:user user:email");
    to.searchParams.set("state", state);
    to.searchParams.set("allow_signup", "false");
    return new Response(null, {
      status: 302,
      headers: { Location: to.toString(), "Set-Cookie": cookieHeader("rxt_oauth_state", state, { maxAge: 600, secure: !isLocal(url) }) },
    });
  },

  "GET /api/auth/github/callback": async ({ env, request, url }) => {
    const state = url.searchParams.get("state");
    if (!state || state !== cookies(request).rxt_oauth_state) return redirectHome("GitHub sign-in expired, please try again");
    let identity;
    try {
      identity = await githubIdentity(env, url.searchParams.get("code"));
    } catch (e) {
      return redirectHome(e.message);
    }
    if (!roleFor(env, identity)) {
      await record(env, identity, "sign-in refused", { reason: "not on the allowlist" });
      return redirectHome(`${identity.github} is not on the dashboard's allowlist`);
    }
    const s = await makeSession(env, identity);
    await record(env, { ...identity, role: roleFor(env, identity) }, "signed in");
    const h = new Headers({ Location: "/" });
    h.append("Set-Cookie", cookieHeader(COOKIE, s.value, { maxAge: s.maxAge, secure: !isLocal(url) }));
    h.append("Set-Cookie", cookieHeader("rxt_oauth_state", "", { maxAge: 0, secure: !isLocal(url) }));
    return new Response(null, { status: 302, headers: h });
  },

  "POST /api/auth/logout": async ({ url }) =>
    json({ ok: true }, 200, { "Set-Cookie": cookieHeader(COOKIE, "", { maxAge: 0, secure: !isLocal(url) }) }),

  "GET /api/me": async ({ session }) => json(publicSession(session)),

  // Everything the home page needs in one call.
  "GET /api/overview": async ({ env }) => {
    const [config, published, latest, autoPublish, cal, pub, weekly] = await Promise.all([
      file(env, "config.json", { json: true, fallback: {} }),
      file(env, "posts/published.json", { json: true, fallback: [] }),
      file(env, "posts/latest.json", { json: true, fallback: null }),
      variable(env, "AUTO_PUBLISH"),
      runs(env, "calendar", 20),
      runs(env, "publish", 8),
      runs(env, "weekly", 4),
    ]);
    return json({
      schedule: config.schedule || {}, reels: config.reels || {}, brand: config.brand, handle: config.handle,
      published, latest: latest && slimLatest(latest), autoPublish: autoPublish === "true", autoPublishRaw: autoPublish,
      runs: [...cal, ...pub, ...weekly].sort((a, b) => b.created_at.localeCompare(a.created_at)),
      liveEnabled: liveEnabled(env), repo: repo(env), branch: branch(env),
    });
  },

  "GET /api/posts": async ({ env }) => {
    const [published, latest, history, insights, files] = await Promise.all([
      file(env, "posts/published.json", { json: true, fallback: [] }),
      file(env, "posts/latest.json", { json: true, fallback: null }),
      file(env, "history.json", { json: true, fallback: [] }),
      file(env, "analytics/insights.json", { json: true, fallback: {} }),
      tree(env),
    ]);
    const folders = {};
    for (const f of files) {
      const m = /^posts\/([^/]+)\/([^/]+)$/.exec(f.path);
      if (m && f.type === "blob") (folders[m[1]] ||= []).push(m[2]);
    }
    return json({
      published, latest: latest && slimLatest(latest), history: history.slice(-200), insights, folders,
      rawBase: `https://raw.githubusercontent.com/${repo(env)}/${branch(env)}/`,
    });
  },

  "GET /api/analytics": async ({ env }) => {
    const [insights, published, config] = await Promise.all([
      file(env, "analytics/insights.json", { json: true, fallback: {} }),
      file(env, "posts/published.json", { json: true, fallback: [] }),
      file(env, "config.json", { json: true, fallback: {} }),
    ]);
    return json({ insights, published, learning: config.learning || {}, schedule: config.schedule || {}, liveEnabled: liveEnabled(env) });
  },

  // Live Instagram numbers for recent posts (only if IG_TOKEN and IG_USER_ID are set).
  "GET /api/live": async ({ env }) => {
    if (!liveEnabled(env)) return json({ enabled: false, posts: {} });
    return json(await liveNumbers(env));
  },

  "GET /api/docs": async ({ env }) => {
    const files = await tree(env);
    const weekly = files.filter((f) => /^analytics\/weekly\/[^/]+\.md$/.test(f.path)).map((f) => f.path.split("/").pop()).sort().reverse();
    return json({ weekly });
  },

  "GET /api/doc": async ({ env, url }) => {
    const name = url.searchParams.get("name") || "";
    const paths = { report: "analytics/report.md", trends: "analytics/trends.md" };
    let path = paths[name];
    if (!path && /^weekly\/[\w.-]+\.md$/.test(name)) path = `analytics/${name}`;
    if (!path) throw new HttpError(400, "unknown document");
    const text = await file(env, path, { fallback: null, ttl: 60 });
    if (text === null) throw new HttpError(404, "that page has not been written yet");
    return json({ name, path, text });
  },

  "GET /api/runs": async ({ env }) => {
    const all = await Promise.all([runs(env, "calendar", 30), runs(env, "publish", 15), runs(env, "weekly", 8)]);
    return json({ runs: all.flat().sort((a, b) => b.created_at.localeCompare(a.created_at)) });
  },

  "GET /api/run": async ({ env, url }) => {
    const id = url.searchParams.get("id");
    if (!/^\d+$/.test(id || "")) throw new HttpError(400, "bad run id");
    const r = await gh(env, `/repos/${repo(env)}/actions/runs/${id}/jobs`, { ttl: 5 });
    return json({
      jobs: (r.jobs || []).map((j) => ({
        name: j.name, status: j.status, conclusion: j.conclusion, url: j.html_url,
        steps: (j.steps || []).map((s) => ({ name: s.name, status: s.status, conclusion: s.conclusion, started_at: s.started_at, completed_at: s.completed_at })),
      })),
    });
  },

  // Streams a Reel's MP4 (zipped, as GitHub stores it) so the browser can play it.
  "GET /api/reel": async ({ env, url }) => {
    const runId = url.searchParams.get("run_id");
    const folder = url.searchParams.get("folder");
    const art = await findReelArtifact(env, runId, folder);
    if (!art) throw new HttpError(404, "the Reel video is not available (GitHub keeps it for 30 days)");
    const r = await fetch(`${env.GITHUB_API || "https://api.github.com"}/repos/${repo(env)}/actions/artifacts/${art.id}/zip`, {
      headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, "User-Agent": "rxt-dashboard", Accept: "application/vnd.github+json" },
      redirect: "manual",
    });
    const loc = r.headers.get("Location");
    if (!loc) throw new HttpError(502, `GitHub did not give a download link (${r.status})`);
    const zip = await fetch(loc);
    if (!zip.ok) throw new HttpError(502, "could not download the Reel from GitHub");
    return new Response(zip.body, { headers: { "Content-Type": "application/zip", "Cache-Control": "private, max-age=3600" } });
  },

  "GET /api/audit": async ({ env, url }) => json(await recent(env, 100, url.searchParams.get("cursor") || undefined)),

  // ------------------------------------------------------------ actions

  "POST /api/trigger": async ({ env, request, session }) => {
    if (!can.post(session)) throw new HttpError(403, "your role can only view");
    const b = await body(request);
    const slot = SLOTS.includes(b.slot) ? b.slot : "next";
    const format = FORMATS.includes(b.format) ? b.format : "auto";
    const force = b.force === true;
    if (await recentlyTriggered(env, "calendar")) throw new HttpError(409, "a post was started less than 90 seconds ago; check Runs before starting another");
    await markTriggered(env, "calendar", 90);
    let run;
    try {
      run = await dispatch(env, "calendar", { slot, format, force: String(force) });
    } catch (e) {
      await clearTrigger(env, "calendar");
      throw e;
    }
    await record(env, session, "started a post", { slot, format, force, run: run?.id ? String(run.id) : "" });
    return json({ ok: true, run, started_at: new Date().toISOString() });
  },

  "POST /api/publish": async ({ env, request, session }) => {
    if (!can.post(session)) throw new HttpError(403, "your role can only view");
    const b = await body(request);
    const latest = await file(env, "posts/latest.json", { json: true, ttl: 0 });
    if (!latest || latest.posted) throw new HttpError(409, "there is no draft waiting to be published");
    if (await recentlyTriggered(env, "publish")) throw new HttpError(409, "publishing was started less than 90 seconds ago");
    await markTriggered(env, "publish", 90);
    let run;
    try {
      run = await dispatch(env, "publish", { force: String(b.force === true) });
    } catch (e) {
      await clearTrigger(env, "publish");
      throw e;
    }
    await record(env, session, "published the draft", { topic: latest.topic, slot: latest.slot || "", force: b.force === true, run: run?.id ? String(run.id) : "" });
    return json({ ok: true, run, started_at: new Date().toISOString() });
  },

  "POST /api/autopublish": async ({ env, request, session }) => {
    if (!can.admin(session)) throw new HttpError(403, "only admins can change the publishing mode");
    const { enabled } = await body(request);
    if (typeof enabled !== "boolean") throw new HttpError(400, "enabled must be true or false");
    await setVariable(env, "AUTO_PUBLISH", enabled ? "true" : "false");
    await record(env, session, enabled ? "switched to auto-publish" : "switched to manual approval");
    return json({ ok: true, autoPublish: enabled });
  },
};

// ---------------------------------------------------------------- helpers

async function signIn(env, url, identity) {
  const role = roleFor(env, identity);
  if (!role) {
    await record(env, identity, "sign-in refused", { reason: "not on the allowlist" });
    throw new HttpError(403, `${identity.email} is not on the dashboard's allowlist. Ask an admin to add you.`);
  }
  const s = await makeSession(env, identity);
  await record(env, { ...identity, role }, "signed in");
  return json(publicSession({ ...identity, role }), 200, { "Set-Cookie": cookieHeader(COOKIE, s.value, { maxAge: s.maxAge, secure: !isLocal(url) }) });
}

function redirectHome(message) {
  return new Response(null, { status: 302, headers: { Location: `/#signin-error=${encodeURIComponent(message)}` } });
}

function publicSession(s) {
  return { email: s.email, github: s.github || null, name: s.name, picture: s.picture, role: s.role, via: s.via };
}

function slimLatest(l) {
  const { render, ...rest } = l; // the Reel render timeline is large and not needed
  return { ...rest, render: render ? { duration: render.duration, tts: render.tts, music: render.music } : undefined };
}

function liveEnabled(env) {
  return !!(env.IG_TOKEN && env.IG_USER_ID);
}

async function findReelArtifact(env, runId, folder) {
  if (runId && /^\d+$/.test(runId)) {
    const r = await gh(env, `/repos/${repo(env)}/actions/runs/${runId}/artifacts`, { ttl: 300 });
    const a = (r.artifacts || []).find((x) => x.name === `reel-${runId}` && !x.expired);
    if (a) return a;
  }
  // Older log entries have no run id: match the artifact made right after the post's folder.
  const m = /^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})$/.exec(folder || "");
  if (!m) return null;
  const made = Date.parse(`${m[1]}T${m[2]}:${m[3]}:${m[4]}Z`);
  const r = await gh(env, `/repos/${repo(env)}/actions/artifacts?per_page=100`, { ttl: 300 });
  return (r.artifacts || []).find((x) => x.name.startsWith("reel-") && !x.expired &&
    Date.parse(x.created_at) >= made - 60e3 && Date.parse(x.created_at) <= made + 45 * 60e3) || null;
}

const liveCache = { at: 0, value: null };

async function liveNumbers(env) {
  if (liveCache.value && Date.now() - liveCache.at < 10 * 60e3) return liveCache.value;
  const graph = env.IG_GRAPH || "https://graph.facebook.com/v27.0";
  const get = async (path, params) => {
    const u = new URL(`${graph}/${path}`);
    for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
    u.searchParams.set("access_token", env.IG_TOKEN);
    const r = await fetch(u);
    const d = await r.json();
    if (!r.ok) throw new Error(d.error?.message || `Instagram ${r.status}`);
    return d;
  };
  const out = { enabled: true, posts: {}, account: null, errors: [] };
  try {
    out.account = await get(env.IG_USER_ID, { fields: "followers_count,media_count,username" });
  } catch (e) {
    out.errors.push(`account: ${e.message}`);
  }
  let media = [];
  try {
    media = (await get(`${env.IG_USER_ID}/media`, { fields: "id,timestamp,media_type,permalink,like_count,comments_count", limit: "30" })).data || [];
  } catch (e) {
    out.errors.push(`media: ${e.message}`);
  }
  await Promise.all(media.map(async (m) => {
    const row = { timestamp: m.timestamp, permalink: m.permalink, media_type: m.media_type, likes: m.like_count, comments: m.comments_count };
    for (const metrics of ["reach,saved,shares,views", "reach,saved,shares"]) {
      try {
        for (const x of (await get(`${m.id}/insights`, { metric: metrics })).data || []) {
          row[x.name] = x.values ? (x.values[0] || {}).value : x.total_value?.value;
        }
        break;
      } catch (e) {
        if (metrics === "reach,saved,shares") out.errors.push(`${m.id}: ${e.message}`);
      }
    }
    out.posts[m.id] = row;
  }));
  out.errors = out.errors.slice(0, 5);
  out.fetched_at = new Date().toISOString();
  liveCache.at = Date.now();
  liveCache.value = out;
  return out;
}

// ---------------------------------------------------------------- entry point

const PUBLIC = new Set(["GET /api/config", "POST /api/auth/google", "POST /api/auth/dev", "GET /api/auth/github",
  "GET /api/auth/github/callback", "POST /api/auth/logout"]);

export async function onRequest({ request, env }) {
  const url = new URL(request.url);
  const key = `${request.method} ${url.pathname.replace(/\/+$/, "")}`;
  const handler = routes[key];
  if (!handler) return json({ error: "not found" }, 404);
  try {
    if (request.method !== "GET") {
      // Cookies are SameSite=Lax; also refuse any write that did not come from this site.
      const origin = request.headers.get("Origin");
      if (origin && origin !== url.origin) throw new HttpError(403, "cross-site request refused");
      if (!origin && key !== "POST /api/auth/logout") throw new HttpError(403, "missing Origin header");
    }
    let session = null;
    if (!PUBLIC.has(key)) {
      session = await readSession(env, request);
      if (!session) throw new HttpError(401, "please sign in");
    }
    return await handler({ request, env, url, session });
  } catch (e) {
    const status = e instanceof HttpError || e instanceof GitHubError ? e.status : 500;
    if (status === 500) console.error(e);
    return json({ error: e.message || "something went wrong" }, status >= 400 && status < 600 ? status : 500);
  }
}
