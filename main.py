"""Instagram carousel bot: a content calendar of up to 3 posts a day (see slots.py).
   python main.py due        -> is a calendar slot due now? prints today's schedule
   python main.py generate   -> Gemini writes it, Playwright renders slides
   python main.py publish    -> posts the latest generated carousel to Instagram
   python main.py preview [template ...] -> renders sample slides for each template into previews/
   python main.py learn      -> measures posts that are 3+ days old (see learning.py)
   python main.py trends     -> refreshes news, search trends and competitor patterns (see trends.py)
   python main.py weekly     -> writes last week's recap and opens it as a GitHub issue (see weekly.py)
   python main.py reel-preview [n] -> renders the sample Reels into previews/reels/ (see reels.py)
   python alerts.py failed|recovered|health -> failure alerts as GitHub issues (see alerts.py)

The calendar's Reel slot (config.json "reels") posts a short video instead of a carousel;
generate and publish handle both formats.
"""
import datetime as dt
import html
import json
import os
import pathlib
import random
import shutil
import sys
import time

import requests

import learning
import promo
import slots
import trends

ROOT = pathlib.Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text())
POSTS = ROOT / "posts"
LATEST = POSTS / "latest.json"
HISTORY = ROOT / "history.json"
PUBLISHED = POSTS / "published.json"  # log of every carousel sent to Instagram
TEMPLATES = ROOT / "templates"  # one HTML slide design per file; a different one is used each post
DEFAULT_CAPTION_STYLE = (
    "Structure: (1) one hook line that restates the carousel's main idea; "
    "(2) two or three short plain-language sentences adding useful context; "
    "(3) one friendly question that invites readers to comment; "
    "(4) the line 'Save this for later and share it with someone who needs it.'; "
    "(5) on the last line, 6 to 8 relevant hashtags mixing broad and specific ones. "
    "Keep it under 900 characters, use at most two emojis, no hype, and never promise "
    "results or give personal advice."
)
GRAPH = os.environ.get("IG_GRAPH", "https://graph.facebook.com/v27.0")  # check Meta docs for the current version


def ask_gemini(past, learnings="", voice=None):
    n = CFG["slides"]
    prompt = f"""You write Instagram carousels for {CFG['brand']}.
Business and audience: {CFG['niche']}
Voice: {voice or CFG['voice']}
Rules: {CFG.get('rules', '')}
Topics already covered (do not repeat): {past[-40:]}

{learnings}

Pick ONE fresh, specific, genuinely useful topic. Write a carousel with exactly {n} slides:
- slide 1: a scroll-stopping hook as the title (max 9 words); body is a short teaser (max 15 words)
- slides 2 to {n - 1}: one idea each; title max 6 words, body max 25 words
- slide {n}: call to action: {CFG['cta']}; keep it short
Also write the Instagram caption. {CFG.get('caption_style', DEFAULT_CAPTION_STYLE)}
Return ONLY JSON: {{"topic": "...", "slides": [{{"title": "...", "body": "..."}}], "caption": "..."}}"""

    def check(data):
        assert len(data["slides"]) == n, "wrong slide count"

    return gemini_json(prompt, check)


