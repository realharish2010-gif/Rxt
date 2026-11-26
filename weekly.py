"""Weekly recap: every Monday morning (India time), a short summary of last week on Instagram.

    python main.py weekly   -> writes analytics/weekly/<week-ending>.md and opens a GitHub
                               issue with the same text (GitHub emails it to you)

What is in it: total reach and saves, followers gained, the best and worst post, results by
content type and by time slot, how the content mix shifted (and what the bot now leans
towards), and anything that went wrong: failed runs, missed slots, token problems.

A week runs Monday to Sunday, India time. Run by hand with WEEK_ENDING=2026-11-29 (a Sunday)
to redo a week, and FORCE=true to overwrite a recap that already exists.

Fair comparisons: posts keep gaining reach for days, so the best/worst ranking uses each
post's 3-day measurement (from the learning loop) when it has one, otherwise its numbers
now, and leaves out posts that are under 2 days old. Totals are always the numbers now.
"""
import collections
import datetime as dt
import json
import os
import pathlib

import requests

import learning
import slots

ROOT = pathlib.Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text())
DIR = ROOT / "analytics" / "weekly"
STATE = DIR / "state.json"  # a few numbers per week, for week-over-week changes
API = "https://api.github.com"
# friendly names for the 4 content types (learning.py calls news content "trend-tied")
TYPE_NAMES = {"educational": "Educational", "trend-tied": "News content",
              "general trends": "General trends", "promotional": "Promotional"}
MIN_AGE_TO_RANK = dt.timedelta(days=2)


# ---------------------------------------------------------------- helpers

def week_bounds():
    """(first day, last day) of the week to report: Monday to Sunday, local time."""
    if os.environ.get("WEEK_ENDING"):
        end = dt.date.fromisoformat(os.environ["WEEK_ENDING"])
    else:
        today = slots.local_now().date()
        end = today - dt.timedelta(days=today.weekday() + 1)  # the Sunday before today
    return end - dt.timedelta(days=6), end


def days(start, end):
    return [(start + dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def type_name(angle):
    angle = learning.OLD_ANGLES.get(angle, angle)
    return TYPE_NAMES.get(angle, angle or "unknown")


def pct(a, b):
    if not b:
        return ""
    change = (a - b) / b
    return f" ({'+' if change >= 0 else ''}{change:.0%} vs the week before)"


def fmt_day(d):
    d = dt.date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d.day} {d:%b}"


def starting_shares():
    shares = {"trend-tied": CFG.get("trends", {}).get("share", 0),
              "general trends": CFG.get("general_trends", {}).get("share", 0),
              "promotional": CFG.get("promotional", {}).get("share", 0)}
    shares["educational"] = max(0.0, 1 - sum(shares.values()))
    return shares


# ---------------------------------------------------------------- Instagram

def ig_ready():
    return bool(os.environ.get("IG_TOKEN") and os.environ.get("IG_USER_ID"))


def account_reach(start, end):
    """Accounts reached across the whole week (each account counted once), or None."""
    since = dt.datetime.combine(start, dt.time(), slots.tz())
    until = dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), slots.tz())
    data = learning.graph_get(f"{os.environ['IG_USER_ID']}/insights", metric="reach", period="day",
                              metric_type="total_value", since=int(since.timestamp()),
                              until=int(until.timestamp()))["data"]
    for row in data:
        if row.get("name") == "reach":
            return int(row.get("total_value", {}).get("value") or 0)
    return None


def token_check(problems):
    """Is the token alive and does it have the permissions the bot uses?"""
    try:
        me = learning.graph_get(os.environ["IG_USER_ID"], fields="username,followers_count")
    except learning.GraphError as e:
        problems.append(f"**Instagram token problem:** Instagram refused a basic request ({e}). "
                        "Posting and measuring will fail until IG_TOKEN is replaced.")
        return None
    try:
        perms = learning.graph_get("me/permissions").get("data", [])
        granted = {p["permission"] for p in perms if p.get("status") == "granted"}
        missing = [p for p in ("instagram_basic", "instagram_content_publish", "instagram_manage_insights")
                   if granted and p not in granted]
        if missing:
            problems.append("**Instagram token is missing permissions:** " + ", ".join(missing)
                            + ". Generate a new token that includes them.")
    except learning.GraphError:
        pass  # some token types cannot list their permissions; the calls above still prove it works
    return me


