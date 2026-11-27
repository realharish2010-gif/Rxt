// RXT timer: a tiny Cloudflare Worker that starts the bot's GitHub workflows on time.
// GitHub runs scheduled workflows late or skips them when it is busy (on 26 Nov 2026 only 1 of
// about 27 calendar runs started on its own), so this Worker's Cron Triggers start them instead,
// through workflow_dispatch with timer=true. The bot treats those runs exactly like scheduled
// ones: it only posts when a slot is due, and its slot and daily checks stop double posts.
// GitHub's own schedule stays on as a backup.
//
// It never talks to Instagram. Its only secret is GITHUB_TOKEN (Actions: read and write on the repo).

const REPO = "realharish2010-gif/Rxt";

// Cron expression (UTC, as in wrangler.toml) -> workflow file to start
const JOBS = {
  "7,37 1-4,6-9,13-16 * * *": "calendar.yml", // every 30 min inside the morning, midday and evening windows
  "27 17 * * *": "health.yml", // 22:57 India time, after the evening window closes
  "17 0 * * MON": "weekly.yml", // Monday 05:47 India time
};

async function dispatch(env, workflow) {
  const repo = env.GITHUB_REPO || REPO;
  const url = `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`;
  for (let attempt = 1; attempt <= 3; attempt++) {
    const r = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "rxt-timer",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: env.GITHUB_BRANCH || "main", inputs: { timer: "true" } }),
    });
    if (r.ok) {
      console.log(`started ${workflow}`);
      return;
    }
    const text = await r.text();
    console.error(`attempt ${attempt}: GitHub ${r.status} for ${workflow}: ${text.slice(0, 300)}`);
    if (r.status < 500 && r.status !== 429) break; // a bad token or setting will not fix itself
    await new Promise((ok) => setTimeout(ok, 5000 * attempt));
  }
  throw new Error(`could not start ${workflow}`);
}

export default {
  async scheduled(event, env, ctx) {
    const workflow = JOBS[event.cron];
    if (!workflow) {
      console.error(`no workflow for cron "${event.cron}"; check wrangler.toml matches JOBS`);
      return;
    }
    if (!env.GITHUB_TOKEN) throw new Error("GITHUB_TOKEN secret is not set");
    ctx.waitUntil(dispatch(env, workflow));
  },

  // Visiting the Worker's URL only shows what it does; it never starts anything.
  async fetch() {
    return new Response("RXT timer: starts the bot's GitHub workflows on schedule.\n" +
      Object.entries(JOBS).map(([c, w]) => `${c}  ->  ${w}`).join("\n") + "\n",
    { headers: { "Content-Type": "text/plain" } });
  },
};
