"""Learning loop: measure each Instagram post once it is a few days old, find which content
types, hooks, topic areas and templates reach the most people, and plan the next post so
it leans on what works while still testing new ideas.

    python main.py learn   -> fetch insights for posts that are old enough, update
                              analytics/insights.json and analytics/report.md

Compliance always wins: the learnings only steer format, hook and topic area. They are
added to the Gemini prompt *below* the brand rules, with an explicit instruction that the
rules come first.
"""
import collections
import datetime as dt
import json
import os
import pathlib
import random
import statistics

import requests

ROOT = pathlib.Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text())
DIR = ROOT / "analytics"
STORE = DIR / "insights.json"  # one record per measured post, keyed by Instagram media id
REPORT = DIR / "report.md"  # human-readable summary of what works
PUBLISHED = ROOT / "posts" / "published.json"
GRAPH = os.environ.get("IG_GRAPH", "https://graph.facebook.com/v27.0")
METRICS = ["reach", "saved", "shares", "likes", "comments"]

DEFAULTS = {
    "measure_after_days": 3,  # posts are measured once, at this age, so they compare fairly
    "explore_rate": 0.3,  # share of choices that test something other than the current best
    "min_measured": 5,  # measured posts needed before leaning on the results at all
    "min_per_option": 2,  # posts an option needs before it can be called best or worst
    "window": 30,  # only the newest N measured posts count, so old results fade out
    # score = reach plus bonus points for the actions Instagram rewards with more reach
    "weights": {"reach": 1, "shares": 5, "saved": 3, "comments": 2, "likes": 1},
    "content_types": [
        "step-by-step how-to", "myth vs fact", "common mistakes to avoid", "checklist",
        "simple explainer", "worked example with numbers", "quick rule of thumb",
    ],
    "hook_styles": [
        "direct question", "number or list", "bold clear statement", "common mistake",
        "myth-busting", "relatable everyday situation", "surprising but true fact",
    ],
    "topic_areas": [
        "budgeting", "saving", "emergency fund", "debt", "credit",
        "insurance basics", "money habits and mindset", "financial goals and planning",
    ],
}
DIMENSIONS = {"content_type": "content_types", "hook_style": "hook_styles", "topic_area": "topic_areas"}
ANGLES = ["evergreen", "trend-tied"]  # trend-tied = prompted by news or a search trend (trends.py)
TAGS = (*DIMENSIONS, "angle", "trend")  # what each post is logged with, besides topic/template/hook
LABELS = {"content_type": "Content type", "hook_style": "Hook style", "topic_area": "Topic area",
          "template": "Template", "angle": "Angle"}


def settings():
    return {**DEFAULTS, **CFG.get("learning", {})}


def now():
    return dt.datetime.now(dt.timezone.utc)


def load_store():
    return json.loads(STORE.read_text()) if STORE.exists() else {}


def measured_posts(store=None):
    """Measured posts, oldest first, limited to the newest `window`."""
    store = load_store() if store is None else store
    posts = [r for r in store.values() if r.get("metrics", {}).get("reach") is not None]
    posts.sort(key=lambda r: r["posted_at"])
    return posts[-settings()["window"]:]


# ---------------------------------------------------------------- fetching insights

class GraphError(Exception):
    def __init__(self, err):
        self.err = err if isinstance(err, dict) else {"message": str(err)}
        super().__init__(self.err.get("message", "Instagram API error"))

    @property
    def permission(self):
        code = self.err.get("code")
        msg = self.err.get("message", "").lower()
        return code in (10, 190) or (isinstance(code, int) and 200 <= code < 300) or "permission" in msg


def graph_get(path, **params):
    params["access_token"] = os.environ["IG_TOKEN"]
    r = requests.get(f"{GRAPH}/{path}", params=params, timeout=30)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if not r.ok or "error" in data:
        raise GraphError(data.get("error") or {"message": r.text[:300]})
    return data


def fetch_metrics(media_id):
    """reach, saved, shares, likes, comments for one post. Falls back to one metric at a
    time if Instagram rejects the combined request (metric names change between versions)."""
    try:
        rows = graph_get(f"{media_id}/insights", metric=",".join(METRICS))["data"]
    except GraphError as e:
        if e.permission:
            raise
        rows = []
        for m in METRICS:
            try:
                rows += graph_get(f"{media_id}/insights", metric=m)["data"]
            except GraphError as e2:
                if e2.permission:
                    raise
                print(f"Metric {m} unavailable for {media_id}: {e2}")
    out = {}
    for row in rows:
        if "values" in row:
            value = (row["values"] or [{}])[0].get("value")
        else:
            value = row.get("total_value", {}).get("value")
        out[row["name"]] = int(value or 0)
    return out if "reach" in out else None


