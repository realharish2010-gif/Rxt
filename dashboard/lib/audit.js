// Who did what, kept in a Cloudflare KV namespace bound as AUDIT (free tier is plenty).
// Keys sort newest first, so the log page is one cheap list call.

const KEEP_DAYS = 365;

export async function record(env, session, action, details = {}) {
  if (!env.AUDIT) return;
  const at = new Date();
  // KV metadata holds at most 1 KB, so keep each detail short.
  for (const k of Object.keys(details)) {
    if (typeof details[k] === "string" && details[k].length > 120) details[k] = details[k].slice(0, 117) + "...";
  }
  const key = `log:${String(9e15 - at.getTime()).padStart(16, "0")}:${crypto.randomUUID().slice(0, 8)}`;
  const entry = {
    at: at.toISOString(), action, details,
    who: session ? (session.email || `gh:${session.github}`) : "unknown",
    name: session?.name || "", via: session?.via || "", role: session?.role || "",
  };
  await env.AUDIT.put(key, JSON.stringify(entry), { expirationTtl: KEEP_DAYS * 86400, metadata: entry });
}

export async function recent(env, limit = 100, cursor) {
  if (!env.AUDIT) return { entries: [], disabled: true };
  const r = await env.AUDIT.list({ prefix: "log:", limit, cursor });
  return { entries: r.keys.map((k) => k.metadata).filter(Boolean), cursor: r.list_complete ? null : r.cursor };
}

// A tiny guard against double-clicks and two people pressing at once.
export async function recentlyTriggered(env, what) {
  if (!env.AUDIT) return false;
  return !!(await env.AUDIT.get(`lock:${what}`));
}

export async function markTriggered(env, what, seconds = 90) {
  if (env.AUDIT) await env.AUDIT.put(`lock:${what}`, "1", { expirationTtl: Math.max(60, seconds) });
}

export async function clearTrigger(env, what) {
  if (env.AUDIT) await env.AUDIT.delete(`lock:${what}`);
}
