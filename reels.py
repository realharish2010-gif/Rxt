"""Reels: short vertical videos made automatically with free tools only.

    python main.py reel-preview [n]   -> renders the built-in sample Reels into previews/reels/
                                         (no Gemini or Instagram needed)

How a Reel is made (in the calendar's Reel slot, see config.json "reels"):
  1. topic: half the time a proven carousel is remade as a Reel (its 3-day score beat a typical
     post), otherwise the learning loop plans a fresh one exactly like a carousel (content type,
     hook style, topic area, and the 4 post types with their checks)
  2. script: Gemini writes a 20-35 second shot plan: a hook that lands in the first 2 seconds,
     one idea per 2-4 second scene, each scene a motion-graphics shot (kinetic text, a counter,
     bars, a comparison, a checklist) and the spoken line for it, ending on a loop back to the hook
  3. the same compliance reviews as carousels, plus a check of the hook and pacing
  4. reel_render.py: voice, word-by-word captions, music, sound effects, optional stock b-roll,
     rendered to 1080x1920 MP4
  5. Gemini watches the finished video and scores it (hook, readability, pacing, audio,
     compliance). Below the bar it is rewritten once; if it still fails, the slot posts a
     carousel instead, so nothing weak goes out
  6. posted with the Instagram Reels API (resumable upload, so the MP4 never goes in git) and
     measured by the learning loop as format "reel"

Compliance always comes first: the brand Rules are at the top of every prompt, and every
check can only block a Reel, never loosen a rule.
"""
import json
import os
import pathlib
import random
import re
import time

import requests

ROOT = pathlib.Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text())

DEFAULTS = {
    "enabled": True,
    "per_day": 1,  # Reels a day (each takes about 5-8 minutes of Actions time)
    "slots": ["evening"],  # calendar slots that post a Reel instead of a carousel
    "remake_share": 0.5,  # share of Reels that remake a proven carousel, when one exists
    "remake_min_rel": 1.2,  # "proven" = scored at least this much better than a typical post
    "seconds": [20, 35],  # target length
    "language": "English",  # English, Hinglish (Roman script) or Hindi (Devanagari)
    "tts": "edge,kokoro",  # tried in order
    "voice": {
        "edge": "en-IN-NeerjaNeural",  # also good: en-IN-PrabhatNeural (male)
        "edge_hindi": "hi-IN-SwaraNeural",  # or hi-IN-MadhurNeural (male)
        "rate": "+8%",
        "kokoro": "af_heart",
        "kokoro_speed": 1.08,
    },
    "music_volume": 0.32,
    "sfx_volume": 0.6,
    "max_broll_scenes": 3,  # stock footage only behind text scenes, only when an API key is set
    "quality": {"min_score": 7, "min_each": 6},  # Gemini's 1-10 scores the video must reach
}

SCENE_TYPES = {
    "hook": '{"type":"hook","say":"...","text":"MAX 6 WORDS ON SCREEN","accent":"one word from text","broll":"2-4 word stock search or empty"}',
    "statement": '{"type":"statement","say":"...","text":"max 8 words","accent":"one word from text","sub":"optional max 10 words","kicker":"optional 1-3 words","broll":"..."}',
    "counter": '{"type":"counter","say":"...","value":20,"prefix":"₹ or empty","suffix":"% or k or empty","label":"max 7 words","kicker":"optional"}',
    "bars": '{"type":"bars","say":"...","title":"max 5 words","items":[{"label":"max 3 words","value":50}],"prefix":"","unit":"%"}',
    "compare": '{"type":"compare","say":"...","title":"max 5 words","left":{"label":"1-3 words","value":"max 4 words"},"right":{"label":"...","value":"..."},"better":"left|right|none"}',
    "list": '{"type":"list","say":"...","title":"max 5 words","items":["max 5 words each, 2-4 items"],"numbered":false}',
    "cta": '{"type":"cta","say":"...","text":"max 6 words","accent":"one word","button":"Follow"}',
}