def parse_time(s):
    return dt.datetime.strptime(s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")


def match_log(media, posted, log):
    """The published.json entry for this Instagram post: by media id, else by publish time."""
    for p in log:
        if p.get("media_id") == media["id"]:
            return p
    best = None
    for p in log:
        if p.get("media_id") or not p.get("at"):
            continue
        gap = abs((dt.datetime.fromisoformat(p["at"]) - posted).total_seconds())
        if gap < 15 * 60 and (best is None or gap < best[0]):
            best = (gap, p)
    return best[1] if best else {}


def sync(classify=None):
    """Measure every carousel that has reached `measure_after_days` and is not measured yet."""
    s = settings()
    if not (os.environ.get("IG_TOKEN") and os.environ.get("IG_USER_ID")):
        print("IG_TOKEN / IG_USER_ID not set, skipping insights")
        return
    store = load_store()
    log = json.loads(PUBLISHED.read_text()) if PUBLISHED.exists() else []
    status = "ok"
    try:
        media = graph_get(f"{os.environ['IG_USER_ID']}/media",
                          fields="id,caption,media_type,timestamp,permalink", limit=50)["data"]
        for m in media:
            if m.get("media_type") != "CAROUSEL_ALBUM":
                continue
            rec = store.get(m["id"], {})
            if rec.get("metrics"):
                continue  # measured once at the same age, never overwritten
            posted = parse_time(m["timestamp"])
            age = now() - posted
            if age < dt.timedelta(days=s["measure_after_days"]):
                continue
            meta = match_log(m, posted, log)
            metrics = fetch_metrics(m["id"])
            if metrics is None:
                print(f"No reach reported yet for {m['id']}, will retry next run")
                continue
            rec.update({
                "media_id": m["id"], "posted_at": posted.isoformat(), "permalink": m.get("permalink"),
                "caption": (m.get("caption") or "")[:600],
                **{k: meta[k] for k in ("topic", "template", "hook", "folder", *TAGS) if meta.get(k)},
                "metrics": metrics, "measured_at": now().isoformat(timespec="seconds"),
                "age_days": round(age.total_seconds() / 86400, 1),
            })
            store[m["id"]] = rec
            print(f"Measured {m['id']} ({rec.get('topic', 'untitled')}): {metrics}")
    except GraphError as e:
        if e.permission:
            status = ("Instagram refused the insights request. The IG_TOKEN needs the "
                      "instagram_manage_insights permission (and must not be expired). "
                      f"Instagram said: {e}")
        else:
            status = f"Instagram API error: {e}"
        print("::warning::" + status)

    if classify:
        tag_missing(store, classify)
    DIR.mkdir(exist_ok=True)
    STORE.write_text(json.dumps(store, indent=2, ensure_ascii=False))
    write_report(store, status)


def tag_missing(store, classify):
    """Posts made before tagging existed (or posted by hand) get tagged from their caption."""
    s = settings()
    todo = {k: r for k, r in store.items()
            if (any(not r.get(d) for d in DIMENSIONS) or not r.get("hook")) and r.get("tag_tries", 0) < 2}
    if not todo:
        return
    for r in todo.values():
        r["tag_tries"] = r.get("tag_tries", 0) + 1
    options = "\n".join(f"- {d}: one of {s[key]}" for d, key in DIMENSIONS.items())
    posts = {k: {"topic": r.get("topic"), "caption": r.get("caption")} for k, r in todo.items()}
    prompt = f"""Classify these Instagram carousel posts. For each post id return:
{options}
- topic: a short name for the specific topic
- hook: the post's opening hook line (the first line of the caption if unsure)
Use the exact option wording. Posts: {json.dumps(posts, ensure_ascii=False)}
Return ONLY JSON: {{"<post id>": {{"content_type": "...", "hook_style": "...", "topic_area": "...", "topic": "...", "hook": "..."}}}}"""
    try:
        tags = classify(prompt)
    except Exception as e:
        print("Could not tag old posts:", e)
        return
    for k, t in (tags or {}).items():
        if k not in store or not isinstance(t, dict):
            continue
        for d, key in DIMENSIONS.items():
            value = str(t.get(d, "")).strip().lower()
            match = next((o for o in s[key] if o.lower() == value), None)
            if match and not store[k].get(d):
                store[k][d] = match
        for f in ("topic", "hook"):
            if t.get(f) and not store[k].get(f):
                store[k][f] = t[f]


# ---------------------------------------------------------------- scoring

def score(rec):
    w = settings()["weights"]
    return sum(w.get(k, 0) * rec["metrics"].get(k, 0) for k in w)


def scored(posts):
    """Each post with `rel`: its score divided by the typical (median) score, so 1.5 means
    50% better than usual. Comparing to the recent median keeps follower growth from
    making newer posts look better than they are."""
    scores = [score(p) for p in posts]
    mid = statistics.median(scores) if scores else 0
    return [{**p, "score": sc, "rel": (sc / mid) if mid else 1.0} for p, sc in zip(posts, scores)]


def ranking(posts, dim):
    """[(option, avg rel, n posts, avg reach)] best first, for options with enough posts."""
    groups = collections.defaultdict(list)
    for p in posts:
        if p.get(dim):
            groups[p[dim]].append(p)
    rows = [(o, statistics.mean(x["rel"] for x in g), len(g), statistics.mean(x["metrics"]["reach"] for x in g))
            for o, g in groups.items()]
    return sorted(rows, key=lambda r: r[1], reverse=True)


# ---------------------------------------------------------------- planning the next post

def choose(dim, options, history, posts, avoid=None, explore_pick=None, hints=None):
    """Mostly the best-scoring option so far; with probability explore_rate (or while there
    is too little data) something less tested, so new ideas keep getting a fair trial.
    `hints` are options that are outperforming for similar accounts (trends.py): half of
    the tests go to them, the other half to whatever we have tried least."""
    s = settings()
    ranked = [r for r in ranking(posts, dim) if r[2] >= s["min_per_option"] and r[0] in options]
    if avoid:  # e.g. not the same template or topic area two posts in a row
        ranked = [r for r in ranked if r[0] != avoid]
    enough = len(posts) >= s["min_measured"]
    # only exploit an option that beats a typical post; otherwise trying something new is better
    if enough and ranked and ranked[0][1] >= 1.0 and random.random() >= s["explore_rate"]:
        return ranked[0][0], f"best so far ({ranked[0][1]:.2f}x typical over {ranked[0][2]} posts)"
    if explore_pick:
        return explore_pick(), "testing (rotation)"
    counts = collections.Counter(h.get(dim) for h in history)
    pool = [o for o in options if o != avoid] or options
    best = ranked[0][0] if enough and ranked else None
    hinted = [o for o in (hints or []) if o in pool and o != best]
    if hinted and random.random() < 0.5:
        return hinted[0], "testing (working for similar accounts)"
    fewest = min(counts[o] for o in pool)
    return random.choice([o for o in pool if counts[o] == fewest]), "testing (least tried)"


def choose_angle(posts, trend_share, have_trend):
    """Tie this post to a news/search trend, or keep it evergreen. Starts at the configured
    share; once both kinds have enough measured posts, the better one is used most days."""
    if not have_trend:
        return "evergreen", "no safe trend available today"
    s = settings()
    rows = {r[0]: r for r in ranking(posts, "angle") if r[2] >= s["min_per_option"]}
    if len(rows) == 2:
        best = max(rows.values(), key=lambda r: r[1])
        if random.random() >= s["explore_rate"]:
            return best[0], f"best so far ({best[1]:.2f}x typical over {best[2]} posts)"
        other = next(a for a in ANGLES if a != best[0])
        return other, "testing"
    if random.random() < trend_share:
        return "trend-tied", f"testing timely posts ({trend_share:.0%} of days until there is data)"
    return "evergreen", "evergreen most days until there is data on timely posts"


def plan(history, templates, explore_template, trend_info=None):
    """Content type, hook style, topic area, template and angle for the next post, with
    reasons. `trend_info` is trends.current(): today's safe trend picks and the patterns
    that are working for similar accounts."""
    s = settings()
    posts = scored(measured_posts())
    last = history[-1] if history else {}
    trend_info = trend_info or {}
    hints = trend_info.get("patterns", {}).get("hints", {})
    picks = trend_info.get("picks") or []
    out, why = {}, {}
    out["angle"], why["angle"] = choose_angle(posts, trend_info.get("share", 0.3), bool(picks))
    out["trend"] = None
    for dim, key in DIMENSIONS.items():
        out[dim], why[dim] = choose(dim, s[key], history, posts, hints=hints.get(dim),
                                    avoid=last.get(dim) if dim == "topic_area" else None)
    if out["angle"] == "trend-tied":
        # prefer a trend in the planned topic area, else the most relevant one
        pick = next((p for p in picks if p["topic_area"] == out["topic_area"]), picks[0])
        out["trend"], out["topic_area"] = pick, pick["topic_area"]
        why["topic_area"] = f"from the trend: {pick['headline'][:80]}"
    out["template"], why["template"] = choose("template", templates, history, posts,
                                              avoid=last.get("template"), explore_pick=explore_template)
    for k in why:
        print(f"Plan {k}: {out[k]} ({why[k]})")
    return out, why


def prompt_block(chosen):
    """Text added to the Gemini prompt: what has worked, what has not, and this post's plan."""
    s = settings()
    posts = scored(measured_posts())
    lines = []
    if len(posts) >= s["min_measured"]:
        ranked = sorted(posts, key=lambda p: p["rel"], reverse=True)
        k = min(3, len(ranked) // 2)

        def describe(p):
            m = p["metrics"]
            tags = ", ".join(p[d] for d in DIMENSIONS if p.get(d))
            return (f'- "{p.get("hook") or p.get("topic")}" ({tags}): reach {m.get("reach", 0)}, '
                    f'saves {m.get("saved", 0)}, shares {m.get("shares", 0)}')

        lines.append(f"WHAT OUR AUDIENCE RESPONDS TO (from our last {len(posts)} posts, measured "
                     f"{s['measure_after_days']} days after posting):")
        lines.append("Strongest posts:")
        lines += [describe(p) for p in ranked[:k]]
        lines.append("Weakest posts:")
        lines += [describe(p) for p in ranked[-k:]]
        for dim in DIMENSIONS:
            rows = [r for r in ranking(posts, dim) if r[2] >= s["min_per_option"]]
            if len(rows) >= 2:
                lines.append(f"{LABELS[dim]}s, best to worst: "
                             + "; ".join(f"{o} ({rel:.1f}x typical)" for o, rel, _, _ in rows))
        lines.append("Learn from the strongest posts' format and hook, and avoid what made the weakest "
                     "ones fall flat. Do not copy their topics.")
    else:
        lines.append("We are still collecting results on what our audience responds to, so this post "
                     "tests the plan below.")
    lines.append("")
    lines.append("PLAN FOR THIS POST (follow it):")
    lines.append(f"- Content type: {chosen['content_type']}")
    lines.append(f"- Hook style for slide 1: {chosen['hook_style']}")
    lines.append(f"- Topic area: {chosen['topic_area']} (pick a fresh, specific topic inside it)")
    lines.append("The Rules above always come first. Never make a post less accurate, more salesy, "
                 "fear-based, clickbait or closer to personal advice to chase reach. If the plan would "
                 "need that, keep the topic area and use a safer angle.")
    return "\n".join(lines)


# ---------------------------------------------------------------- report

def write_report(store, status="ok"):
    s = settings()
    posts = scored(measured_posts(store))
    out = ["# What works on Instagram", "",
           f"Updated {now():%Y-%m-%d %H:%M} UTC. Each post is measured once, "
           f"{s['measure_after_days']} days after it goes out. Score = "
           + " + ".join(f"{w}x {k}" for k, w in s["weights"].items())
           + "; \"vs typical\" compares it to the median of the last "
           f"{s['window']} measured posts.", ""]
    if status != "ok":
        out += [f"> **Warning:** {status}", ""]
    out.append(f"Measured posts: **{len(posts)}**"
               + ("" if len(posts) >= s["min_measured"] else
                  f" (the bot starts leaning on results at {s['min_measured']}; until then it rotates "
                  "through every option to collect data)"))
    out.append("")
    if posts:
        ranked = sorted(posts, key=lambda p: p["rel"], reverse=True)
        head = ["| Post | Type | Hook | Topic area | Template | Reach | Saves | Shares | Likes | Comments | vs typical |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]

        def row(p):
            m = p["metrics"]
            name = (p.get("hook") or p.get("topic") or p["media_id"]).replace("|", "/")
            link = f"[{name}]({p['permalink']})" if p.get("permalink") else name
            return (f"| {p['posted_at'][:10]} {link} | {p.get('content_type', '?')} | {p.get('hook_style', '?')} | "
                    f"{p.get('topic_area', '?')} | {p.get('template', '?')} | {m.get('reach', 0)} | {m.get('saved', 0)} | "
                    f"{m.get('shares', 0)} | {m.get('likes', 0)} | {m.get('comments', 0)} | {p['rel']:.2f}x |")

        k = min(5, max(1, len(ranked) // 2))
        out += ["## Best performers", "", *head, *[row(p) for p in ranked[:k]], ""]
        if len(ranked) > 1:
            out += ["## Worst performers", "", *head, *[row(p) for p in ranked[::-1][:k]], ""]
        for dim in [*DIMENSIONS, "template", "angle"]:
            rows = ranking(posts, dim)
            if rows:
                out += [f"## {LABELS[dim]}", "", "| Option | Posts | Avg reach | vs typical |", "|---|---|---|---|"]
                out += [f"| {o} | {n} | {reach:.0f} | {rel:.2f}x |" for o, rel, n, reach in rows]
                out.append("")
    DIR.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(out) + "\n")
