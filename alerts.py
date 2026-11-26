"""Failure alerts: tells you (as a GitHub issue, which GitHub emails you) when the bot breaks.

    python alerts.py failed     -> last step of every workflow, runs only when the run failed:
                                   opens (or comments on) "Bot alert: <workflow> failed" with the
                                   step that broke, the error message and a link to the run
    python alerts.py recovered  -> runs after a run that did real work succeeded: closes that
                                   workflow's open alert with a "working again" comment
    python alerts.py health     -> daily check (.github/workflows/health.yml, 22:57 India time):
                                   the dashboard's GitHub token expiring (repo variable
                                   DASHBOARD_TOKEN_EXPIRES, saved by the dashboard), and
                                   calendar slots that passed today without a post

One open issue per problem, labelled bot-alert. A problem that keeps happening adds a comment
at most every few hours instead of a new issue, so a bad day does not flood your inbox.
Closing an issue yourself is fine: the next failure opens a new one.

Uses only the Python standard library for the failure path, so it still works when the run
broke before anything was installed. Nothing here changes what the bot posts or when.
"""
import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).parent
API = "https://api.github.com"
LABEL = "bot-alert"
QUIET_HOURS = 3  # a repeat of the same failure inside this window is logged but not emailed
TOKEN_WARN_DAYS = 14  # warn this long before the dashboard's GitHub token expires
# (the Instagram token is not checked here: Harish asked to drop that check on 2026-11-26.
# A failed post still raises a "Content calendar failed" alert with Instagram's error.)
# a plain-language hint per workflow step, so the alert says where to look first
HINTS = {
    "Is a post due?": "The calendar check itself broke, so no post was attempted.",
    "Install": "Installing tools failed (usually a temporary GitHub or package-mirror problem). "
               "The next run normally works.",
    "Generate": "Making the post failed: usually Gemini (quota, outage or GEMINI_API_KEY), slide "
                "rendering, or the Reel renderer. Nothing was posted.",
    "Commit slides": "Saving the new post to the repo failed (often two runs pushing at once). "
                     "Nothing was posted.",
    "Publish": "Instagram refused the post. Check the error below: an expired token, missing "
               "permission, or Instagram processing the media.",
    "Publish (only when AUTO_PUBLISH is true)": "Instagram refused the post. Check the error below: "
               "an expired token, missing permission, or Instagram processing the media.",
    "Fetch the Reel video": "The Reel's video file could not be found (workflow artifacts expire "
                            "after 30 days, or the run that made it failed).",
    "Save posted state": "The post went out, but saving that fact failed. The next run might post "
                         "the same slot again; check Instagram.",
    "Write the recap": "The weekly recap could not be written (Instagram or GitHub API).",
    "Commit the recap": "The recap was written but could not be saved to the repo.",
}


def error_file():
    return pathlib.Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "rxt-last-error.txt"