def week_posts(start, end, log, problems):
    """Every carousel that went out this week, with its numbers now and its 3-day snapshot."""
    inweek = set(days(start, end))
    store = learning.load_store()
    posts = []
    media = []
    if ig_ready():
        try:
            media = learning.graph_get(f"{os.environ['IG_USER_ID']}/media",
                                       fields="id,caption,media_type,timestamp,permalink", limit=50)["data"]
        except learning.GraphError as e:
            problems.append(f"**Could not list Instagram posts:** {e}")
    seen = set()
    for m in media:
        posted = learning.parse_time(m["timestamp"])
        day = posted.astimezone(slots.tz()).date().isoformat()
        if day not in inweek or m.get("media_type") not in ("CAROUSEL_ALBUM", "VIDEO"):
            continue
        meta = learning.match_log(m, posted, log)
        if meta:
            seen.add(id(meta))
        live = None
        try:
            live = learning.fetch_metrics(m["id"])
        except learning.GraphError as e:
            problems.append(f"**Could not read post insights:** {e}")
        snap = (store.get(m["id"]) or {}).get("metrics")
        posts.append({**meta, "media_id": m["id"], "permalink": m.get("permalink"), "posted": posted,
                      "local_day": day, "slot": meta.get("slot") or slots.slot_of(posted),
                      "angle": learning.OLD_ANGLES.get(meta.get("angle"), meta.get("angle")),
                      "topic": meta.get("topic") or (m.get("caption") or "").split("\n")[0][:80],
                      "format": meta.get("format") or ("reel" if m.get("media_type") == "VIDEO" else "carousel"),
                      "by_hand": not meta, "live": live, "snap": snap})
    # logged as published but not found on Instagram (no token, or deleted)
    for p in log:
        d, sl = slots.where(p)
        if d in inweek and id(p) not in seen:
            posted = dt.datetime.fromisoformat(p["at"]) if p.get("at") else None
            posts.append({**p, "posted": posted, "local_day": d, "slot": sl, "live": None, "snap": None,
                          "angle": learning.OLD_ANGLES.get(p.get("angle"), p.get("angle")), "by_hand": False})
    posts.sort(key=lambda p: p["posted"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    return posts


def metrics(p):
    return p.get("live") or p.get("snap") or {}


# ---------------------------------------------------------------- GitHub

def gh(method, path, **kw):
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        return None
    r = requests.request(method, f"{API}/repos/{repo}{path}", timeout=30, **kw,
                         headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub {method} {path}: {r.status_code} {r.text[:200]}")
    return r.json() if r.text else {}


def failed_runs(start, end):
    """[(workflow name, local time, url)] for runs that failed during the week, or None."""
    since = dt.datetime.combine(start, dt.time(), slots.tz()).astimezone(dt.timezone.utc)
    until = dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), slots.tz()).astimezone(dt.timezone.utc)
    rng = f"{since:%Y-%m-%dT%H:%M:%SZ}..{until:%Y-%m-%dT%H:%M:%SZ}"
    out = []
    for status in ("failure", "timed_out"):
        data = gh("GET", "/actions/runs", params={"created": rng, "status": status, "per_page": 100})
        if data is None:
            return None
        for r in data.get("workflow_runs", []):
            when = dt.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone(slots.tz())
            out.append((r["name"], when, r["html_url"]))
    return sorted(out, key=lambda x: x[1])


def open_issue(title, body):
    """Open this week's recap as an issue (GitHub emails it) and close last week's."""
    label = "weekly-summary"
    try:
        gh("POST", "/labels", json={"name": label, "color": "0e8a16", "description": "Monday Instagram recap"})
    except RuntimeError:
        pass  # already exists
    for old in gh("GET", "/issues", params={"labels": label, "state": "open", "per_page": 20}) or []:
        gh("PATCH", f"/issues/{old['number']}", json={"state": "closed", "state_reason": "completed"})
    who = os.environ.get("NOTIFY", "").strip().lstrip("@")
    if who:
        body += f"\n\n_cc @{who}_\n"
    issue = gh("POST", "/issues", json={"title": title, "body": body, "labels": [label]})
    return issue and issue.get("html_url")


# ---------------------------------------------------------------- the recap

def table(rows, head):
    return [f"| {' | '.join(head)} |", "|" + "---|" * len(head), *[f"| {' | '.join(map(str, r))} |" for r in rows]]


def group(posts, key, names):
    out = []
    for name in names:
        g = [p for p in posts if key(p) == name]
        if not g:
            continue
        m = [metrics(p) for p in g]
        n = len(g)
        out.append((name, n, round(sum(x.get("reach", 0) for x in m) / n), round(sum(x.get("saved", 0) for x in m) / n, 1),
                    round(sum(x.get("shares", 0) for x in m) / n, 1)))
    return out


def link(p):
    name = (p.get("hook") or p.get("topic") or "untitled").replace("|", "/")
    return f"[{name}]({p['permalink']})" if p.get("permalink") else name


def build(start, end):
    problems, notes = [], []
    log = slots.published_log()
    history = json.loads((ROOT / "history.json").read_text()) if (ROOT / "history.json").exists() else []
    me = None
    if ig_ready():
        me = token_check(problems)
    else:
        problems.append("**IG_TOKEN / IG_USER_ID are not set** for this workflow, so no Instagram numbers.")
    posts = week_posts(start, end, log, problems)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    prev = state.get((start - dt.timedelta(days=1)).isoformat(), {})
    now = learning.now()

    reach = sum(metrics(p).get("reach", 0) for p in posts)
    saves = sum(metrics(p).get("saved", 0) for p in posts)
    shares = sum(metrics(p).get("shares", 0) for p in posts)
    acct = None
    if me:
        try:
            acct = account_reach(start, end)
        except learning.GraphError as e:
            notes.append(f"Account-level reach was not available ({e}).")
    followers = (me or {}).get("followers_count")

    week = {"posts": len(posts), "reach": reach, "saves": saves, "shares": shares, "account_reach": acct,
            "followers": followers, "mix": dict(collections.Counter(p.get("angle") or "unknown" for p in posts))}

    title = f"Weekly recap: {fmt_day(start)} to {fmt_day(end)} {end.year}"
    out = [f"# {title}", ""]
    n_slots = len(slots.slot_names()) * 7
    out.append(f"**{len(posts)} post{'' if len(posts) == 1 else 's'}** went out (of {n_slots} calendar slots).")
    bits = [f"**Reach on this week's posts:** {reach:,}{pct(reach, prev.get('reach'))}",
            f"**Saves:** {saves:,}{pct(saves, prev.get('saves'))}",
            f"**Shares:** {shares:,}{pct(shares, prev.get('shares'))}"]
    if acct is not None:
        bits.append(f"**Accounts reached (whole account, each counted once):** {acct:,}{pct(acct, prev.get('account_reach'))}")
    if followers is not None:
        gained = followers - prev["followers"] if prev.get("followers") is not None else None
        bits.append(f"**Followers:** {followers:,}" + (f" ({'+' if gained >= 0 else ''}{gained} this week)"
                                                         if gained is not None else " (first recap, so no change yet)"))
    out += ["", *[f"- {b}" for b in bits], ""]
    young = [p for p in posts if p.get("posted") and now - p["posted"] < dt.timedelta(days=learning.settings()["measure_after_days"])]
    if young:
        out.append(f"_{len(young)} of these posts are under {learning.settings()['measure_after_days']} days old "
                   "and still gaining reach, so the totals will grow._")
        out.append("")

    # best and worst
    def rank_metrics(p):
        return p.get("snap") or p.get("live")

    rankable = [p for p in posts if rank_metrics(p) and (p.get("snap") or now - p["posted"] >= MIN_AGE_TO_RANK)]
    out += ["## Best and worst post", ""]
    if rankable:
        sc = lambda p: learning.score({"metrics": rank_metrics(p)})
        rankable.sort(key=sc, reverse=True)

        def line(label, p):
            m = rank_metrics(p)
            basis = "at 3 days" if p.get("snap") else f"at {(now - p['posted']).days} days"
            return (f"- **{label}:** {link(p)} ({type_name(p.get('angle'))}, {p.get('slot') or 'no slot'}, "
                    f"{fmt_day(p['local_day'])}): reach {m.get('reach', 0):,}, saves {m.get('saved', 0)}, "
                    f"shares {m.get('shares', 0)} ({basis})")

        out.append(line("Best", rankable[0]))
        if len(rankable) > 1:
            out.append(line("Worst", rankable[-1]))
        left = len(posts) - len(rankable)
        if left:
            out.append(f"- {left} newer post{'s' if left > 1 else ''} were too young to rank fairly.")
    else:
        out.append("Not enough measured posts yet to name a best and worst.")
    out.append("")

    # by type and slot
    head = ["", "Posts", "Avg reach", "Avg saves", "Avg shares"]
    by_type = group(posts, lambda p: p.get("angle"), learning.ANGLES)
    by_slot = [(r[0].capitalize(), *r[1:]) for r in group(posts, lambda p: p.get("slot"), slots.slot_names() + ["extra"])]
    if by_type:
        out += ["## By content type", "", *table([(type_name(r[0]), *r[1:]) for r in by_type], head), ""]
    if by_slot:
        out += ["## By time slot", "", *table(by_slot, head), ""]
    by_format = group(posts, lambda p: p.get("format") or "carousel", ["carousel", "reel"])
    if len(by_format) > 1:
        out += ["## Carousels vs Reels", "", *table([(r[0].capitalize(), *r[1:]) for r in by_format], head), ""]

    # mix shift
    out += ["## How the content mix shifted", ""]
    start_mix = starting_shares()
    prev_mix = prev.get("mix")
    rows = []
    for a in learning.ANGLES:
        this = week["mix"].get(a, 0)
        row = [type_name(a), f"{this} ({this / len(posts):.0%})" if posts else "0"]
        if prev_mix is not None:
            total = sum(prev_mix.values()) or 1
            row.append(f"{prev_mix.get(a, 0)} ({prev_mix.get(a, 0) / total:.0%})")
        row.append(f"{start_mix.get(a, 0):.0%}")
        rows.append(row)
    out += table(rows, ["Type", "This week"] + (["Week before"] if prev_mix is not None else []) + ["Starting mix"])
    out.append("")
    out += leaning()
    out.append("")

    # problems
    out += ["## Failures and warnings", ""]
    runs = failed_runs(start, end) if os.environ.get("GITHUB_TOKEN") else None
    if runs:
        problems.append(f"**{len(runs)} failed workflow run{'s' if len(runs) > 1 else ''}:** "
                        + "; ".join(f"[{n} {w:%a %H:%M}]({u})" for n, w, u in runs[:8])
                        + (" and more" if len(runs) > 8 else ""))
    elif runs is None:
        notes.append("Workflow runs were not checked (no GitHub token).")
    done = {(p["local_day"], p.get("slot")) for p in posts}
    missed = [(d, s) for d in days(start, end) for s in slots.slot_names()
              if (d, s) not in done and dt.date.fromisoformat(d) >= first_post_day(log, history)
              and slots.at(d, slots.settings()["slots"][s]["window"][1]) < now]
    if missed:
        made = {(h.get("local_day"), h.get("slot")) for h in history}
        unposted = [m for m in missed if m in made]
        text = f"**{len(missed)} slot{'s' if len(missed) > 1 else ''} had no post:** " + ", ".join(
            f"{fmt_day(d)} {s}" for d, s in missed[:10]) + (" and more" if len(missed) > 10 else "")
        if unposted:
            text += f" ({len(unposted)} of them were generated but never published"
            text += ", AUTO_PUBLISH is off)" if os.environ.get("AUTO_PUBLISH", "true").lower() != "true" else ")"
        problems.append(text)
    report = ROOT / "analytics" / "report.md"
    if report.exists():
        warn = next((l for l in report.read_text().splitlines() if l.startswith("> **Warning:**")), None)
        if warn:
            problems.append("**Learning loop:** " + warn.removeprefix("> **Warning:**").strip())
    hand = [p for p in posts if p.get("by_hand")]
    if hand:
        notes.append(f"{len(hand)} post{'s were' if len(hand) > 1 else ' was'} published outside the bot.")
    out += [f"- {p}" for p in problems] or ["- Nothing went wrong this week."]
    out += [f"- {n}" for n in notes]
    out.append("")
    out.append(f"_Made {now.astimezone(slots.tz()):%a %d %b %H:%M} India time by the weekly recap workflow._")
    return title, "\n".join(out) + "\n", week


def first_post_day(log, history):
    """Slots before the content calendar's first slot post do not count as missed."""
    days_ = [slots.where(p)[0] for p in log if p.get("slot")] + [h.get("local_day") for h in history if h.get("slot")]
    days_ = [d for d in days_ if d]
    return dt.date.fromisoformat(min(days_)) if days_ else dt.date.max


def leaning():
    """What the learning loop now favours in each slot, from all recent 3-day measurements."""
    s = learning.settings()
    posts = learning.scored(learning.measured_posts())
    if len(posts) < s["min_measured"]:
        return [f"The bot is still testing: {len(posts)} posts have their 3-day numbers so far and it starts "
                f"leaning on results at {s['min_measured']}. Until every type has "
                f"{s['min_per_option']} measured posts in a slot, it picks types by the starting mix."]
    lines = ["Where the bot now leans (from the last "
             f"{len(posts)} measured posts; it still tests other types {s['explore_rate']:.0%} of the time):", ""]
    def best(pool):  # like learning.choose_angle: only once every type has enough posts
        rows = [r for r in learning.ranking(pool, "angle") if r[2] >= s["min_per_option"]]
        return max(rows, key=lambda r: r[1]) if len(rows) == len(learning.ANGLES) else None

    overall = best(posts)
    for sl in slots.slot_names():
        pick, scope = best([p for p in posts if p.get("slot") == sl]), "in this slot"
        if not pick and overall:
            pick, scope = overall, "overall, until this slot has enough posts of every type"
        if pick:
            lines.append(f"- **{sl.capitalize()}:** {type_name(pick[0])} ({pick[1]:.2f}x a typical post "
                         f"over {pick[2]} posts, {scope})")
        else:
            lines.append(f"- **{sl.capitalize()}:** still testing with the starting mix (every type needs "
                         f"{s['min_per_option']} measured posts first)")
    return lines


def run():
    start, end = week_bounds()
    path = DIR / f"{end.isoformat()}.md"
    force = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")
    if path.exists() and not force:
        print(f"Recap for the week ending {end} already exists ({path.name}); set FORCE=true to redo it")
        return
    title, body, week = build(start, end)
    DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    state[end.isoformat()] = week
    STATE.write_text(json.dumps(dict(sorted(state.items())[-26:]), indent=2))
    print(body)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(body)
    if os.environ.get("NO_ISSUE", "").lower() not in ("1", "true", "yes"):
        try:
            url = open_issue(title, body)
            print("Issue:", url or "not opened (no GitHub token)")
        except RuntimeError as e:
            print("::warning::Could not open the recap issue:", e)
