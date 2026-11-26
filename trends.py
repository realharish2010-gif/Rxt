"""Trend intelligence: what is happening in money news, what people are searching for, and
which post formats are working for similar Instagram accounts. The results steer (never
override) the daily plan made in learning.py.

    python main.py trends   -> refresh analytics/trends.json and analytics/trends.md

Sources (all free, official or public feeds, no scraping):
  - official announcements: RSS feeds from RBI, SEBI and the Press Information Bureau
    (config.json "trends" -> "official_feeds")
  - finance and business news: Google News search feeds ("news_feeds"); only stories from
    the outlets in "trusted_sources" can become a news post
  - what people are searching for: the Google Trends "trending now" RSS feed
  - competitor research: Instagram Graph API Business Discovery (public posts of the
    business/creator accounts listed in "competitors")
  - viral patterns: Instagram Graph API hashtag search (top posts for "hashtags"), plus the
    competitor posts above, scored against each account's own typical engagement
  - trending audio: Instagram has no API for it, and carousels published through the API
    cannot carry music. The report lists the Reels that are outperforming right now so a
    person can open them in the app and see which audio they use.

Compliance always wins. Every news or search trend goes through a safety screen (a word
blocklist and a Gemini review against the brand rules) and only becomes a post as a calm,
educational explainer of a general principle, using only facts stated in the source.
"""
import datetime as dt
import html
import json
import os
import pathlib
import re
import statistics
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

import learning

ROOT = pathlib.Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text())
DIR = ROOT / "analytics"
STORE = DIR / "trends.json"
REPORT = DIR / "trends.md"
GRAPH = learning.GRAPH
UA = {"User-Agent": "Mozilla/5.0 (compatible; rxt-trends/1.0; RSS reader)"}


def gnews(query):
    """Google News search as an RSS feed (India English edition)."""
    return ("https://news.google.com/rss/search?q=" + requests.utils.quote(query + " when:3d")
            + "&hl=en-IN&gl=IN&ceid=IN:en")


DEFAULTS = {
    "share": 0.3,  # share of posts tied to a news/search trend while there is no data on how they do
    "max_age_days": 3,  # ignore news older than this
    "stale_after_hours": 36,  # generate ignores trends.json older than this
    "news_feeds": [
        gnews("personal finance India"),
        gnews("RBI repo rate OR RBI policy"),
        gnews("income tax India OR GST OR budget"),
        gnews("inflation India OR EMI OR home loan rates"),
        gnews("credit score OR CIBIL OR credit card rules"),
        gnews("UPI OR digital payments fraud"),
    ],
    "official_feeds": [
        "https://www.rbi.org.in/pressreleases_rss.xml",
        "https://www.sebi.gov.in/sebirss.xml",
        "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=3",
    ],
    # outlets whose reporting can be turned into a news content post (matched on the
    # Google News source name); other outlets' stories can only prompt an evergreen lesson
    "trusted_sources": [
        "Reserve Bank of India", "RBI", "SEBI", "PIB", "Press Information Bureau", "Mint", "Livemint",
        "The Economic Times", "Economic Times", "Business Standard", "The Hindu BusinessLine",
        "BusinessLine", "Moneycontrol", "Reuters", "Press Trust of India", "PTI", "The Hindu",
        "Financial Express", "The Financial Express", "Hindustan Times", "The Indian Express",
        "NDTV Profit", "CNBC TV18", "Business Today",
    ],
    "search_trend_feeds": ["https://trends.google.com/trending/rss?geo=IN"],
    "hashtags": ["personalfinance", "moneytips", "financialliteracy", "budgeting", "savingmoney"],
    "competitors": [],  # Instagram business/creator usernames (without @) to learn from
    "outperform_at": 1.5,  # a post counts as outperforming at this multiple of its account's median
    # headlines with these words are never used (case-insensitive, whole words or phrases)
    "blocked_words": [
        "stock tip", "multibagger", "target price", "buy call", "sell call", "penny stock",
        "intraday", "f&o", "options trading", "crypto", "bitcoin", "memecoin", "ipo gmp",
        "guaranteed return", "double your money", "get rich", "lottery", "betting", "gambling",
        "suicide", "death", "dies", "killed", "murder", "arrested", "scam accused",
        "election", "bjp", "congress party", "modi", "rahul gandhi",
    ],
}


