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
GRAPH = os.environ.get("IG_GRAPH", "https://graph.instagram.com/v23.0")  # check Meta docs for the current version


def ask_gemini(past):
    from google import genai

    n = CFG["slides"]
    prompt = f"""You write Instagram carousels for {CFG['brand']}.
Business and audience: {CFG['niche']}
Voice: {CFG['voice']}
Topics already covered (do not repeat): {past[-40:]}

Pick ONE fresh, specific, genuinely useful topic. Write a carousel with exactly {n} slides:
- slide 1: a scroll-stopping hook as the title (max 9 words); body is a short teaser (max 15 words)
- slides 2 to {n - 1}: one idea each; title max 6 words, body max 25 words
- slide {n}: call to action: {CFG['cta']}; keep it short
Also write an Instagram caption (max 600 characters) ending with 5 relevant hashtags.
Return ONLY JSON: {{"topic": "...", "slides": [{{"title": "...", "body": "..."}}], "caption": "..."}}"""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    r = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),  # check AI Studio for the current free model name
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    data = json.loads(r.text)
    assert len(data["slides"]) == n, "wrong slide count"
    return data


def render(data, outdir):
    from playwright.sync_api import sync_playwright

    tpl = (ROOT / "template.html").read_text()
    c = CFG["colors"]
    n = len(data["slides"])
    names = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1350})
        for i, s in enumerate(data["slides"], 1):
            kind = "hook" if i == 1 else "cta" if i == n else "content"
            progress = "".join(f'<i class="{"on" if j <= i else ""}"></i>' for j in range(1, n + 1))
            values = {
                "KIND": kind, "TITLE": html.escape(s["title"]), "BODY": html.escape(s["body"]),
                "NUM": str(i), "TOTAL": str(n), "PROGRESS": progress,
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


def generate():
    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    data = None
    for attempt in range(3):
        try:
            data = ask_gemini([h["topic"] for h in history])
            break
        except Exception as e:  # bad JSON, rate limit, wrong slide count
            print(f"Gemini attempt {attempt + 1} failed: {e}")
            time.sleep(15)
    if data is None:
        sys.exit("Could not generate a carousel")

    day = dt.date.today().isoformat()
    outdir = POSTS / day
    outdir.mkdir(parents=True, exist_ok=True)
    names = render(data, outdir)

    repo = os.environ.get("GITHUB_REPOSITORY", "owner/repo")
    base = f"https://raw.githubusercontent.com/{repo}/main/posts/{day}"
    LATEST.write_text(json.dumps({
        "day": day, "topic": data["topic"], "caption": data["caption"],
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
    api(f"{uid}/media_publish", creation_id=parent)
    post["posted"] = True
    LATEST.write_text(json.dumps(post, indent=2))
    print("Published:", post["topic"])


if __name__ == "__main__":
    {"generate": generate, "publish": publish}[sys.argv[1]]()
