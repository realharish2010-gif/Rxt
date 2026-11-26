// Every GitHub call goes through here, using the fine-grained token in GITHUB_TOKEN.
// The token stays on the server; the browser only ever talks to /api/*.

const cache = new Map(); // short-lived, per server instance, to stay far below GitHub's rate limit

// GitHub tells us when the dashboard's own token expires (a header on every API answer).
// It is copied into the repo variable DASHBOARD_TOKEN_EXPIRES so the bot's nightly health
// check (alerts.py) can email a reminder before the dashboard stops working.
export const EXPIRY_VAR = "DASHBOARD_TOKEN_EXPIRES";
const expiry = { seen: null, synced: null };

export function tokenExpiry() {
  return expiry.seen;
}

function noteExpiry(r) {
  const h = r.headers.get("github-authentication-token-expiration"); // e.g. "2027-11-26 13:00:00 UTC"
  if (!h) return;
  const d = new Date(h.replace(" UTC", "Z").replace(/ ([+-]\d{2})(\d{2})$/, "$1:$2").replace(" ", "T"));
  if (!Number.isNaN(d.getTime())) expiry.seen = d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

// Called after a request (in the background): save the expiry to the repo variable once
// per server instance, and only when it changed (e.g. after the token was renewed).
export async function syncTokenExpiry(env) {
  if (!expiry.seen || expiry.seen === expiry.synced) return;
  try {
    if ((await variable(env, EXPIRY_VAR)) !== expiry.seen) await setVariable(env, EXPIRY_VAR, expiry.seen);
    expiry.synced = expiry.seen;
  } catch (e) {
    console.error("could not save the token expiry", e);
  }
}

export class GitHubError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export function repo(env) {
  const r = env.GITHUB_REPO || "realharish2010-gif/Rxt";
  if (!/^[\w.-]+\/[\w.-]+$/.test(r)) throw new Error("GITHUB_REPO must look like owner/name");
  return r;
}

export function branch(env) {
  return env.GITHUB_BRANCH || "main";
}

export async function gh(env, path, { method = "GET", body, raw = false, ttl = 0, accept } = {}) {
  if (!env.GITHUB_TOKEN) throw new GitHubError(500, "GITHUB_TOKEN is not set on the server");
  const url = /^https?:\/\//.test(path) ? path : `${env.GITHUB_API || "https://api.github.com"}${path}`;
  const key = `${raw}|${url}`;
  if (method === "GET" && ttl) {
    const hit = cache.get(key);
    if (hit && hit.until > Date.now()) return hit.value;
  }
  const r = await fetch(url, {
    method,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: accept || (raw ? "application/vnd.github.raw+json" : "application/vnd.github+json"),
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "rxt-dashboard",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  noteExpiry(r);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).message || msg; } catch { /* not json */ }
    if (r.status === 403 || r.status === 401) msg += " (check the GITHUB_TOKEN permissions in the setup guide)";
    throw new GitHubError(r.status, `GitHub: ${msg}`);
  }
  let value = null;
  if (r.status !== 204) {
    const text = await r.text();
    value = raw ? text : text ? JSON.parse(text) : null;
  }
  if (method === "GET" && ttl) cache.set(key, { value, until: Date.now() + ttl * 1000 });
  if (method !== "GET") cache.clear(); // anything we change should show up at once
  return value;
}

// A file from the repo's main branch, as text (or parsed JSON). Missing files give `fallback`.
export async function file(env, path, { json = false, fallback = null, ttl = 45 } = {}) {
  try {
    const text = await gh(env, `/repos/${repo(env)}/contents/${path.split("/").map(encodeURIComponent).join("/")}?ref=${encodeURIComponent(branch(env))}`, { raw: true, ttl });
    return json ? JSON.parse(text) : text;
  } catch (e) {
    if (e.status === 404) return fallback;
    throw e;
  }
}

