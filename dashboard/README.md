# RXT Marketing dashboard

A private website for the team to run the Instagram bot without touching GitHub:

- **New post**: make a post now. Pick the slot (or "next open slot") and the format (carousel, Reel, or let the bot decide). The page shows what will happen before you press the button, including the daily limits (3 a day, 150 minutes apart, one per slot), and offers **Post anyway** to skip them.
- **Publishing mode**: switch between **Auto-publish** (posts go out by themselves) and **Manual approval** (each post waits on the Overview page so someone can preview every slide, the Reel and the caption, then press **Approve and publish**).
- **Alerts**: the bot's failure alerts (failed posts, Reels or runs, Instagram token problems, missed slots), the same ones emailed to you as GitHub issues labelled `bot-alert`. Overview shows a red banner and the menu a count while any are open. Editors can run the daily health check on demand and mark an alert fixed. Needs the token's Issues permission (step 1); an older token without it just shows a note.
- **Analytics**: posts, reach, saves and shares, with averages by content type, slot, format, hook style, topic area and design, a per-post chart and a full table. Uses the bot's own 3-day measurements, plus live Instagram numbers if you add the optional Instagram token.
- **Posts**: every post with its slides, Reel video, caption, tags, the bot's reasons for choosing it, and its numbers.
- **Runs**: what the bot is doing on GitHub Actions, live step-by-step progress for a post you just started, and failures.
- **Reports**: the Monday weekly recap, "What works" and the trends report, as the bot writes them.
- **Activity log**: who signed in, who started or published a post, and who changed the publishing mode.

Signed-out visitors see a one-screen landing page (black, with a background video); every link on it opens the sign-in box. People sign in with Google (GitHub sign-in is optional). Only email addresses on your allowlist can get in.

It lives entirely in this `dashboard/` folder. It does not change the bot's code or its workflows, and it adds no GitHub Actions workflows. It only does what a person could do by hand on GitHub: run the **Content calendar** and **Publish latest post** workflows, and change the `AUTO_PUBLISH` variable.

## How it works

```
Browser ──► Cloudflare Pages (static site in public/)
        └─► /api/* (Cloudflare Pages Functions in functions/ and lib/)
                ├─► GitHub API with your fine-grained token (never sent to the browser)
                ├─► Instagram API (optional, for live numbers)
                └─► Cloudflare KV "AUDIT" (activity log)
```

Everything here is free on Cloudflare's free plan (100,000 requests a day, 1,000 activity-log writes a day).

## Roles

| Role | Can do |
|---|---|
| `viewer` | See everything |
| `editor` | Also make new posts and approve drafts |
| `admin` | Also switch between auto-publish and manual approval |

You set them with three lists (see step 3). An entry is an email address (`asha@company.com`), a whole domain (`@company.com`), or a GitHub username (`gh:asha-dev`, only if you turn on GitHub sign-in). Anyone on no list cannot sign in. Changes take effect straight away, even for people already signed in.

---

## Setup (about 20 minutes)

You need a free Cloudflare account and a Google account. Do the steps in order.

### 1. Make a GitHub token for the dashboard