def gemini_json(prompt, check=None):
    """Ask Gemini for JSON, trying backup models if the preferred one fails."""
    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    last_error = None
    for model in candidate_models(client):
        try:
            r = client.models.generate_content(
                model=model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            data = json.loads(r.text)
            if check:
                check(data)
            print("Used model:", model)
            return data
        except Exception as e:  # overloaded, retired model, bad JSON
            print(f"Model {model} failed: {e}")
            last_error = e
    raise last_error


def candidate_models(client):
    """Preferred model first, then other available Gemini flash models as backups."""
    names = [os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")]
    try:
        for m in client.models.list():
            n = m.name.replace("models/", "")
            can_generate = "generateContent" in (getattr(m, "supported_actions", None) or ["generateContent"])
            if "flash" in n and can_generate and n not in names:
                if not any(x in n for x in ("image", "tts", "live", "audio", "embedding")):
                    names.append(n)
    except Exception as e:
        print("Could not list models:", e)
    return names[:4]


def available_templates():
    """Templates to rotate through: config.json "templates" if set, else every templates/*.html."""
    found = sorted(f.stem for f in TEMPLATES.glob("*.html"))
    wanted = CFG.get("templates") or found
    names = [t for t in wanted if t in found]
    if not names:
        sys.exit("No slide templates found in templates/")
    return names


def pick_template(history):
    """Shuffle through the templates: each one is used once (in random order) before any
    repeats, and never the same design two posts in a row. TEMPLATE=<name> forces one."""
    names = available_templates()
    forced_tpl = os.environ.get("TEMPLATE", "").strip()
    if forced_tpl:
        if forced_tpl not in names:
            sys.exit(f"Unknown template {forced_tpl!r}. Choose from: {', '.join(names)}")
        return forced_tpl
    used = [h["template"] for h in history if h.get("template") in names]
    cycle = set()  # templates already used in the current round
    for t in used:
        if t in cycle:  # round broke early (templates list changed or a forced pick)
            cycle = set()
        cycle.add(t)
        if len(cycle) == len(names):
            cycle = set()
    choices = [t for t in names if t not in cycle]
    if used and len(choices) > 1:
        choices = [t for t in choices if t != used[-1]] or choices
    return random.choice(choices)


def render(data, outdir, template="spray"):
    from playwright.sync_api import sync_playwright

    tpl = (TEMPLATES / f"{template}.html").read_text()
    c = CFG["colors"]
    n = len(data["slides"])
    names = []
    logo = ROOT / "logo.png"
    if logo.exists():
        import base64
        logo_html = '<img class="abs logo" src="data:image/png;base64,' + base64.b64encode(logo.read_bytes()).decode() + '">'
    else:
        logo_html = '<div class="abs logo-text">' + html.escape(CFG["brand"]) + '</div>'
    halftone = (ROOT / "assets" / "halftone.svg").read_text()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = browser.new_page(viewport={"width": 1080, "height": 1350})
        for i, s in enumerate(data["slides"], 1):
            kind = "hook" if i == 1 else "cta" if i == n else "content"
            progress = "".join(f'<i class="{"on" if j <= i else ""}"></i>' for j in range(1, n + 1))
            values = {
                "KIND": kind, "TITLE": html.escape(s["title"]), "BODY": html.escape(s["body"]),
                "LOGO": logo_html, "HALFTONE": halftone, "NUM": str(i), "TOTAL": str(n), "PROGRESS": progress,
                "NUM2": f"{i:02d}", "TOTAL2": f"{n:02d}",
                "BRAND": html.escape(CFG["brand"]), "HANDLE": html.escape(CFG["handle"]),
                "BG": c["bg"], "ACCENT": c["accent"], "TEXT": c["text"], "MUTED": c["muted"],
            }
            doc = tpl
            for k, v in values.items():
                doc = doc.replace("{{" + k + "}}", v)
            page.set_content(doc, wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            path = outdir / f"slide_{i}.jpg"
            page.screenshot(path=str(path), type="jpeg", quality=92)
            names.append(path.name)
        browser.close()
    return names


def today():
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def forced():
    return os.environ.get("FORCE_PUBLISH", "").strip().lower() in ("1", "true", "yes")


def published_log():
    return json.loads(PUBLISHED.read_text()) if PUBLISHED.exists() else []


def write_carousel(history, chosen, notes):
    """Gemini writes the carousel. A trend-tied post gets a second Gemini pass (a fact-check
    for news, a rules/taste/accuracy check for general trends) and is rewritten with the
    problems listed; if it still fails, the post becomes a plain educational one. A promotional
    post gets the same treatment, with a check that it claims nothing about RXT beyond the facts."""
    trend = chosen.get("trend")
    promo_angle = chosen.get("promo_angle")
    if promo_angle:
        chosen["content_type"] = "promotional"
    general = bool(trend) and trend.get("format") == "general trends"
    if trend and trend.get("format") in ("news content", "general trends"):
        chosen["content_type"] = trend["format"]
    feedback = ""
    for check_round in range(3 if trend or promo_angle else 1):
        learnings = learning.prompt_block(chosen)
        if promo_angle:
            learnings += "\n\n" + promo.prompt_block(promo_angle, CFG["slides"]) + feedback
        elif general:
            learnings += "\n\n" + trends.general_prompt_block(trend, CFG["slides"]) + feedback
        elif trend:
            learnings += "\n\n" + trends.prompt_block(trend, CFG["slides"]) + feedback
        if notes:
            learnings += ("\n\nOpening and format techniques working for similar finance accounts right now "
                          "(borrow the technique only, never their content or claims):\n"
                          + "\n".join(f"- {n}" for n in notes[:5]))
        voice = (promo.settings()["voice"] if promo_angle
                 else trends.general_settings().get("voice") if general else None)
        data = None
        for attempt in range(6):
            try:
                data = ask_gemini([h["topic"] for h in history], learnings, voice)
                break
            except Exception as e:  # bad JSON, rate limit, wrong slide count
                print(f"Gemini attempt {attempt + 1} failed: {e}")
                time.sleep(30 * (attempt + 1))
        if data is None:
            sys.exit("Could not generate a carousel")
        if not (trend or promo_angle):
            return data
        try:
            if promo_angle:
                ok, problems = promo.check(data, gemini_json)
            else:
                check = trends.vibe_check if general else trends.fact_check
                ok, problems = check(data, trend, gemini_json)
        except Exception as e:
            ok, problems = False, [f"review could not run: {e}"]
        if ok:
            print("Review passed")
            return data
        print("Review failed:", problems)
        feedback = ("\n\nA reviewer rejected the previous draft for these reasons; fix all of them:\n"
                    + "\n".join(f"- {x}" for x in problems))
    # still failing: drop the trend / promo angle and write an educational post in the same topic area
    kind = "Promotional" if promo_angle else trend.get("format", "Trend")
    print(f"::warning::{kind} post still failed review after two rewrites, "
          "writing an educational post instead")
    if trend:
        trends.mark_used(trend["id"])
    chosen.update(angle="educational", trend=None, promo_angle=None)
    if chosen["content_type"] in ("news content", "general trends", "promotional"):
        chosen["content_type"] = "simple explainer"
    return write_carousel(history, chosen, notes)


def current_slot():
    """(slot, planned time) this run is for, from the calendar step; empty outside the calendar."""
    return os.environ.get("SLOT", "").strip(), os.environ.get("POST_TIME", "").strip()


def already_made():
    """True when latest.json is this slot's post for today and has not gone out yet (a rerun
    after a failed publish, or a draft waiting for approval). Once it is posted, a forced run
    for the same slot (e.g. a second "extra" post) makes a new one instead of reusing it."""
    slot, _ = current_slot()
    if not (slot and LATEST.exists()):
        return False
    last = json.loads(LATEST.read_text())
    return last.get("slot") == slot and last.get("local_day") == slots.local_day() and not last.get("posted")


def generate():
    if already_made():
        print("This slot's carousel already exists, skipping (publish will retry it)")
        return
    if LATEST.exists():
        last = json.loads(LATEST.read_text())
        if not last.get("posted"):
            print(f"::warning::Replacing a carousel that was never posted: {last.get('topic')}")
    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    slot, post_time = current_slot()
    local = slots.local_day()
    # plan this post from what has worked so far (content type, hook, topic area, template)
    # and from today's safe news/search trends and what works for similar accounts
    trend_info = trends.current()
    if trend_info:
        trend_info["share"] = trends.settings()["share"]
        trend_info["general_share"] = trends.general_settings()["share"]
    chosen, why = learning.plan(history, available_templates(), lambda: pick_template(history), trend_info,
                                slot=slot or None, today=slots.types_today(history, local))
    forced_tpl = os.environ.get("TEMPLATE", "").strip()
    if forced_tpl:
        chosen["template"] = pick_template(history)  # validates and returns the forced one
    notes = (trend_info.get("patterns") or {}).get("notes") or []
    if make_reel(history, chosen, why, slot, post_time, local):
        return
    data = write_carousel(history, chosen, notes)

    now = dt.datetime.now(dt.timezone.utc)
    day = now.date().isoformat()
    # a fresh folder per run (date + time) so a rerun never overwrites slides that
    # Instagram or the raw.githubusercontent.com cache may still be serving
    folder = now.strftime("%Y-%m-%d_%H%M%S")
    outdir = POSTS / folder
    outdir.mkdir(parents=True, exist_ok=True)
    template = chosen["template"]
    print("Template:", template)
    tags = {d: chosen[d] for d in learning.DIMENSIONS}
    tags["angle"] = chosen["angle"]
    if chosen.get("promo_angle"):
        tags["promo_angle"] = chosen["promo_angle"]
    if chosen.get("trend"):
        t = chosen["trend"]
        tags["trend"] = {k: t.get(k) for k in ("id", "headline", "source", "link", "published", "format")}
        trends.mark_used(t["id"])
    tags["hook"] = data["slides"][0]["title"]
    tags["format"] = "carousel"
    tags["local_day"] = local
    if slot:
        tags.update(slot=slot, post_time=post_time)
    names = render(data, outdir, template)

    repo = os.environ.get("GITHUB_REPOSITORY", "owner/repo")
    base = f"https://raw.githubusercontent.com/{repo}/main/posts/{folder}"
    LATEST.write_text(json.dumps({
        "day": day, "folder": folder, "topic": data["topic"], "template": template, **tags,
        "plan_reasons": why,
        "caption": (data["caption"].strip() + "\n\n" + CFG.get("caption_footer", "")).strip(),
        "urls": [f"{base}/{n}" for n in names], "posted": False,
    }, indent=2))
    history.append({"day": day, "topic": data["topic"], "template": template, **tags})
    HISTORY.write_text(json.dumps(history, indent=2))

    # keep only the newest post folders (about a week at 3 a day) so the repo stays small
    folders = sorted(d for d in POSTS.iterdir() if d.is_dir())
    for old in folders[:-21]:
        shutil.rmtree(old)
    print("Generated:", data["topic"])


def make_reel(history, chosen, why, slot, post_time, local):
    """Makes this slot's post a Reel when the calendar says so (reels.choose_format). Returns
    False, and the slot posts a carousel instead, if Reels are off or the Reel did not pass
    its reviews and quality check."""
    import reels
    posts = learning.scored(learning.measured_posts())
    fmt, reason = reels.choose_format(slot, history, local, posts)
    print(f"Format: {fmt} ({reason})")
    if fmt != "reel":
        return False
    chosen, why = dict(chosen), {**why, "format": reason}
    remake = reels.pick_remake(history, posts)
    if remake:  # a proven carousel, retold as a Reel: same type, hook style and topic area
        chosen.update(trend=None, promo_angle=remake.get("promo_angle"),
                      angle=remake.get("angle") or "educational",
                      **{d: remake[d] for d in learning.DIMENSIONS if remake.get(d)})
        why["remake"] = f"remaking a carousel that scored {remake['rel']:.2f}x typical: {remake.get('topic')}"
        print("Remake:", why["remake"])
    if chosen.get("promo_angle"):
        chosen["content_type"] = "promotional"
    if chosen.get("trend") and chosen["trend"].get("format") in ("news content", "general trends"):
        chosen["content_type"] = chosen["trend"]["format"]
    now = dt.datetime.now(dt.timezone.utc)
    folder = now.strftime("%Y-%m-%d_%H%M%S")
    outdir = POSTS / folder
    try:
        made = reels.build(history, chosen, gemini_json, outdir, remake)
    except Exception as e:  # TTS, render or upload trouble must never cost the slot its post
        print(f"::warning::Reel failed ({e}); posting a carousel in this slot instead")
        made = None
    if not made:
        print("::warning::No Reel passed the checks; posting a carousel in this slot instead")
        shutil.rmtree(outdir, ignore_errors=True)
        return False
    data, info, quality = made
    for f in ("mix.wav", "reel.html"):
        (outdir / f).unlink(missing_ok=True)
    (outdir / "script.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tags = {d: chosen[d] for d in learning.DIMENSIONS}
    tags.update(angle=chosen["angle"], format="reel", hook=data["scenes"][0]["say"], local_day=local)
    if chosen.get("promo_angle"):
        tags["promo_angle"] = chosen["promo_angle"]
    if chosen.get("trend"):
        t = chosen["trend"]
        tags["trend"] = {k: t.get(k) for k in ("id", "headline", "source", "link", "published", "format")}
        trends.mark_used(t["id"])
    if remake:
        tags["remake_of"] = remake["media_id"]
    if slot:
        tags.update(slot=slot, post_time=post_time)
    LATEST.write_text(json.dumps({
        "day": now.date().isoformat(), "folder": folder, "topic": data["topic"], "template": "reel", **tags,
        "plan_reasons": why, "caption": reels.caption(data, info), "cover_ms": info["cover_ms"],
        "render": info, "quality": quality, "run_id": os.environ.get("GITHUB_RUN_ID"), "posted": False,
    }, indent=2, ensure_ascii=False))
    history.append({"day": now.date().isoformat(), "topic": data["topic"], "template": "reel", **tags})
    HISTORY.write_text(json.dumps(history, indent=2))
    print("Generated Reel:", data["topic"])
    return True


def api(path, **params):
    params["access_token"] = os.environ["IG_TOKEN"]
    r = requests.post(f"{GRAPH}/{path}", data=params, timeout=60)
    if not r.ok:
        sys.exit(f"Instagram error: {r.text}")
    return r.json()


def wait_for_urls(urls):
    for _ in range(30):
        if all(requests.head(u, timeout=20).status_code == 200 for u in urls):
            return
        time.sleep(10)
    sys.exit("Slide images are not publicly reachable yet (is the repo public and pushed?)")


def publish():
    post = json.loads(LATEST.read_text())
    if post["posted"]:
        print("Latest carousel was already posted")
        return
    day = today()
    local = slots.local_day()
    done_today = slots.posted_on(local)
    slot = post.get("slot")
    if not forced():
        why = None
        if slot and slot in {slots.where(p)[1] for p in done_today}:
            why = f"the {slot} slot already posted today"
        elif len(done_today) >= slots.settings()["max_per_day"]:
            why = f"already {len(done_today)} posts today"
        elif not slot and done_today:
            why = "a post already went out today and this carousel has no calendar slot"
        elif slot and post.get("local_day") != local and os.environ.get("GITHUB_EVENT_NAME") == "schedule":
            why = f"this carousel was made for {post.get('local_day')}'s {slot} slot"
        if why:
            print(f"Skipping: {why}. Run the publish workflow with 'force' ticked to post anyway.")
            return
    uid = os.environ["IG_USER_ID"]
    if post.get("format") == "reel":
        import reels
        check_publish_limit(uid)
        media = {"id": reels.publish(post, GRAPH, os.environ["IG_TOKEN"], uid)}
        log_published(post, day, local, media, done_today)
        return
    wait_for_urls(post["urls"])
    check_publish_limit(uid)
    kids = [api(f"{uid}/media", image_url=u, is_carousel_item="true")["id"] for u in post["urls"]]
    parent = api(f"{uid}/media", media_type="CAROUSEL", children=",".join(kids), caption=post["caption"])["id"]
    for _ in range(20):
        status = requests.get(
            f"{GRAPH}/{parent}",
            params={"fields": "status_code", "access_token": os.environ["IG_TOKEN"]},
            timeout=30,
        ).json().get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            sys.exit("Instagram could not process the carousel")
        time.sleep(5)
    media = api(f"{uid}/media_publish", creation_id=parent)
    log_published(post, day, local, media, done_today)


def log_published(post, day, local, media, done_today):
    post["posted"] = True
    LATEST.write_text(json.dumps(post, indent=2))
    log = published_log()
    log.append({
        "day": day, "folder": post.get("folder", post["day"]), "topic": post["topic"],
        "template": post.get("template"),
        **{k: post[k] for k in ("hook", *learning.TAGS) if post.get(k)},
        "local_day": local, "media_id": media.get("id"), "forced": forced() and bool(done_today),
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    })
    PUBLISHED.write_text(json.dumps(log[-200:], indent=2))
    print("Published:", post["topic"])


def check_publish_limit(uid):
    """Instagram caps posts published through the API per 24 hours (100 at the time of
    writing). We post far fewer, but stop cleanly if the account is ever at the cap."""
    try:
        r = requests.get(f"{GRAPH}/{uid}/content_publishing_limit",
                         params={"fields": "quota_usage,config", "access_token": os.environ["IG_TOKEN"]},
                         timeout=30).json()
        row = (r.get("data") or [{}])[0]
        used, total = row.get("quota_usage"), (row.get("config") or {}).get("quota_total")
        if used is not None and total and used >= total:
            sys.exit(f"Instagram publishing limit reached ({used}/{total} in 24 hours)")
        if used is not None:
            print(f"Instagram publishing quota: {used}/{total or '?'} used in the last 24 hours")
    except (requests.RequestException, ValueError, IndexError) as e:
        print("Could not check the publishing limit:", e)


SAMPLE = {
    "topic": "Sample: building an emergency fund",
    "slides": [
        {"title": "Your first emergency fund starts with one small step",
         "body": "A simple plan to build a cushion without changing your whole life."},
        {"title": "Pick a starter target", "body": "Aim for a small first goal, like one month of essential bills. A clear number is easier to work towards than a vague idea."},
        {"title": "Give it its own home", "body": "Keep the fund in a separate account from everyday spending, so it is easy to see and harder to dip into."},
        {"title": "Automate a small amount", "body": "Set up a transfer on payday, even a modest one. Small, regular amounts add up faster than waiting for a big lump sum."},
        {"title": "Define a real emergency", "body": "Write down what counts: job loss, urgent repairs, medical bills. A sale or a holiday does not."},
        {"title": "Refill after you use it", "body": "Using the fund is the point. Afterwards, restart the automatic transfer until it is back to your target."},
        {"title": "Small steps build real security", "body": "Follow RXT for better financial health"},
    ],
}


def preview(names=None):
    """Render the sample carousel with each template into previews/<template>/ (no Gemini needed)."""
    for t in names or available_templates():
        out = ROOT / "previews" / t
        out.mkdir(parents=True, exist_ok=True)
        render(SAMPLE, out, t)
        print("Rendered", t, "->", out)


def main(cmd):
    if cmd == "preview":
        preview(sys.argv[2:])
    elif cmd == "reel-preview":
        import reels
        reels.preview(int(sys.argv[2]) - 1 if len(sys.argv) > 2 else None)
    elif cmd == "learn":
        learning.sync(classify=gemini_json if os.environ.get("GEMINI_API_KEY") else None)
    elif cmd == "due":
        slots.cli()
    elif cmd == "weekly":
        import weekly
        weekly.run()
    elif cmd == "trends":
        if already_made():
            print("This slot's carousel already exists, skipping trends")
        else:
            trends.refresh(ask=gemini_json if os.environ.get("GEMINI_API_KEY") else None)
    else:
        {"generate": generate, "publish": publish}[cmd]()


ALERTED = ("due", "generate", "publish", "weekly")  # steps that stop a run; learn/trends never do


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except SystemExit as e:
        if e.code not in (None, 0) and sys.argv[1] in ALERTED:  # keep the message for the failure alert
            import alerts
            alerts.record_error(f"main.py {sys.argv[1]}: {e.code}")
        raise
    except BaseException as e:
        if sys.argv[1] not in ALERTED:
            raise
        import alerts
        import traceback
        alerts.record_error(f"main.py {sys.argv[1]}: " + "".join(traceback.format_exception_only(type(e), e))
                            + "".join(traceback.format_tb(e.__traceback__)[-2:]))
        raise
