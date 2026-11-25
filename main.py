"""Daily Instagram carousel bot.
   python main.py generate   -> Gemini writes it, Playwright renders slides
   python main.py publish    -> posts the latest generated carousel to Instagram
"""
import datetime as dt
import html
import json
import os
import pathlib
import shutil
import sys
import time

import requests

ROOT = pathlib.Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text())
POSTS = ROOT / "posts"
LATEST = POSTS / "latest.json"
HISTORY = ROOT / "history.json"
PUBLISHED = POSTS / "published.json"  # log of every carousel sent to Instagram
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


def ask_gemini(past):
    from google import genai

    n = CFG["slides"]
    prompt = f"""You write Instagram carousels for {CFG['brand']}.
Business and audience: {CFG['niche']}
Voice: {CFG['voice']}
Rules: {CFG.get('rules', '')}
Topics already covered (do not repeat): {past[-40:]}

Pick ONE fresh, specific, genuinely useful topic. Write a carousel with exactly {n} slides:
- slide 1: a scroll-stopping hook as the title (max 9 words); body is a short teaser (max 15 words)
- slides 2 to {n - 1}: one idea each; title max 6 words, body max 25 words
- slide {n}: call to action: {CFG['cta']}; keep it short
Also write the Instagram caption. {CFG.get('caption_style', DEFAULT_CAPTION_STYLE)}
Return ONLY JSON: {{"topic": "...", "slides": [{{"title": "...", "body": "..."}}], "caption": "..."}}"""
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
            assert len(data["slides"]) == n, "wrong slide count"
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


def render(data, outdir):
    from playwright.sync_api import sync_playwright

    tpl = (ROOT / "template.html").read_text()
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
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1350})
        for i, s in enumerate(data["slides"], 1):
            kind = "hook" if i == 1 else "cta" if i == n else "content"
            progress = "".join(f'<i class="{"on" if j <= i else ""}"></i>' for j in range(1, n + 1))
            values = {
                "KIND": kind, "TITLE": html.escape(s["title"]), "BODY": html.escape(s["body"]),
                "LOGO": logo_html, "HALFTONE": halftone, "NUM": str(i), "TOTAL": str(n), "PROGRESS": progress,
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


def generate():
    # backup scheduled runs: skip if today's carousel was already made
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and LATEST.exists():
        if json.loads(LATEST.read_text()).get("day") == today():
            print("Today's carousel already exists, skipping")
            return
    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    data = None
    for attempt in range(6):
        try:
            data = ask_gemini([h["topic"] for h in history])
            break
        except Exception as e:  # bad JSON, rate limit, wrong slide count
            print(f"Gemini attempt {attempt + 1} failed: {e}")
            time.sleep(30 * (attempt + 1))
    if data is None:
        sys.exit("Could not generate a carousel")

    now = dt.datetime.now(dt.timezone.utc)
    day = now.date().isoformat()
    # a fresh folder per run (date + time) so a rerun never overwrites slides that
    # Instagram or the raw.githubusercontent.com cache may still be serving
    folder = now.strftime("%Y-%m-%d_%H%M%S")
    outdir = POSTS / folder
    outdir.mkdir(parents=True, exist_ok=True)
    names = render(data, outdir)

    repo = os.environ.get("GITHUB_REPOSITORY", "owner/repo")
    base = f"https://raw.githubusercontent.com/{repo}/main/posts/{folder}"
    LATEST.write_text(json.dumps({
        "day": day, "folder": folder, "topic": data["topic"],
        "caption": (data["caption"].strip() + "\n\n" + CFG.get("caption_footer", "")).strip(),
        "urls": [f"{base}/{n}" for n in names], "posted": False,
    }, indent=2))
    history.append({"day": day, "topic": data["topic"]})
    HISTORY.write_text(json.dumps(history, indent=2))

    # keep only the 14 newest post folders so the repo stays small
    folders = sorted(d for d in POSTS.iterdir() if d.is_dir())
    for old in folders[:-14]:
        shutil.rmtree(old)
    print("Generated:", data["topic"])


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
    done_today = [p for p in published_log() if p["day"] == day]
    if done_today and not forced():
        print(f"Already published today ({done_today[-1]['topic']}). "
              "Skipping. Run the publish workflow with 'force' ticked to post again.")
        return
    wait_for_urls(post["urls"])
    uid = os.environ["IG_USER_ID"]
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
    post["posted"] = True
    LATEST.write_text(json.dumps(post, indent=2))
    log = published_log()
    log.append({
        "day": day, "folder": post.get("folder", post["day"]), "topic": post["topic"],
        "media_id": media.get("id"), "forced": bool(done_today),
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    })
    PUBLISHED.write_text(json.dumps(log[-200:], indent=2))
    print("Published:", post["topic"])


if __name__ == "__main__":
    {"generate": generate, "publish": publish}[sys.argv[1]]()