1. Open <https://github.com/settings/personal-access-tokens/new> (Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token).
2. **Token name**: `rxt-dashboard`. **Expiration**: up to 1 year (put a reminder in your calendar to renew it).
3. **Resource owner**: `realharish2010-gif`. **Repository access**: *Only select repositories* → `Rxt`.
4. **Repository permissions**:
   - **Actions**: Read and write (to start posts and see runs)
   - **Contents**: Read-only (to read posts and reports)
   - **Variables**: Read and write (for the publishing-mode switch)
   - **Issues**: Read and write (to show the bot's failure alerts and mark them fixed)
   - Metadata: Read-only is added automatically.
   Leave everything else as *No access*.
5. Generate it and copy the token (`github_pat_...`). You will paste it into Cloudflare in step 3. Do not put it in the repo: the repo is public.

### 2. Make a Google sign-in client

1. Open <https://console.cloud.google.com/> and create a project (for example `RXT dashboard`).
2. Go to **Google Auth Platform** (search for it in the top bar). Fill in **Branding**: app name `RXT Marketing`, your support email.
3. **Audience**: choose **External**, then press **Publish app** so anyone on your allowlist can sign in. (The dashboard only asks Google for name and email, which needs no Google review. The allowlist decides who gets in.)
4. **Clients** → **Create client** → type **Web application**, name `RXT dashboard`.
5. **Authorized JavaScript origins**: add your site address, for example `https://rxt-dashboard.pages.dev` (you will know the exact address after step 3; you can come back and add it). No redirect URIs are needed.
6. Create it and copy the **Client ID** (ends in `.apps.googleusercontent.com`). The client secret is not used.

### 3. Put the site on Cloudflare Pages

1. Sign in at <https://dash.cloudflare.com/> → **Workers & Pages** → **Create** → **Pages** → **Connect to Git**, and give Cloudflare access to the `Rxt` repository.
2. Build settings:
   - **Production branch**: `main`
   - **Framework preset**: None
   - **Build command**: leave empty
   - **Build output directory**: `public`
   - **Root directory (advanced)**: `dashboard`
3. Before the first deploy (or afterwards under **Settings → Variables and Secrets**), add these. Use **Encrypt / Secret** for the first two:

   | Name | Value |
   |---|---|
   | `GITHUB_TOKEN` (secret) | the token from step 1 |
   | `SESSION_SECRET` (secret) | any random text of 32+ characters, for example from a password generator |
   | `GOOGLE_CLIENT_ID` | the client ID from step 2 |
   | `ADMINS` | your email, for example `you@gmail.com` |
   | `EDITORS` | emails or `@yourcompany.com`, comma separated (can be empty) |
   | `VIEWERS` | emails or domains, comma separated (can be empty) |
   | `GITHUB_REPO` | `realharish2010-gif/Rxt` |

4. Save and deploy. Your site address appears at the top (for example `https://rxt-dashboard.pages.dev`). Add it to the Google client's **Authorized JavaScript origins** (step 2.5) if you have not yet.
5. **Only rebuild when the dashboard changes**: the bot commits to `main` several times a day. Under **Settings → Builds → Build watch paths**, set *Include paths* to `dashboard/*`, so those commits do not rebuild the site.

### 4. Turn on the activity log

1. In Cloudflare: **Storage & Databases → KV → Create** a namespace called `rxt-dashboard-audit`.
2. Back in your Pages project: **Settings → Bindings → Add → KV namespace**. Variable name `AUDIT`, namespace `rxt-dashboard-audit`.
3. **Deployments** → on the latest one, **Retry deployment** so the binding takes effect.

The activity log also stops two people starting a post within 90 seconds of each other by accident.

### 5. Try it

Open the site, sign in with Google, and check:

- Overview shows today's calendar and the publishing mode.
- Analytics shows "No numbers yet" until the first post is 3 days old (the bot measures each post at 3 days), unless you do step 6.
- New post shows "What will happen" for your choices. Start one, and the Runs page follows it step by step.

### 6. Optional: live Instagram numbers

Add two more variables to the Pages project, as secrets: `IG_TOKEN` and `IG_USER_ID`, the same values the bot uses in the repository's Actions secrets. Then Analytics gets a **Live now** view, Posts show today's numbers, and Overview shows your follower count. Numbers are cached for 10 minutes.

This puts a second copy of the Instagram token in Cloudflare. It is never sent to the browser, but skip this step if you prefer to keep that token only in GitHub.

### 7. Optional: GitHub sign-in

For teammates who would rather use GitHub:

1. <https://github.com/settings/developers> → **OAuth Apps** → **New OAuth App**. Homepage URL: your site. Authorization callback URL: `https://YOUR-SITE/api/auth/github/callback`.
2. Generate a client secret. In Cloudflare add `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET` (secret).
3. Add those people to a role list as `gh:their-username` (or by the verified email on their GitHub account).

### Custom domain (optional)

Pages project → **Custom domains** → add, for example, `dashboard.yourcompany.com`. Then add that address to the Google client's Authorized JavaScript origins (and the GitHub OAuth app, if used).

---

## Manual approval: how it works

Manual approval sets the repository variable `AUTO_PUBLISH` to `false`, which the bot already supports:

1. The calendar keeps making posts on schedule (3 a day), but does not publish them.
2. The newest post shows on the Overview under **Waiting to publish**. Press **Preview and publish** to see every slide (or play the Reel) and the caption, then **Approve and publish**. This runs the **Publish latest post** workflow.
3. Switch back to **Auto-publish** any time.

One thing to know: the bot only keeps one draft (the newest). If a draft is not approved before the next slot makes a new post, the new one replaces it. With slots at least 150 minutes apart there is usually plenty of time. A queue of several drafts, or a "skip this draft" button, would need a small change to the bot itself.

## Security notes

- The GitHub token, the session secret and the optional Instagram token are Cloudflare secrets. The browser only ever talks to `/api/*` on your own site.
- Sign-in sessions last 12 hours, are stored in an HttpOnly, Secure, SameSite cookie, and are checked against the allowlists on every request.
- Google sign-in tokens are checked against Google's signing keys, your client ID, expiry and a verified email.
- Every action is refused unless it comes from your own site (Origin check), and roles are enforced on the server.
- The site sends `noindex` headers and a strict Content Security Policy.
- To lock everyone out at once, delete or rotate `SESSION_SECRET`. To cut off GitHub access, revoke the token in GitHub settings.

## Working on the dashboard locally

```bash
cd dashboard
npm install
cp .dev.vars.example .dev.vars   # fill in a token; DEV_LOGIN gives a local test sign-in button
npm run dev                       # http://localhost:8788
```

`DEV_LOGIN` only works on `localhost`, never on the live site. You can also deploy from your computer instead of the Git connection with `npx wrangler pages deploy public --project-name rxt-dashboard`.

### Files

| Path | What it is |
|---|---|
| `public/` | The website: `index.html` (landing page and app shell), `landing.js` (entrance animation fallback and phone menu), `app.js` (all dashboard pages), `style.css`, `markdown.js` (renders the bot's reports safely), `hero.mp4` and `hero-poster.jpg` (landing background video, H.264 so every browser plays it), `_headers` (security headers), self-hosted Inter and Instrument Serif fonts (SIL Open Font License, see `FONTS-LICENSE.txt`) |
| `functions/api/[[path]].js` | The backend: every `/api/*` route |
| `lib/auth.js` | Google and GitHub sign-in, sessions, roles |
| `lib/github.js` | GitHub API calls (files, runs, starting workflows, the `AUTO_PUBLISH` variable) |
| `lib/audit.js` | Activity log in Cloudflare KV |
