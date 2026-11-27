# RXT timer (Cloudflare Worker)

GitHub starts scheduled workflows late, or skips them when it is busy. On 26 Nov 2026 only 1 of
about 27 calendar runs started on its own. This free Cloudflare Worker starts the bot's workflows
on time instead, through `workflow_dispatch` with `timer=true`:

| When (UTC) | India time | Starts |
|---|---|---|
| `7,37 1-4,6-9,13-16 * * *` | every 30 min, 06:37-10:07, 11:37-15:07, 18:37-22:07 | calendar.yml |
| `27 17 * * *` | 22:57 daily | health.yml |
| `17 0 * * MON` | Monday 05:47 | weekly.yml |

A timer run behaves exactly like a scheduled run: it posts only when a slot is due, and the
slot checks stop double posts. GitHub's own schedule stays on as a backup. The Worker never
talks to Instagram and never posts by itself.

## Set it up (about 5 minutes, Cloudflare dashboard)

1. **Workers & Pages → Create → Create Worker.** Name it `rxt-timer` and press Deploy.
2. **Edit code**, replace everything with the contents of `worker.js`, and press Deploy.
3. **Settings → Variables and Secrets → Add**: type *Secret*, name `GITHUB_TOKEN`, value = a
   fine-grained GitHub token with **Actions: Read and write** on the Rxt repo. The dashboard's
   token already has that, so you can paste the same one.
4. **Settings → Trigger events → Add → Cron Triggers**, and add the three expressions from the
   table above, one at a time.

Or from this folder with the CLI: `npx wrangler deploy`, then `npx wrangler secret put GITHUB_TOKEN`.

## Check it works

Cloudflare: the Worker's **Logs** show `started calendar.yml` each half hour inside the windows.
GitHub: Actions shows runs called **Content calendar (timer)**. The dashboard lists them as
"on schedule". When the token expires the Worker logs a 401, and the dashboard's token reminder
covers it if you use the same token.
