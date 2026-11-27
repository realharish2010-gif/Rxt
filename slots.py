"""Content calendar: up to three posts a day, each in its own time slot (morning, midday,
evening, India time), at a posting time the learning loop picks for reach.

    python main.py due   -> decides whether a slot is due right now (run every 30 minutes by
                            .github/workflows/calendar.yml) and prints today's schedule

How a day works:
  - each slot has a window (e.g. evening 18:30 to 22:30 IST) and a few candidate posting times
    inside it (config.json "schedule"). Every day each slot gets one of its candidate times:
    the best-performing one once there is enough 3-day data, otherwise the least-tried one,
    so every time gets a fair test. The pick is seeded by date + slot, so every run that day
    agrees on it without having to save anything.
  - a slot is due from its planned time until its window closes. GitHub's scheduled runs are
    often late and sometimes skipped, so the workflow runs every 30 minutes inside the
    windows; a late run still posts, a window that passes without a run is skipped (an
    evening post never goes out at midnight).
  - each slot posts at most once a day, there are at most `max_per_day` posts a day, and posts
    are at least `min_gap_minutes` apart, even after a late run.
  - which of the 4 content types goes into the slot is decided in learning.plan (per-slot
    results first, then overall results, then the starting shares), with daily caps so a day
    never turns into three promotional posts.
"""
import collections
import datetime as dt
import json
import os
import pathlib
import random

import learning

ROOT = pathlib.Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text())
PUBLISHED = ROOT / "posts" / "published.json"
LATEST = ROOT / "posts" / "latest.json"

DEFAULTS = {
    "utc_offset": "+05:30",  # India Standard Time
    "max_per_day": 3,  # hard cap on posts per local day (Instagram allows far more via the API)
    "min_gap_minutes": 150,  # never two posts closer than this, even when a run was late
    "max_same_type_per_day": 2,  # no content type in more than 2 of a day's slots
    "max_promotional_per_day": 1,  # at most one promotional post a day
    # windows and candidate posting times (24h, local). The first time listed is tried first.
    # Starting points are the usual Instagram peaks in India: commute / breakfast scroll,
    # lunch break, and the evening peak (the strongest). The learning loop then tests the
    # other times and keeps whichever reaches the most people.
    "slots": {
        "morning": {"window": ["07:00", "10:30"], "times": ["08:30", "07:30", "09:30"]},
        "midday": {"window": ["12:00", "15:00"], "times": ["13:00", "12:30", "14:00"]},
        "evening": {"window": ["18:30", "22:30"], "times": ["20:00", "19:00", "21:00"]},
    },
}


def settings():
    return {**DEFAULTS, **CFG.get("schedule", {})}


def tz():
    off = settings()["utc_offset"]
    sign = -1 if off.startswith("-") else 1
    h, m = off.lstrip("+-").split(":")
    return dt.timezone(sign * dt.timedelta(hours=int(h), minutes=int(m)))


def local_now():
    override = os.environ.get("NOW")  # for testing: NOW=2026-11-26T20:05 (local time)
    if override:
        return dt.datetime.fromisoformat(override).replace(tzinfo=tz())
    return dt.datetime.now(tz())


def local_day():
    return local_now().date().isoformat()


def hm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def at(day, s):
    return dt.datetime.combine(dt.date.fromisoformat(day), hm(s), tz())


def slot_names():
    """Slots in the order they happen in the day."""
    slots = settings()["slots"]
    return sorted(slots, key=lambda n: hm(slots[n]["window"][0]))


def slot_of(when):
    """Which slot a moment (any timezone) falls in, or None."""
    t = when.astimezone(tz()).time()
    for name in slot_names():
        start, end = settings()["slots"][name]["window"]
        if hm(start) <= t < hm(end):
            return name
    return None


# ---------------------------------------------------------------- what has been posted

def published_log():
    return json.loads(PUBLISHED.read_text()) if PUBLISHED.exists() else []


def where(entry):
    """(local day, slot) of a published.json entry; older entries without them are worked out
    from their publish time."""
    when = dt.datetime.fromisoformat(entry["at"]) if entry.get("at") else None
    day = entry.get("local_day") or (when.astimezone(tz()).date().isoformat() if when else entry.get("day"))
    slot = entry.get("slot") or (slot_of(when) if when else None)
    return day, slot


def posted_on(day):
    return [p for p in published_log() if where(p)[0] == day]


def latest():
    return json.loads(LATEST.read_text()) if LATEST.exists() else {}


def types_today(history, day):
    """Content types (angles) already planned for today's earlier slots."""
    return [learning.OLD_ANGLES.get(h.get("angle"), h.get("angle"))
            for h in history if h.get("local_day") == day and h.get("angle")]


# ---------------------------------------------------------------- planning times