def settings():
    return {**DEFAULTS, **CFG.get("trends", {})}


def now():
    return dt.datetime.now(dt.timezone.utc)


def load():
    return json.loads(STORE.read_text()) if STORE.exists() else {}


# ---------------------------------------------------------------- feeds

def _text(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def clean(s, limit=300):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def read_feed(url):
    """Items from an RSS 2.0 or Atom feed: title, link, source, published, summary, extra."""
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    atom = "{http://www.w3.org/2005/Atom}"
    ht = "{https://trends.google.com/trending/rss}"
    items = root.findall(".//item") or root.findall(f".//{atom}entry")
    out = []
    for it in items:
        link = _text(it, "link")
        if not link:
            le = it.find(f"{atom}link")
            link = le.get("href", "") if le is not None else ""
        source = it.find("source")
        news = [clean(_text(n, f"{ht}news_item_title"), 160) for n in it.findall(f"{ht}news_item")]
        out.append({
            "title": clean(_text(it, "title", f"{atom}title"), 200),
            "link": link,
            "source": clean(source.text if source is not None else "", 60),
            "published": parse_date(_text(it, "pubDate", f"{atom}updated", f"{atom}published")),
            "summary": clean(_text(it, "description", f"{atom}summary"), 300),
            "traffic": _text(it, f"{ht}approx_traffic"),
            "related": [n for n in news if n][:3],
        })
    return out


def blocked(text, words):
    t = text.lower()
    return next((w for w in words if re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", t)), None)


def collect_candidates(s, errors):
    """Fresh, de-duplicated headlines and search trends that pass the word blocklist."""
    cutoff = now() - dt.timedelta(days=s["max_age_days"])
    seen, out, dropped = set(), [], 0
    trusted = {t.lower() for t in s["trusted_sources"]}
    for kind, feeds in (("official", s["official_feeds"]), ("news", s["news_feeds"]),
                        ("search", s["search_trend_feeds"])):
        for url in feeds:
            try:
                items = read_feed(url)
            except Exception as e:  # a dead feed never stops the others
                errors.append(f"Feed failed ({url[:80]}): {e}")
                continue
            for it in items[:25]:
                if not it["title"]:
                    continue
                if it["published"] and it["published"] < cutoff:
                    continue
                key = re.sub(r"[^a-z0-9]", "", it["title"].lower().rsplit(" - ", 1)[0])[:60]
                if key in seen:
                    continue
                seen.add(key)
                if blocked(" ".join([it["title"], it["summary"], *it["related"]]), s["blocked_words"]):
                    dropped += 1
                    continue
                if kind == "official" and not it["source"]:
                    it["source"] = ("RBI" if "rbi.org" in url else "SEBI" if "sebi.gov" in url
                                    else "PIB" if "pib.gov" in url else "official release")
                it["kind"] = kind
                it["trusted"] = kind == "official" or (kind == "news" and it["source"].lower() in trusted)
                it["published"] = it["published"].isoformat() if it["published"] else None
                out.append(it)
    print(f"Trend candidates: {len(out)} kept, {dropped} blocked by word list")
    return out


# ---------------------------------------------------------------- safety screen + angles

def brief(candidates, ask, used_ids):
    """Gemini reads the candidates and keeps only the ones RXT can teach from safely."""
    if not candidates:
        return []
    items = [{"id": i, "kind": c["kind"], "trusted": c["trusted"], "title": c["title"], "source": c["source"],
              "published": c["published"], "summary": c["summary"], "traffic": c["traffic"],
              "related_headlines": c["related"]} for i, c in enumerate(candidates[:60])]
    areas = learning.settings()["topic_areas"]
    prompt = f"""You screen news and search trends for {CFG['brand']}, an Instagram account.
Business and audience: {CFG['niche']}
Rules: {CFG.get('rules', '')}

Below are today's official announcements ("official": RBI, SEBI, government press releases),
headlines ("news") and Google search trends ("search"). Pick at most 5 that
everyday people would genuinely benefit from understanding, where {CFG['brand']} can teach a
GENERAL, evergreen money principle prompted by the story (for example: an interest rate
change -> how loan EMIs and savings rates respond; a new tax deadline -> how to prepare
calmly; a payments fraud wave -> how to spot and avoid that fraud).

Reject anything that would need: predicting markets or prices, naming or judging specific
stocks, funds, lenders, apps or products, crypto or trading, politics, tragedy, celebrity
gossip, fear or hype, or facts that are not stated in the item itself. Reject rumours,
opinion pieces and unconfirmed proposals unless the angle is purely "what this kind of
change means in general". Most items should be rejected; returning an empty list is fine.

Prefer official announcements and items marked trusted.

For each pick give:
- format: "news content" if the post should report the news itself (what happened, then
  what it means for everyday people). Only allowed for items with trusted = true that state
  a confirmed decision, rule change, deadline or official data release (not a rumour,
  forecast, opinion or market move). Otherwise "lesson" (the news only prompts a general lesson).
- id: the item's id
- topic_area: one of {areas}
- angle: the educational carousel idea in one sentence, calm and practical
- facts: up to 3 short facts copied from the item's title/summary that the post may state
  (never add numbers, dates or details that are not in the item)
- avoid: one sentence on what the post must not say (hype, predictions, advice, etc.)
- relevance: 1 to 5, how useful this is for the audience this week
- evergreen_ok: true if the angle still makes sense even if a detail of the story changes

Items: {json.dumps(items, ensure_ascii=False)}
Return ONLY JSON: {{"picks": [{{"id": 0, "topic_area": "...", "angle": "...", "facts": ["..."],
"avoid": "...", "relevance": 3, "evergreen_ok": true}}]}}"""
    data = ask(prompt, lambda d: isinstance(d.get("picks"), list))
    s = settings()
    picks = []
    for p in data["picks"]:
        try:
            c = candidates[int(p["id"])]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        if p.get("topic_area") not in areas or not p.get("angle"):
            continue
        text = " ".join([p["angle"], *map(str, p.get("facts", []))])
        if blocked(text, s["blocked_words"]):  # second check on what Gemini wrote
            continue
        tid = re.sub(r"[^a-z0-9]+", "-", c["title"].lower())[:60].strip("-")
        if tid in used_ids:
            continue
        picks.append({
            "id": tid, "kind": c["kind"], "headline": c["title"], "source": c["source"] or ("Google Trends" if c["kind"] == "search" else "news"),
            "link": c["link"], "published": c["published"], "topic_area": p["topic_area"],
            "angle": p["angle"].strip(), "facts": [str(f)[:200] for f in p.get("facts", [])][:3],
            "avoid": str(p.get("avoid", ""))[:200], "relevance": int(p.get("relevance") or 1),
            "evergreen_ok": bool(p.get("evergreen_ok")),
            "format": "news content" if (p.get("format") == "news content" and c["trusted"]
                                           and p.get("facts")) else "lesson",
        })
    picks.sort(key=lambda p: (p["kind"] == "official", p["relevance"], p["evergreen_ok"]), reverse=True)
    return picks


# ---------------------------------------------------------------- Instagram research

def ig_get(path, **params):
    return learning.graph_get(path, **params)


MEDIA_FIELDS = "id,caption,media_type,media_product_type,like_count,comments_count,timestamp,permalink"


def competitor_posts(usernames, errors):
    uid = os.environ["IG_USER_ID"]
    out = []
    for name in usernames:
        name = name.lstrip("@").strip()
        try:
            d = ig_get(uid, fields=f"business_discovery.username({name})"
                                   f"{{username,followers_count,media.limit(30){{{MEDIA_FIELDS}}}}}")
        except learning.GraphError as e:
            errors.append(f"Competitor @{name}: {e}"
                          + (" (needs a business/creator account and a token with instagram_basic "
                             "+ pages_read_engagement)" if e.permission else ""))
            continue
        bd = d.get("business_discovery", {})
        for m in bd.get("media", {}).get("data", []):
            m.update(account=bd.get("username", name), followers=bd.get("followers_count"), via="competitor")
            out.append(m)
    return out


def hashtag_posts(tags, cache, errors):
    """Top posts for each hashtag. Instagram allows 30 different hashtags per 7 days, so the
    hashtag ids are cached and the list should stay short."""
    uid = os.environ["IG_USER_ID"]
    out = []
    for tag in tags:
        tag = tag.lstrip("#").strip().lower()
        try:
            if tag not in cache:
                cache[tag] = ig_get("ig_hashtag_search", user_id=uid, q=tag)["data"][0]["id"]
            rows = ig_get(f"{cache[tag]}/top_media", user_id=uid, fields=MEDIA_FIELDS, limit=30)["data"]
        except (learning.GraphError, KeyError, IndexError) as e:
            perm = isinstance(e, learning.GraphError) and e.permission
            errors.append(f"Hashtag #{tag}: {e}" + (" (hashtag search needs the Instagram Public "
                                                     "Content Access feature on the Meta app)" if perm else ""))
            continue
        for m in rows:
            m.update(account=None, followers=None, via=f"#{tag}")
            out.append(m)
    return out


def engagement(m):
    if m.get("like_count") is None and m.get("comments_count") is None:
        return None
    return (m.get("like_count") or 0) + 3 * (m.get("comments_count") or 0)


def outperformers(posts, s):
    """Posts doing well *for their own account* (or, for hashtags, against that hashtag's
    other top posts), so a big account's ordinary post does not look viral."""
    groups = {}
    for m in posts:
        if engagement(m) is not None:
            groups.setdefault(m["account"] or m["via"], []).append(m)
    hits = []
    for group in groups.values():
        if len(group) < 4:
            continue
        med = statistics.median(engagement(m) for m in group) or 1
        for m in group:
            m["rel"] = round(engagement(m) / med, 2)
            if m["rel"] >= s["outperform_at"]:
                hits.append(m)
    hits.sort(key=lambda m: m["rel"], reverse=True)
    return hits


def classify_patterns(hits, ask):
    """Tag the outperforming posts with the same labels the learning loop uses."""
    if not hits:
        return {}
    ls = learning.settings()
    posts = {m["id"]: (m.get("caption") or "")[:500] for m in hits[:25] if m.get("caption")}
    if not posts:
        return {}
    prompt = f"""Classify these Instagram posts from finance education accounts by their caption.
For each post id return:
- content_type: one of {ls['content_types']}
- hook_style: one of {ls['hook_styles']}
- topic_area: one of {ls['topic_areas']}, or "other"
- pattern: max 12 words on what makes the opening or format work (no topic details)
Use the exact option wording. Posts: {json.dumps(posts, ensure_ascii=False)}
Return ONLY JSON: {{"<post id>": {{"content_type": "...", "hook_style": "...", "topic_area": "...", "pattern": "..."}}}}"""
    return ask(prompt, lambda d: isinstance(d, dict))


def summarise_patterns(hits, tags):
    ls = learning.settings()
    tally = {}
    for dim, key in learning.DIMENSIONS.items():
        counts = {}
        for m in hits:
            v = tags.get(m["id"], {}).get(dim)
            if v in ls[key]:
                counts[v] = counts.get(v, 0) + 1
        tally[dim] = sorted(counts, key=counts.get, reverse=True)
        tally[dim + "_counts"] = counts
    formats = {}
    for m in hits:
        f = m.get("media_product_type") == "REELS" and "reel" or (
            "carousel" if m.get("media_type") == "CAROUSEL_ALBUM" else "single image/video")
        formats[f] = formats.get(f, 0) + 1
    hours = [(dt.datetime.strptime(m["timestamp"].replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
              + dt.timedelta(hours=5, minutes=30)).hour for m in hits if m.get("timestamp")]
    return {
        "hints": {d: tally[d][:3] for d in learning.DIMENSIONS},
        "counts": {d: tally[d + "_counts"] for d in learning.DIMENSIONS},
        "formats": formats,
        "best_hours_ist": sorted(set(h for h in hours if hours.count(h) > 1)),
        "notes": list(dict.fromkeys(tags[m["id"]]["pattern"] for m in hits
                                    if tags.get(m["id"], {}).get("pattern")))[:8],
        "examples": [{"account": m["account"] or m["via"], "permalink": m.get("permalink"),
                      "rel": m["rel"], "format": m.get("media_product_type") or m.get("media_type"),
                      "first_line": (m.get("caption") or "").strip().split("\n")[0][:120],
                      **{d: tags.get(m["id"], {}).get(d) for d in learning.DIMENSIONS}}
                     for m in hits[:12]],
        "reels": [{"account": m["account"] or m["via"], "permalink": m.get("permalink"), "rel": m["rel"]}
                  for m in hits if m.get("media_product_type") == "REELS"][:8],
        "measured": len(hits),
    }


# ---------------------------------------------------------------- main entry

def refresh(ask=None):
    s = settings()
    store = load()
    errors = []
    used = set(store.get("used", []))

    candidates = collect_candidates(s, errors)
    picks = []
    if ask:
        try:
            picks = brief(candidates, ask, used)
        except Exception as e:
            errors.append(f"Gemini trend screen failed: {e}")
    else:
        errors.append("GEMINI_API_KEY not set, so no trend passed the safety screen")

    patterns = store.get("patterns", {})
    if os.environ.get("IG_TOKEN") and os.environ.get("IG_USER_ID"):
        posts = competitor_posts(s["competitors"], errors) if s["competitors"] else []
        cache = store.get("hashtag_ids", {})
        posts += hashtag_posts(s["hashtags"], cache, errors)
        store["hashtag_ids"] = cache
        hits = outperformers(posts, s)
        if hits:
            tags = {}
            if ask:
                try:
                    tags = classify_patterns(hits, ask)
                except Exception as e:
                    errors.append(f"Gemini pattern tagging failed: {e}")
            patterns = summarise_patterns(hits, tags)
            patterns["updated"] = now().isoformat(timespec="seconds")
            patterns["looked_at"] = len(posts)
        elif posts:
            errors.append(f"Looked at {len(posts)} public posts but none had enough engagement data")
    else:
        errors.append("IG_TOKEN / IG_USER_ID not set, so no competitor or hashtag research")

    store.update({
        "updated": now().isoformat(timespec="seconds"),
        "candidates": len(candidates),
        "picks": picks,
        "patterns": patterns,
        "errors": errors,
        "used": sorted(used)[-200:],
    })
    DIR.mkdir(exist_ok=True)
    STORE.write_text(json.dumps(store, indent=2, ensure_ascii=False))
    write_report(store)
    for e in errors:
        print("::warning::" + e)
    print(f"Trends: {len(picks)} usable topics, patterns from {patterns.get('measured', 0)} outperforming posts")


def current():
    """trends.json if it is fresh enough to use today, else {}."""
    store = load()
    if not store.get("updated"):
        return {}
    age = now() - dt.datetime.fromisoformat(store["updated"])
    return store if age < dt.timedelta(hours=settings()["stale_after_hours"]) else {}


def mark_used(trend_id):
    store = load()
    if store:
        store["used"] = sorted(set(store.get("used", [])) | {trend_id})[-200:]
        store["picks"] = [p for p in store.get("picks", []) if p["id"] != trend_id]
        STORE.write_text(json.dumps(store, indent=2, ensure_ascii=False))


def prompt_block(trend, slides):
    """Gemini instructions for a post tied to a news story or search trend."""
    facts = "\n".join(f"  - {f}" for f in trend["facts"]) or "  - (none: do not state any details of the story)"
    when = (trend.get("published") or "")[:10]
    head = f"""TIMELY ANGLE FOR THIS POST:
In the news / search trends: "{trend['headline']}" ({trend['source']}{', ' + when if when else ''})
Teach this: {trend['angle']}
Facts you may state (only these; add no other numbers, dates, names or details about the story):
{facts}
Must avoid: {trend['avoid']}"""
    if trend.get("format") == "news content":
        return head + f"""
FORMAT: NEWS CONTENT. This carousel reports the news, then explains it.
- slide 1: a calm, clear hook naming what happened (no "breaking", no urgency, no hype)
- slide 2: "What happened": state the facts above plainly and name the source ({trend['source']})
- slides 3 to {slides - 1}: what it means for everyday people and one sensible, general action
  step; no predictions about markets, prices or what happens next
- slide {slides}: the call to action as usual
Caption: first line says what happened in plain words; include a line
"Source: {trend['source']}{', ' + when if when else ''}". If the facts are not enough to explain
the story accurately, write an evergreen post in the same topic area instead."""
    return head + f"""
Make the carousel useful even to someone who has not seen the news: explain the general
principle and one practical step. Mention the news calmly in at most one slide, without hype,
predictions or urgency. In the caption, add a line "Context: {trend['source']}" so readers can
look it up. If following this angle would break the Rules, ignore it and write an evergreen post
in the same topic area instead."""


def fact_check(data, trend, ask):
    """Second Gemini pass on a news-tied carousel: every claim about the story must come from
    the source facts, and nothing may break the brand rules. Returns (ok, problems)."""
    prompt = f"""You are a strict fact-checker for {CFG['brand']}'s Instagram.
Rules: {CFG.get('rules', '')}
The post is about this story: "{trend['headline']}" ({trend['source']})
The ONLY confirmed facts about the story are: {json.dumps(trend['facts'], ensure_ascii=False)}

Check the carousel below. Flag a problem if it:
- states any number, date, name, amount or detail about the story that is not in the facts
- predicts markets, prices, rates or what will happen next
- recommends a specific product, stock, fund, lender or app, or gives personalised advice
- uses hype, fear or urgency
General educational principles and clearly hypothetical examples ("say your EMI is ...") are fine.

Carousel: {json.dumps(data, ensure_ascii=False)}
Return ONLY JSON: {{"ok": true, "problems": ["..."]}}"""
    r = ask(prompt, lambda d: isinstance(d.get("ok"), bool))
    return r["ok"], [str(x) for x in r.get("problems", [])][:5]


def write_report(store):
    s = settings()
    p = store.get("patterns", {})
    out = ["# Trend intelligence", "",
           f"Updated {store['updated'][:16].replace('T', ' ')} UTC. {store.get('candidates', 0)} fresh "
           "headlines and search trends were read; the ones below passed the safety screen "
           "(word blocklist + a Gemini check against the brand rules).", ""]
    if store.get("errors"):
        out += ["> **Notes:**", *[f"> - {e}" for e in store["errors"]], ""]
    out += ["## Usable timely topics", ""]
    if store.get("picks"):
        out += ["| Relevance | Post type | Topic area | Angle | Story |", "|---|---|---|---|---|"]
        for t in store["picks"]:
            story = f"[{t['headline'].replace('|', '/')}]({t['link']})" if t.get("link") else t["headline"]
            out.append(f"| {t['relevance']}/5 | {t.get('format', 'lesson')} | {t['topic_area']} | {t['angle'].replace('|', '/')} | {story} |")
    else:
        out.append("None today. The next post will be evergreen.")
    out += ["", "## What is working for similar accounts", ""]
    if p.get("measured"):
        out.append(f"From {p.get('looked_at', '?')} public posts ({len(s['competitors'])} competitor accounts, "
                   f"{len(s['hashtags'])} hashtags), {p['measured']} are outperforming "
                   f"(at least {s['outperform_at']}x their account's or hashtag's median engagement).")
        out.append("")
        for d in learning.DIMENSIONS:
            c = p.get("counts", {}).get(d, {})
            if c:
                out.append(f"- **{learning.LABELS[d]}:** " + ", ".join(f"{k} ({v})" for k, v in
                                                                        sorted(c.items(), key=lambda x: -x[1])))
        if p.get("formats"):
            out.append("- **Format:** " + ", ".join(f"{k} ({v})" for k, v in p["formats"].items()))
        if p.get("best_hours_ist"):
            out.append("- **Hours (IST) that show up more than once:** " + ", ".join(map(str, p["best_hours_ist"])))
        if p.get("notes"):
            out += ["", "Patterns:", *[f"- {n}" for n in p["notes"]]]
        if p.get("examples"):
            out += ["", "| Post | Account | vs typical | Opening line |", "|---|---|---|---|"]
            for e in p["examples"]:
                out.append(f"| [{e.get('format') or 'post'}]({e.get('permalink')}) | {e['account']} | "
                           f"{e['rel']}x | {e['first_line'].replace('|', '/')} |")
        out += ["", "The bot uses these as hints when it tests something new (see analytics/report.md); "
                "our own results always outrank them."]
    else:
        out.append("No data yet. Add competitor usernames under config.json \"trends\" -> \"competitors\" "
                   "and make sure IG_TOKEN can use Business Discovery / hashtag search.")
    out += ["", "## Trending audio", "",
            "Instagram has no API for trending audio, and carousels posted through the API cannot carry "
            "music, so the bot cannot pick or add audio. To use it by hand: in the Instagram app, tracks with "
            "a small upward arrow in the Reels audio picker are trending. The Reels below are outperforming "
            "right now; open them to see which audio they use.", ""]
    reels = p.get("reels") or []
    out += [f"- [{r['account']}]({r['permalink']}) ({r['rel']}x typical)" for r in reels] or ["- None found yet."]
    DIR.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(out) + "\n")