def settings():
    s = {**DEFAULTS, **CFG.get("reels", {})}
    s["voice"] = {**DEFAULTS["voice"], **(CFG.get("reels", {}).get("voice") or {})}
    s["quality"] = {**DEFAULTS["quality"], **(CFG.get("reels", {}).get("quality") or {})}
    return s


# ---------------------------------------------------------------- when to make a Reel

def reels_today(history, day):
    return sum(1 for h in history if h.get("local_day") == day and h.get("format") == "reel")


def choose_format(slot, history, day, posts):
    """("reel" | "carousel", reason) for this slot. FORMAT=reel/carousel forces one.
    Reel slots post a Reel up to per_day; once both formats have enough measured posts in the
    slot, the better one wins (with the usual share of tests)."""
    import learning
    s = settings()
    forced = os.environ.get("FORMAT", "").strip().lower()
    if forced in ("reel", "carousel"):
        return forced, "chosen by hand"
    if not s["enabled"] or s["per_day"] <= 0:
        return "carousel", "Reels are switched off"
    if slot not in s["slots"]:
        return "carousel", f"the {slot or 'unscheduled'} slot is a carousel slot"
    if reels_today(history, day) >= s["per_day"]:
        return "carousel", f"already {s['per_day']} Reel(s) today"
    ls = learning.settings()
    pool = [p for p in posts if p.get("slot") == slot]
    rows = {r[0]: r for r in learning.ranking(pool, "format") if r[2] >= ls["min_per_option"]}
    if len(rows) == 2 and len(pool) >= ls["min_measured"]:
        best = max(rows.values(), key=lambda r: r[1])
        if random.random() >= ls["explore_rate"]:
            return best[0], f"best format in the {slot} slot ({best[1]:.2f}x typical over {best[2]} posts)"
        other = "carousel" if best[0] == "reel" else "reel"
        return other, f"testing (best in the {slot} slot is {best[0]})"
    return "reel", f"the {slot} slot is a Reel slot"


def pick_remake(history, posts):
    """The best measured carousel not yet remade, if it beat a typical post clearly. Only
    timeless posts: news and trend posts go stale."""
    s = settings()
    remade = {h.get("remake_of") for h in history if h.get("remake_of")}
    cands = [p for p in posts if p.get("format", "carousel") == "carousel" and p["rel"] >= s["remake_min_rel"]
             and p.get("angle") in (None, "educational", "promotional") and p["media_id"] not in remade]
    if not cands or random.random() >= s["remake_share"]:
        return None
    return max(cands, key=lambda p: p["rel"])


# ---------------------------------------------------------------- script