def plan_time(slot, day):
    """(time, reason) for this slot today. Exploits the best measured time in the slot,
    otherwise tests the least-tried one. Seeded so every run that day agrees."""
    cands = settings()["slots"][slot]["times"]
    ls = learning.settings()
    rng = random.Random(f"{day}:{slot}")
    posts = [p for p in learning.scored(learning.measured_posts()) if p.get("slot") == slot]
    ranked = [r for r in learning.ranking(posts, "post_time") if r[2] >= ls["min_per_option"] and r[0] in cands]
    if len(posts) >= ls["min_measured"] and ranked and ranked[0][1] >= 1.0 and rng.random() >= ls["explore_rate"]:
        return ranked[0][0], f"best {slot} time so far ({ranked[0][1]:.2f}x typical over {ranked[0][2]} posts)"
    # least tried, counting posts made before today so the pick holds all day
    counts = collections.Counter(p.get("post_time") for p in published_log()
                                 if where(p)[1] == slot and where(p)[0] < day)
    fewest = min(counts[c] for c in cands)
    pick = next(c for c in cands if counts[c] == fewest)
    return pick, f"testing {slot} times (least tried; {counts[pick]} posts so far at {pick})"


def today_schedule(day=None):
    day = day or local_day()
    done = {where(p)[1]: p for p in posted_on(day)}
    rows = []
    for name in slot_names():
        t, why = plan_time(name, day)
        end = settings()["slots"][name]["window"][1]
        rows.append({"slot": name, "time": t, "window_end": end, "why": why, "posted": done.get(name)})
    return rows


# ---------------------------------------------------------------- is a post due?

def due(now=None, wanted=None, manual=False, force=False, auto_publish=True):
    """(slot, post_time, reason). slot is None when nothing should run."""
    now = now or local_now()
    day = now.date().isoformat()
    s = settings()
    today = posted_on(day)
    done = {where(p)[1] for p in today}
    last = latest()
    waiting = (not auto_publish and last.get("local_day") == day and not last.get("posted")
               and last.get("slot"))  # made, waiting for a manual publish
    rows = today_schedule(day)

    if wanted and wanted not in ("", "next"):
        if wanted not in s["slots"]:
            return None, None, f"unknown slot {wanted!r}; choose from {', '.join(slot_names())}"
        if wanted in done and not force:
            return None, None, f"the {wanted} slot already posted today (tick 'force' to post again)"
        t = next(r["time"] for r in rows if r["slot"] == wanted)
        return wanted, t, f"manual run for the {wanted} slot"

    if len(today) >= s["max_per_day"] and not force:
        return None, None, f"already {len(today)} posts today (max {s['max_per_day']})"
    if today and not force:
        gap = now - max(dt.datetime.fromisoformat(p["at"]) for p in today if p.get("at")) \
            if any(p.get("at") for p in today) else dt.timedelta(days=1)
        if gap < dt.timedelta(minutes=s["min_gap_minutes"]):
            return None, None, (f"last post was {int(gap.total_seconds() // 60)} min ago; "
                                f"waiting for the {s['min_gap_minutes']} min gap")

    for r in rows:
        if r["slot"] in done or r["slot"] == waiting:
            continue
        start, end = at(day, r["time"]), at(day, r["window_end"])
        if start <= now < end:
            late = int((now - start).total_seconds() // 60)
            return r["slot"], r["time"], (f"{r['slot']} slot planned for {r['time']} ({r['why']})"
                                          + (f", running {late} min after" if late >= 5 else ""))
    if manual:  # a person pressed Run: post the next open slot early
        for r in rows:
            if r["slot"] not in done and r["slot"] != waiting and now < at(day, r["window_end"]):
                return r["slot"], r["time"], f"manual run: posting the {r['slot']} slot early"
        if force:
            return "extra", now.strftime("%H:%M"), "manual run with force: an extra post"
        return None, None, "every slot today is done or closed (tick 'force' to post anyway)"
    return None, None, "no slot is due right now"


def timed_run():
    """True for a run started by the clock: GitHub's own schedule, or the Cloudflare timer
    (timer/), which starts the calendar through workflow_dispatch with timer=true because
    GitHub often runs scheduled workflows late or skips them."""
    timer = os.environ.get("TIMER", "").strip().lower() in ("1", "true", "yes")
    return os.environ.get("GITHUB_EVENT_NAME", "") == "schedule" or timer


def cli():
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    force = os.environ.get("FORCE_PUBLISH", "").strip().lower() in ("1", "true", "yes")
    auto = os.environ.get("AUTO_PUBLISH", "true").strip().lower() == "true"
    slot, t, reason = due(wanted=os.environ.get("SLOT_INPUT", "").strip(), force=force,
                          manual=event == "workflow_dispatch" and not timed_run(), auto_publish=auto)
    now = local_now()
    lines = [f"Now: {now:%Y-%m-%d %H:%M} (UTC{settings()['utc_offset']})", "",
             "| Slot | Planned time | Why | Status |", "|---|---|---|---|"]
    for r in today_schedule(now.date().isoformat()):
        p = r["posted"]
        status = f"posted at {dt.datetime.fromisoformat(p['at']).astimezone(tz()):%H:%M}" if p and p.get("at") \
            else "posted" if p else f"open until {r['window_end']}"
        lines.append(f"| {r['slot']} | {r['time']} | {r['why']} | {status} |")
    lines += ["", ("Running: " if slot else "Nothing to do: ") + reason]
    print("\n".join(lines))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("## Content calendar\n\n" + "\n".join(lines) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"slot={slot or ''}\ntime={t or ''}\n")