export async function tree(env) {
  const t = await gh(env, `/repos/${repo(env)}/git/trees/${encodeURIComponent(branch(env))}?recursive=1`, { ttl: 60 });
  return t.tree || [];
}

export const WORKFLOWS = { calendar: "calendar.yml", publish: "publish.yml", weekly: "weekly.yml", health: "health.yml" };

export async function runs(env, workflow, perPage = 15) {
  const r = await gh(env, `/repos/${repo(env)}/actions/workflows/${WORKFLOWS[workflow]}/runs?per_page=${perPage}`, { ttl: 15 });
  return (r.workflow_runs || []).map((x) => ({
    id: x.id, workflow, name: x.name, title: x.display_title, event: x.event, status: x.status,
    conclusion: x.conclusion, created_at: x.created_at, updated_at: x.updated_at,
    started_at: x.run_started_at, url: x.html_url, actor: x.triggering_actor?.login || x.actor?.login,
  }));
}

export async function dispatch(env, workflow, inputs) {
  const path = `/repos/${repo(env)}/actions/workflows/${WORKFLOWS[workflow]}/dispatches`;
  // return_run_details asks GitHub for the new run's id; older API versions just answer 204.
  let res;
  try {
    res = await gh(env, path, { method: "POST", body: { ref: branch(env), inputs, return_run_details: true } });
  } catch (e) {
    if (e.status !== 422 || !/return_run_details/.test(e.message)) throw e;
    res = await gh(env, path, { method: "POST", body: { ref: branch(env), inputs } });
  }
  return res && (res.workflow_run_id || res.id) ? { id: res.workflow_run_id || res.id, url: res.html_url } : null;
}

export async function variable(env, name) {
  try {
    return (await gh(env, `/repos/${repo(env)}/actions/variables/${name}`, { ttl: 15 })).value;
  } catch (e) {
    if (e.status === 404) return null;
    throw e;
  }
}

export async function setVariable(env, name, value) {
  try {
    await gh(env, `/repos/${repo(env)}/actions/variables/${name}`, { method: "PATCH", body: { name, value } });
  } catch (e) {
    if (e.status !== 404) throw e;
    await gh(env, `/repos/${repo(env)}/actions/variables`, { method: "POST", body: { name, value } });
  }
}

// Failure alerts the bot opens as GitHub issues labelled bot-alert (see alerts.py in the repo).
// Open ones come with their latest comment, since a repeat failure is added as a comment.
export async function alerts(env, { closed = 10 } = {}) {
  const list = await gh(env, `/repos/${repo(env)}/issues?labels=bot-alert&state=all&sort=updated&direction=desc&per_page=${20 + closed}`, { ttl: 30 });
  const slim = (i) => ({
    number: i.number, title: i.title, state: i.state, url: i.html_url, comments: i.comments,
    created_at: i.created_at, updated_at: i.updated_at, closed_at: i.closed_at, body: cleanBody(i.body),
  });
  const open = (list || []).filter((i) => i.state === "open" && !i.pull_request).map(slim);
  await Promise.all(open.filter((i) => i.comments).map(async (i) => {
    try {
      const c = await gh(env, `/repos/${repo(env)}/issues/${i.number}/comments?per_page=100`, { ttl: 30 });
      const last = (c || []).slice(-1)[0];
      if (last) i.latest = { at: last.created_at, body: cleanBody(last.body) };
    } catch { /* the issue text is enough */ }
  }));
  const recent = (list || []).filter((i) => i.state === "closed" && !i.pull_request).slice(0, closed).map(slim);
  return { open, closed: recent };
}

function cleanBody(b) {
  return String(b || "").replace(/\n*_cc @[\w-]+_\s*$/, "").trim();
}

export async function closeIssue(env, number, note) {
  if (note) await gh(env, `/repos/${repo(env)}/issues/${number}/comments`, { method: "POST", body: { body: note } });
  await gh(env, `/repos/${repo(env)}/issues/${number}`, { method: "PATCH", body: { state: "closed", state_reason: "completed" } });
}