def script_prompt(past, learnings, voice, remake=None, feedback=""):
    s = settings()
    lo, hi = s["seconds"]
    lang = s["language"]
    lang_rule = {"english": "Write everything in clear Indian English.",
                 "hinglish": "Write the spoken lines and on-screen text in natural Hinglish (Hindi and English mixed, Roman script), the way young Indians talk.",
                 "hindi": "Write the spoken lines and on-screen text in simple, everyday Hindi (Devanagari script); English money words are fine."}.get(lang.lower(), f"Write in {lang}.")
    source = ""
    if remake:
        source = f"""
REMAKE A PROVEN POST: this carousel did {remake['rel']:.1f}x better than our typical post. Turn the
same topic and core idea into a Reel (same facts, a sharper video hook, not a copy of the slides):
Topic: {remake.get('topic')}
Hook it used: {remake.get('hook')}
Caption it used: {(remake.get('caption') or '')[:500]}"""
    types = "\n".join(f"- {v}" for v in SCENE_TYPES.values())
    return f"""You write short Instagram Reels for {CFG['brand']} ({CFG['handle']}).
Business and audience: {CFG['niche']}
Voice: {voice or CFG['voice']}
Rules (these come first, always): {CFG.get('rules', '')}
Topics already covered (do not repeat unless this is a remake): {past[-40:]}
{source}
{learnings}

Write a {lo}-{hi} second Reel as a shot plan that feels like top-performing, high-quality
creator content, not a slideshow and not generic AI filler:
- Scene 1 is the HOOK and must land within 2 seconds: the spoken line is at most 8 words and the
  on-screen text at most 6 words. Use a pattern interrupt: a surprising number, a sharp question,
  a common mistake, or a bold but TRUE statement. No greetings, no "hey guys", no "in this video".
- 6 to 9 scenes in total; each scene is ONE idea, 2 to 4 seconds of speech (6 to 14 words).
- Use at least 3 different scene types, and at least two visual ones (counter, bars, compare, list).
- Every number must be a clearly hypothetical example ("say you earn ₹40,000") or a widely
  accepted rule of thumb; never present invented statistics or studies as facts.
- On-screen text is short and punchy and never just repeats the whole spoken line.
- Spoken lines are conversational, fast and specific; one concrete example beats three vague tips.
- The last scene is "cta": a short line that ties back to the hook (so the loop rewatches well),
  then: {CFG['cta']}.
- "broll": a 2-4 word stock-footage search (e.g. "person checking phone") only for hook or
  statement scenes where real footage would help; otherwise "".
- {lang_rule} Write numbers as digits with ₹ where it is money.
Scene types (use these JSON shapes exactly):
{types}

Also write the Instagram caption. {CFG.get('caption_style', '')} Keep it under 700 characters,
first line is the hook, end with 5 to 7 relevant hashtags.{feedback}
Return ONLY JSON: {{"topic": "...", "scenes": [...], "caption": "..."}}"""


def validate(data):
    sc = data.get("scenes") or []
    assert 5 <= len(sc) <= 10, f"{len(sc)} scenes"
    assert sc[0].get("type") == "hook", "first scene must be the hook"
    assert sc[-1].get("type") == "cta", "last scene must be the cta"
    assert len(sc[0].get("say", "").split()) <= 10, "hook line too long"
    assert len(sc[0].get("text", "").split()) <= 7, "hook text too long"
    for s in sc:
        assert s.get("type") in SCENE_TYPES, f"unknown scene type {s.get('type')}"
        assert s.get("say", "").strip(), "scene without a spoken line"
        if s["type"] in ("bars", "list"):
            assert 2 <= len(s.get("items") or []) <= 4, f"{s['type']} needs 2-4 items"
        if s["type"] == "bars":
            for it in s["items"]:
                it["value"] = float(str(it.get("value", 0)).replace(",", "").rstrip("%") or 0)
        if s["type"] == "counter":
            s["value"] = float(str(s.get("value", 0)).replace(",", "").replace("₹", "") or 0)
        if s["type"] == "compare":
            assert s.get("left") and s.get("right"), "compare needs left and right"
    words = sum(len(s["say"].split()) for s in sc)
    lo, hi = settings()["seconds"]
    assert lo * 2.0 <= words <= hi * 3.0, f"{words} spoken words does not fit {lo}-{hi} seconds"
    assert data.get("caption"), "no caption"


def as_carousel(data):
    """The script in the shape the carousel reviewers (promo/trends checks) expect."""
    slides = []
    for s in data["scenes"]:
        shown = " | ".join(str(x) for x in [s.get("text"), s.get("title"), s.get("label"), s.get("sub"),
                                             *(s.get("items") or []), s.get("left"), s.get("right")] if x)
        slides.append({"title": shown, "body": s["say"]})
    return {"topic": data.get("topic"), "slides": slides, "caption": data.get("caption")}


