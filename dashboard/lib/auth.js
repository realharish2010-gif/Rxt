// Sign-in and sessions. Nothing secret ever goes to the browser: the session is an
// HttpOnly cookie signed with SESSION_SECRET, and roles are looked up from the allowlists
// on every request, so removing someone from the list locks them out straight away.

const enc = new TextEncoder();
const SESSION_HOURS = 12;
export const COOKIE = "rxt_session";

function b64url(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function unb64url(str) {
  const s = atob(str.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((str.length + 3) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

async function hmacKey(secret) {
  return crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export function cookies(request) {
  const out = {};
  for (const part of (request.headers.get("Cookie") || "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0) out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}

export function cookieHeader(name, value, { maxAge, secure = true } = {}) {
  return [`${name}=${encodeURIComponent(value)}`, "Path=/", "HttpOnly", "SameSite=Lax",
    secure ? "Secure" : "", maxAge !== undefined ? `Max-Age=${maxAge}` : ""].filter(Boolean).join("; ");
}

export async function makeSession(env, identity) {
  if (!env.SESSION_SECRET || env.SESSION_SECRET.length < 32) throw new Error("SESSION_SECRET must be set (32+ characters)");
  const payload = { ...identity, exp: Date.now() + SESSION_HOURS * 3600e3 };
  const body = b64url(enc.encode(JSON.stringify(payload)));
  const sig = await crypto.subtle.sign("HMAC", await hmacKey(env.SESSION_SECRET), enc.encode(body));
  return { value: `${body}.${b64url(sig)}`, maxAge: SESSION_HOURS * 3600 };
}

export async function readSession(env, request) {
  const raw = cookies(request)[COOKIE];
  if (!raw || !env.SESSION_SECRET) return null;
  const [body, sig] = raw.split(".");
  if (!body || !sig) return null;
  try {
    const ok = await crypto.subtle.verify("HMAC", await hmacKey(env.SESSION_SECRET), unb64url(sig), enc.encode(body));
    if (!ok) return null;
    const s = JSON.parse(new TextDecoder().decode(unb64url(body)));
    if (!s.exp || s.exp < Date.now()) return null;
    const role = roleFor(env, s);
    return role ? { ...s, role } : null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- allowlists

function list(v) {
  return String(v || "").split(/[,\s]+/).map((x) => x.trim().toLowerCase()).filter(Boolean);
}

function matches(entries, identity) {
  const emails = (identity.emails || [identity.email]).filter(Boolean).map((e) => e.toLowerCase());
  const login = identity.github ? `gh:${identity.github.toLowerCase()}` : null;
  return entries.some((e) => (login && e === login) ||
    emails.some((m) => m === e || (e.startsWith("@") && m.endsWith(e))));
}

// admin: everything, including pausing auto-publish. editor: can make and publish posts.
// viewer: read only. Someone on no list cannot sign in.
export function roleFor(env, identity) {
  if (matches(list(env.ADMINS), identity)) return "admin";
  if (matches(list(env.EDITORS), identity)) return "editor";
  if (matches(list(env.VIEWERS), identity)) return "viewer";
  return null;
}

export const can = {
  view: (s) => !!s,
  post: (s) => s && (s.role === "admin" || s.role === "editor"),
  admin: (s) => s && s.role === "admin",
};

// ---------------------------------------------------------------- Google

let jwks = { keys: [], at: 0 };

async function googleKeys(force) {
  if (!force && jwks.keys.length && Date.now() - jwks.at < 3600e3) return jwks.keys;
  const r = await fetch("https://www.googleapis.com/oauth2/v3/certs");
  if (!r.ok) throw new Error("could not load Google's signing keys");
  jwks = { keys: (await r.json()).keys, at: Date.now() };
  return jwks.keys;
}

// Checks a Google Identity Services ID token: Google's signature, our client id, not expired,
// and a verified email.
export async function verifyGoogle(env, token) {
  if (!env.GOOGLE_CLIENT_ID) throw new Error("GOOGLE_CLIENT_ID is not set");
  const parts = String(token || "").split(".");
  if (parts.length !== 3) throw new Error("bad token");
  const header = JSON.parse(new TextDecoder().decode(unb64url(parts[0])));
  const claims = JSON.parse(new TextDecoder().decode(unb64url(parts[1])));
  if (header.alg !== "RS256") throw new Error("bad token algorithm");
  let jwk = (await googleKeys()).find((k) => k.kid === header.kid);
  if (!jwk) jwk = (await googleKeys(true)).find((k) => k.kid === header.kid);
  if (!jwk) throw new Error("unknown signing key");
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, unb64url(parts[2]), enc.encode(`${parts[0]}.${parts[1]}`));
  if (!ok) throw new Error("bad signature");
  if (!["accounts.google.com", "https://accounts.google.com"].includes(claims.iss)) throw new Error("bad issuer");
  if (claims.aud !== env.GOOGLE_CLIENT_ID) throw new Error("token is for another app");
  if (!claims.exp || claims.exp * 1000 < Date.now()) throw new Error("token expired");
  if (!claims.email || claims.email_verified !== true) throw new Error("Google email is not verified");
  return { email: claims.email.toLowerCase(), name: claims.name || claims.email, picture: claims.picture || "", via: "google" };
}

// ---------------------------------------------------------------- GitHub (optional)

export function githubEnabled(env) {
  return !!(env.GITHUB_OAUTH_CLIENT_ID && env.GITHUB_OAUTH_CLIENT_SECRET);
}

export async function githubIdentity(env, code) {
  const r = await fetch("https://github.com/login/oauth/access_token", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: env.GITHUB_OAUTH_CLIENT_ID, client_secret: env.GITHUB_OAUTH_CLIENT_SECRET, code }),
  });
  const tok = (await r.json()).access_token;
  if (!tok) throw new Error("GitHub sign-in failed");
  const h = { Authorization: `Bearer ${tok}`, "User-Agent": "rxt-dashboard", Accept: "application/vnd.github+json" };
  const user = await (await fetch("https://api.github.com/user", { headers: h })).json();
  const emailsResp = await fetch("https://api.github.com/user/emails", { headers: h });
  const emails = emailsResp.ok ? (await emailsResp.json()).filter((e) => e.verified).map((e) => e.email.toLowerCase()) : [];
  // The user's GitHub token is used once here and then dropped; it is never stored.
  return { github: user.login, email: emails[0] || "", emails, name: user.name || user.login, picture: user.avatar_url || "", via: "github" };
}

export function randomState() {
  return b64url(crypto.getRandomValues(new Uint8Array(18)));
}
