"""Promotional posts: creative carousels about RXT itself (what it does and why it helps),
told through a different creative angle each time.

Claims come ONLY from config.json "promotional" -> "facts" (plus the business description in
"niche"). Gemini may not invent features, prices, offers, numbers, results, client stories,
testimonials, awards or partnerships. A second Gemini pass checks every draft for that and for
the brand rules; a draft that keeps failing becomes a plain educational post instead.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text())

DEFAULTS = {
    "share": 0.15,  # share of days with a promotional post until there is data (about 1 in 7)
    "voice": ("warm, confident and creative; proud of what RXT does but never pushy. Plain language, "
              "a little playful where it fits, always honest."),
    # what RXT really offers; the ONLY things a promotional post may say about RXT.
    # Add real features, services, how to get started and links here.
    "facts": [
        "RXT is a financial health clinic for individuals.",
        "RXT helps people build financial health through simple financial education and guidance.",
        "RXT covers budgeting, saving, debt, credit, emergency funds, insurance basics and making informed money decisions.",
        "RXT is for everyday people who feel unsure about money and want clear, practical steps.",
        "RXT explains things in plain language, without jargon.",
        "RXT does not sell or recommend specific financial products, and never promises returns.",
    ],
    "cta": "",  # e.g. "Follow RXT and tap the link in bio to book a session"; empty = the usual CTA
    # creative angles, rotated (least used first) and later picked by results
    "angles": {
        "money check-up": "Play on the clinic idea: common 'symptoms' of money stress, then how RXT's "
                          "kind of help works like a check-up. Clinic and prescription wordplay welcome.",
        "myths about getting money help": "Bust myths that stop people asking for money help (you must be "
                                          "rich, in crisis, good at maths, or ready to be judged), then show "
                                          "how RXT fits.",
        "is this you?": "A relatable quiz or 'signs you would benefit from RXT' list of everyday money "
                        "moments, ending with how RXT helps with them.",
        "what we will never do": "A trust post: the things RXT will never do (sell products, promise "
                                 "returns, use jargon, judge) and what it does instead.",
        "letter from future you": "A short, warm letter from the reader's future self who started "
                                  "building financial health, without promising any result or number.",
        "this or that": "A playful 'this or that' contrast between money confusion and money clarity, "
                        "showing where RXT helps, framed as feelings and habits, not results.",
        "one tip, then us": "Give one genuinely useful money tip from the topic area first, then show "
                            "that this is the kind of clear help RXT offers.",
    },
}


def settings():
    s = {**DEFAULTS, **CFG.get("promotional", {})}
    s["angles"] = s.get("angles") or DEFAULTS["angles"]
    return s


def prompt_block(promo_angle, slides):
    """Gemini instructions for a promotional post (added after the brand rules)."""
    s = settings()
    facts = "\n".join(f"  - {f}" for f in s["facts"])
    idea = s["angles"].get(promo_angle, "")
    cta = s.get("cta") or CFG["cta"]
    return f"""PROMOTIONAL POST (a creative post about {CFG['brand']} itself):
This is the one kind of post that may talk about {CFG['brand']} directly. Every other Rule
still applies in full: never recommend specific stocks, funds, loans or products, never promise
or imply returns or results, never give personalised advice, no hype, scare tactics or urgency.
Creative angle: {promo_angle}. {idea}
What you may say about {CFG['brand']} (ONLY these; invent nothing else about it):
{facts}
Never invent features, services, prices, discounts, offers, deadlines, numbers, statistics,
results, client stories, testimonials, quotes, reviews, awards or partnerships. Any example
person must be clearly hypothetical ("say you ...") and must not be presented as a real client.
TONE FOR THIS POST (replaces the usual Voice): {s['voice']}
FORMAT:
- slide 1: a creative, scroll-stopping hook for the angle (max 9 words); not a sales line
- slides 2 to {slides - 1}: tell the angle as a story or list; at least two of these slides
  must teach something genuinely useful about the topic area on their own, so the post is
  worth saving even for someone who never contacts {CFG['brand']}
- slide {slides}: call to action: {cta}
Caption: a creative first line that matches the angle, then the usual structure; it may say
plainly what {CFG['brand']} helps with (from the list above). Up to three emojis."""


def check(data, ask):
    """Second Gemini pass on a promotional carousel. Returns (ok, problems)."""
    s = settings()
    prompt = f"""You review a promotional Instagram carousel for {CFG['brand']} before it is posted.
Rules: {CFG.get('rules', '')}
(Talking about {CFG['brand']} itself is allowed in this post; everything else in the Rules applies.)
The ONLY true facts about {CFG['brand']}: {json.dumps(s['facts'], ensure_ascii=False)}

Flag a problem if the carousel:
- claims any feature, service, price, offer, number, result, award or partnership for
  {CFG['brand']} that is not in the facts
- contains a testimonial, review, quote or client story, or presents an example person as real
- promises or implies financial results or returns, or gives personalised advice
- recommends a specific product, stock, fund, lender or app
- uses hype, pressure, fear, urgency or a misleading claim
- teaches nothing useful on its own (pure advertising)
Creative wordplay, metaphors and clearly hypothetical examples are fine.

Carousel: {json.dumps(data, ensure_ascii=False)}
Return ONLY JSON: {{"ok": true, "problems": ["..."]}}"""
    r = ask(prompt, lambda d: isinstance(d.get("ok"), bool))
    return r["ok"], [str(x) for x in r.get("problems", [])][:5]