def review_script(data, ask):
    """A strict editor pass on every Reel: rules, accuracy, and whether it is genuinely good."""
    prompt = f"""You are a strict editor for {CFG['brand']}'s Instagram Reels.
Rules: {CFG.get('rules', '')}
Flag a problem if the Reel script below:
- breaks any Rule (product picks, promised or implied returns, personal advice, hype, fear, urgency)
- states a statistic, study or "fact" that is not a widely accepted principle or a clearly
  hypothetical example, or gets any money concept wrong
- has a weak hook (generic, a greeting, slow, or over 8 spoken words) or is clickbait the Reel does not pay off
- is vague or repetitive, has scenes that do not move the idea forward, or on-screen text that is
  too long to read in the time
- would feel like low-effort AI content (empty phrases like "unlock", "game-changer", "journey")
Script: {json.dumps(data, ensure_ascii=False)}
Return ONLY JSON: {{"ok": true, "problems": ["..."]}}"""
    r = ask(prompt, lambda d: isinstance(d.get("ok"), bool))
    return r["ok"], [str(x) for x in r.get("problems", [])][:6]


def write_script(history, chosen, ask, remake=None, feedback=""):
    """Gemini writes the Reel, then the same reviews as a carousel of this type plus the
    editor pass; rewritten with the problems listed, up to 3 drafts. None if all fail."""
    import learning
    import promo
    import trends
    trend, promo_angle = chosen.get("trend"), chosen.get("promo_angle")
    general = bool(trend) and trend.get("format") == "general trends"
    extra = feedback
    for draft in range(3):
        learnings = learning.prompt_block(chosen)
        if promo_angle:
            learnings += "\n\n" + promo.prompt_block(promo_angle, 7).split("FORMAT:")[0]
        elif general:
            learnings += "\n\n" + trends.general_prompt_block(trend, 7).split("FORMAT:")[0]
        elif trend:
            learnings += "\n\n" + trends.prompt_block(trend, 7).split("FORMAT:")[0]
        voice = (promo.settings()["voice"] if promo_angle else trends.general_settings().get("voice") if general else None)
        try:
            data = ask(script_prompt([h["topic"] for h in history], learnings, voice, remake, extra), validate)
        except Exception as e:
            print(f"Reel script draft {draft + 1} failed: {e}")
            time.sleep(20)
            continue
        problems = []
        try:
            ok, problems = review_script(data, ask)
            if ok and (trend or promo_angle):
                flat = as_carousel(data)
                if promo_angle:
                    ok, problems = promo.check(flat, ask)
                else:
                    ok, problems = (trends.vibe_check if general else trends.fact_check)(flat, trend, ask)
        except Exception as e:
            ok, problems = False, [f"review could not run: {e}"]
        if ok:
            print("Reel script passed review:", data["topic"])
            return data
        print("Reel script review failed:", problems)
        extra = feedback + ("\n\nAn editor rejected the previous draft for these reasons; fix all of them:\n"
                            + "\n".join(f"- {p}" for p in problems))
    return None


# ---------------------------------------------------------------- quality check on the video