SECRET_ENV = ("IG_TOKEN", "GEMINI_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "GITHUB_TOKEN")


def redact(text):
    """The repo is public, so an error quoted in an issue must never carry a key or token
    (request URLs in error messages include ?access_token=...)."""
    for name in SECRET_ENV:
        val = os.environ.get(name)
        if val and len(val) > 6:
            text = text.replace(val, f"[{name}]")
    text = re.sub(r"(access_token|input_token|api_key|key|token)=[^&\s'\"]+", r"\1=[hidden]", text, flags=re.I)
    return re.sub(r"\bEAA[A-Za-z0-9]{20,}", "[token]", text)  # Meta tokens


def record_error(text):
    """Called by main.py when a command fails, so the alert can quote the actual error."""
    try:
        with open(error_file(), "a") as f:
            f.write(redact(text.strip())[-1500:] + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- GitHub

def gh(method, path, body=None, params=None):
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        print("No GITHUB_TOKEN / GITHUB_REPOSITORY, cannot send alerts")
        return None
    url = f"{API}/repos/{repo}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "Content-Type": "application/json", "User-Agent": "rxt-bot-alerts"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub {method} {path}: {e.code} {e.read().decode()[:200]}") from None
    return json.loads(text) if text else {}


def find_open(title):
    for issue in gh("GET", "/issues", params={"labels": LABEL, "state": "open", "per_page": 50}) or []:
        if issue["title"] == title:
            return issue
    return None


def cc():
    who = os.environ.get("NOTIFY", "").strip().lstrip("@")
    return f"\n\n_cc @{who}_" if who else ""


def now_local():
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    return dt.datetime.now(ist).strftime("%a %d %b %H:%M") + " India time"


def raise_alert(title, body, quiet_hours=QUIET_HOURS):
    """Open the alert, or comment on the open one (at most once per quiet_hours)."""
    try:
        gh("POST", "/labels", {"name": LABEL, "color": "d93f0b", "description": "Something in the bot broke"})
    except RuntimeError:
        pass  # already exists
    issue = find_open(title)
    if issue is None:
        new = gh("POST", "/issues", {"title": title, "body": body + cc(), "labels": [LABEL]})
        print("Opened alert:", new and new.get("html_url"))
        return
    updated = dt.datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
    if dt.datetime.now(dt.timezone.utc) - updated < dt.timedelta(hours=quiet_hours):
        print(f"Same alert already sent in the last {quiet_hours} h, not emailing again:", issue["html_url"])
        return
    gh("POST", f"/issues/{issue['number']}/comments", {"body": "**It happened again.**\n\n" + body + cc()})
    print("Commented on alert:", issue["html_url"])


def clear_alert(title, note):
    issue = find_open(title)
    if issue is None:
        return
    gh("POST", f"/issues/{issue['number']}/comments", {"body": note})
    gh("PATCH", f"/issues/{issue['number']}", {"state": "closed", "state_reason": "completed"})
    print("Closed alert:", issue["html_url"])


# ---------------------------------------------------------------- failed / recovered runs

def run_url():
    return f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY')}" \
           f"/actions/runs/{os.environ.get('GITHUB_RUN_ID')}"


def failed_steps():
    """Names of the steps that failed in this run so far (the alert step runs last)."""
    run, attempt = os.environ.get("GITHUB_RUN_ID"), os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    try:
        jobs = gh("GET", f"/actions/runs/{run}/attempts/{attempt}/jobs") or {}
    except RuntimeError as e:
        print("Could not read the run's steps:", e)
        return []
    return [s["name"] for j in jobs.get("jobs", []) for s in j.get("steps", [])
            if s.get("conclusion") in ("failure", "timed_out", "cancelled")]


def alert_title():
    return f"Bot alert: {os.environ.get('GITHUB_WORKFLOW', 'a workflow')} failed"


def failed():
    steps = failed_steps()
    slot = os.environ.get("SLOT", "").strip()
    what = f"the **{slot}** slot" if slot else "this run"
    lines = [f"**{os.environ.get('GITHUB_WORKFLOW', 'A workflow')}** failed for {what} on {now_local()}.", ""]
    if steps:
        lines.append("Step that broke: " + ", ".join(f"**{s}**" for s in steps))
        hints = [HINTS[s] for s in steps if s in HINTS]
        if hints:
            lines += ["", hints[0]]
    else:
        lines.append("The run stopped (it may have been cancelled or timed out).")
    err = error_file().read_text().strip() if error_file().exists() else ""
    if err:
        lines += ["", "Error:", "```", redact(err)[-1500:].replace("```", "'''"), "```"]
    lines += ["", f"Run log: {run_url()}"]
    if os.environ.get("GITHUB_WORKFLOW") == "Content calendar":
        lines += ["", "The calendar runs every 30 minutes inside each slot's window, so if the problem was "
                      "temporary the slot can still post on the next run. This issue closes itself when a "
                      "run works again."]
    raise_alert(alert_title(), "\n".join(lines))


def recovered():
    clear_alert(alert_title(), f"Working again: this run succeeded on {now_local()}.\n\n{run_url()}")


# ---------------------------------------------------------------- daily health check

def dashboard_token_problem(now=None):
    """The dashboard's own GitHub token (a fine-grained token that expires). The dashboard copies
    its expiry date into the repo variable DASHBOARD_TOKEN_EXPIRES whenever someone uses it."""
    now = now or dt.datetime.now(dt.timezone.utc)
    raw = os.environ.get("DASHBOARD_TOKEN_EXPIRES", "").strip()
    if not raw:
        print("Dashboard token expiry not known yet (it is saved the first time someone opens the dashboard)")
        return None
    try:
        when_ = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        print("Could not read DASHBOARD_TOKEN_EXPIRES:", raw)
        return None
    left = when_ - now
    if left >= dt.timedelta(days=TOKEN_WARN_DAYS):
        print(f"Dashboard token OK; expires {when_:%d %b %Y}")
        return None
    status = "has expired" if left.total_seconds() <= 0 else f"expires on {when_:%d %b %Y} ({left.days} days left)"
    return (f"The dashboard's GitHub token {status}. Without a working token the dashboard cannot show runs, "
            "start posts or switch the publishing mode (the bot itself keeps posting). Renew it: GitHub, "
            "Settings, Developer settings, Fine-grained tokens, rxt-dashboard, Regenerate token, then paste the "
            "new token into Cloudflare (Pages project rxt, Settings, Variables and secrets, GITHUB_TOKEN). "
            "Opening the dashboard once afterwards records the new date and closes this alert at the next check.")


def missed_slots():
    """Today's slots (India time) whose window has closed without a post, when posting is automatic."""
    sys.path.insert(0, str(ROOT))
    import slots
    if os.environ.get("AUTO_PUBLISH", "").strip().lower() != "true":
        print("AUTO_PUBLISH is off, drafts wait for approval; not checking for missed slots")
        return []
    now = slots.local_now()
    day = now.date().isoformat()
    posted = slots.posted_on(day)
    if len(posted) >= slots.settings()["max_per_day"]:
        return []  # the day's cap was reached (e.g. extra posts), so skipped slots are expected
    done = {slots.where(p)[1] for p in posted}
    return [name for name in slots.slot_names()
            if name not in done and slots.at(day, slots.settings()["slots"][name]["window"][1]) <= now]


def health():
    dash = dashboard_token_problem()
    title = "Bot alert: dashboard GitHub token"
    if dash:
        raise_alert(title, f"Daily check on {now_local()}:\n\n- {dash}\n\nCheck log: {run_url()}", quiet_hours=20)
    elif os.environ.get("DASHBOARD_TOKEN_EXPIRES", "").strip():
        clear_alert(title, f"The dashboard token was renewed ({now_local()}).")
    missed = missed_slots()
    if missed:
        raise_alert("Bot alert: missed posts",
                    f"On {now_local()}: today's **{', '.join(missed)}** slot{'s' if len(missed) > 1 else ''} "
                    "passed without a post. Usually GitHub skipped or delayed the scheduled runs, or a run "
                    "failed (look for a separate alert). Content calendar runs: "
                    f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY')}"
                    "/actions/workflows/calendar.yml", quiet_hours=20)
    print("Problems:", int(bool(dash)), "dashboard token,", len(missed), "missed slots")


if __name__ == "__main__":
    {"failed": failed, "recovered": recovered, "health": health}[sys.argv[1]]()