def quality_check(video, script):
    """Gemini watches the finished Reel. Returns (ok, scores, problems)."""
    from google import genai
    import main
    s = settings()["quality"]
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    f = client.files.upload(file=str(video))
    for _ in range(60):
        f = client.files.get(name=f.name)
        if str(getattr(f.state, "name", f.state)) == "ACTIVE":
            break
        if str(getattr(f.state, "name", f.state)) == "FAILED":
            raise RuntimeError("Gemini could not process the video")
        time.sleep(3)
    prompt = f"""You are the final quality gate for {CFG['brand']}'s Instagram Reels. Watch the whole
video and listen to it. Be strict: only content that could compete with top creators passes.
Rules (any break fails the video): {CFG.get('rules', '')}
Score 1-10:
- hook: do the first 2 seconds stop the scroll (clear, specific, visually strong)?
- readability: is every on-screen word readable in time, not cut off, overlapping or clashing with captions?
- pacing: does it move fast with no dead air or dragging scene?
- audio: is the voice clear and natural, captions in sync, music not too loud?
- value: would a viewer learn something useful or want to save or share it?
- polish: does it look professional and on-brand rather than like low-effort AI content?
Also set compliance_ok=false if it breaks a Rule or states a made-up statistic as fact.
The script it was made from: {json.dumps(script, ensure_ascii=False)[:4000]}
Return ONLY JSON: {{"scores": {{"hook": 0, "readability": 0, "pacing": 0, "audio": 0, "value": 0, "polish": 0}},
"compliance_ok": true, "problems": ["specific, fixable problems"]}}"""
    last = None
    for model in main.candidate_models(client):
        try:
            r = client.models.generate_content(model=model, contents=[f, prompt],
                                               config={"response_mime_type": "application/json"})
            d = json.loads(r.text)
            scores = {k: float(v) for k, v in d["scores"].items()}
            break
        except Exception as e:
            print(f"Quality check with {model} failed: {e}")
            last = e
    else:
        raise last
    try:
        client.files.delete(name=f.name)
    except Exception:
        pass
    avg = sum(scores.values()) / len(scores)
    ok = bool(d.get("compliance_ok")) and avg >= s["min_score"] and min(scores.values()) >= s["min_each"]
    probs = [str(x) for x in d.get("problems", [])][:6]
    if not d.get("compliance_ok"):
        probs.insert(0, "compliance: the reviewer says it breaks a rule")
    return ok, {**scores, "average": round(avg, 1)}, probs


# ---------------------------------------------------------------- build (called by main.generate)

def build(history, chosen, ask, outdir, remake=None):
    """Script -> video -> quality gate. Returns (script, render info, quality) or None."""
    import reel_render
    feedback = ""
    for attempt in range(2):
        data = write_script(history, chosen, ask, remake, feedback)
        if not data:
            return None
        info = reel_render.render(data, outdir)
        if not os.environ.get("GEMINI_API_KEY"):
            return data, info, {"skipped": "no GEMINI_API_KEY"}
        try:
            ok, scores, problems = quality_check(outdir / "reel.mp4", data)
        except Exception as e:
            print(f"::warning::Video quality check could not run ({e}); not posting an unchecked Reel")
            return None
        print("Quality scores:", scores, "problems:", problems)
        if ok:
            return data, info, {"scores": scores, "problems": problems}
        feedback = ("\n\nThe last version of this Reel was rendered and rejected by the quality reviewer "
                    f"(scores {scores}). Fix these problems:\n" + "\n".join(f"- {p}" for p in problems))
    return None


# ---------------------------------------------------------------- publishing

def publish(post, graph, token, uid):
    """Resumable upload of the local MP4 to Instagram as a Reel. Returns the media id."""
    video = ROOT / "posts" / post["folder"] / "reel.mp4"
    if not video.exists():
        raise SystemExit(f"{video} is missing (the MP4 is kept as a workflow artifact, not in git)")
    r = requests.post(f"{graph}/{uid}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": post["caption"],
        "share_to_feed": "true", "thumb_offset": str(post.get("cover_ms", 1000)), "access_token": token,
    }, timeout=60)
    if not r.ok:
        raise SystemExit(f"Instagram error: {r.text}")
    cid = r.json()["id"]
    version = graph.rstrip("/").rsplit("/", 1)[-1]
    up = requests.post(f"https://rupload.facebook.com/ig-api-upload/{version}/{cid}",
                       headers={"Authorization": f"OAuth {token}", "offset": "0",
                                "file_size": str(video.stat().st_size)},
                       data=video.read_bytes(), timeout=600)
    if not up.ok:
        raise SystemExit(f"Instagram upload error: {up.text}")
    for _ in range(60):  # video processing usually takes 30 s to 2 min
        st = requests.get(f"{graph}/{cid}", params={"fields": "status_code,status", "access_token": token},
                          timeout=30).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise SystemExit(f"Instagram could not process the Reel: {st.get('status')}")
        time.sleep(10)
    else:
        raise SystemExit("Instagram is still processing the Reel after 10 minutes")
    r = requests.post(f"{graph}/{uid}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=60)
    if not r.ok:
        raise SystemExit(f"Instagram error: {r.text}")
    return r.json().get("id")


def caption(data, info):
    text = data["caption"].strip()
    srcs = sorted({b["source"] for b in info.get("broll", [])})
    if srcs:
        text += "\n\nStock footage: " + " and ".join(srcs)
    return (text + "\n\n" + CFG.get("caption_footer", "")).strip()


# ---------------------------------------------------------------- samples

SAMPLES = [
    {
        "topic": "Sample: the 50/30/20 budget",
        "scenes": [
            {"type": "hook", "say": "Your salary isn't small. It's leaking.", "text": "Your salary is leaking", "accent": "leaking"},
            {"type": "statement", "say": "Most of us budget by guessing, then wonder where the money went.",
             "text": "Guessing is not a budget", "accent": "guessing", "kicker": "The problem"},
            {"type": "bars", "say": "Try the 50 30 20 rule. Half for needs, 30 for wants, 20 for savings.",
             "title": "The 50/30/20 rule", "items": [{"label": "Needs", "value": 50}, {"label": "Wants", "value": 30},
                                                     {"label": "Savings", "value": 20}], "unit": "%"},
            {"type": "counter", "say": "Say you earn ₹40,000. That's ₹8,000 saved, every single month.",
             "value": 8000, "prefix": "₹", "label": "saved a month on ₹40,000", "kicker": "Example"},
            {"type": "list", "say": "Do it in three steps. List your bills, set up an auto transfer on payday, and spend the rest guilt free.",
             "title": "Start today", "items": ["List your fixed bills", "Auto-transfer 20% on payday", "Spend the rest guilt-free"], "numbered": True},
            {"type": "compare", "say": "Saving what's left rarely works. Saving first does.",
             "title": "What actually works", "left": {"label": "Save what's left", "value": "Usually ₹0"},
             "right": {"label": "Save first", "value": "20% every month"}, "better": "right"},
            {"type": "cta", "say": "Stop the leak this payday. Follow RXT for better financial health.",
             "text": "Stop the leak this payday", "accent": "leak", "button": "Follow"},
        ],
        "caption": "Your salary isn't small. It's leaking.",
    },
    {
        "topic": "Sample: emergency fund in 3 numbers",
        "scenes": [
            {"type": "hook", "say": "One surprise bill can undo a year.", "text": "One bill. One year undone.", "accent": "undone"},
            {"type": "counter", "say": "Aim for 6 months of essential expenses as your safety net.",
             "value": 6, "suffix": " months", "label": "of essential expenses", "kicker": "The target"},
            {"type": "statement", "say": "Too big? Start with one month. Small wins keep you going.",
             "text": "Start with one month", "accent": "one", "sub": "Then build it up, month by month."},
            {"type": "counter", "say": "Even 10 percent of each salary gets you there faster than you think.",
             "value": 10, "suffix": "%", "label": "of every salary, automatically"},
            {"type": "list", "say": "Keep it separate, keep it boring, and only touch it for real emergencies.",
             "title": "The 3 rules", "items": ["Separate account", "Easy to reach, hard to spend", "Real emergencies only"]},
            {"type": "cta", "say": "So the next surprise bill is just a bill. Follow RXT for better financial health.",
             "text": "Make the next bill just a bill", "accent": "just", "button": "Follow"},
        ],
        "caption": "One surprise bill can undo a year.",
    },
]


def preview(which=None):
    import reel_render
    out = ROOT / "previews" / "reels"
    for i, data in enumerate(SAMPLES):
        if which is not None and i != which:
            continue
        validate(data)
        d = out / f"sample_{i + 1}"
        info = reel_render.render(data, d, seed=i + 7)
        print(json.dumps(info, indent=2))
