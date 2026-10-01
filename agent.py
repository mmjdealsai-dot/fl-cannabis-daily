#!/usr/bin/env python3
"""
Florida Medical Cannabis Daily — the morning agent.

Every morning this script asks Claude (with live web search) to find:
  1. current patient deals at each Florida dispensary listed in config.yaml
  2. upcoming Florida cannabis events
  3. recent Florida cannabis news (legislation, regulation, business, community)
It then flags what's new since yesterday, saves the data, rebuilds the
website in the /docs folder, and emails you the digest.

    python agent.py            normal daily run
    python agent.py --demo     try it with sample data (no API key, no email)
    python agent.py --no-email run everything except sending the email
"""
import argparse
import concurrent.futures as cf
import email
import hashlib
import html
import imaplib
import json
import os
import re
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
TZ = ZoneInfo("America/New_York")
NOW = datetime.now(TZ)
TODAY = NOW.date().isoformat()
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def log(msg):
    print(f"[{datetime.now(TZ):%H:%M:%S}] {msg}", flush=True)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Talking to Claude
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a careful research assistant for a Florida medical cannabis physician.
You gather factual, current information that is useful to Florida medical marijuana patients.

Rules:
- Use web search. Prefer official sources: the dispensary's own website and official social
  accounts, the Florida Department of Health / Office of Medical Marijuana Use, the Florida
  Legislature, and established news outlets.
- Never invent deals, dates, events or articles. If you cannot confirm something, leave it out.
- Every item must have a source_url that you actually saw in your search results.
- Write all summaries and descriptions in your own words. Never copy article text.
- Today's date is {today} (Florida time).
- End your reply with ONLY a JSON object inside a ```json code block, in exactly the format
  requested. Put nothing after the code block."""


def extract_json(text):
    """Pull the JSON object out of Claude's reply."""
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)[::-1]
    if "{" in text:
        candidates.append(text[text.find("{"): text.rfind("}") + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    raise ValueError("Claude's reply did not contain readable JSON")


def ask_claude(client, prompt, max_searches):
    cfg = CONFIG["claude"]
    kwargs = dict(
        model=cfg["model"],
        max_tokens=cfg.get("max_tokens", 8000),
        system=SYSTEM_PROMPT.format(today=TODAY),
    )
    if max_searches:
        kwargs["tools"] = [{"type": cfg["web_search_tool"], "name": "web_search",
                            "max_uses": max_searches}]
    messages = [{"role": "user", "content": prompt}]
    resp = None
    for _ in range(6):  # long searches can pause; we let Claude continue
        resp = client.messages.create(messages=messages, **kwargs)
        if resp.stop_reason != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": resp.content}]
    text = "\n".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return extract_json(text)


DEAL_FIELDS = """{
  "title": "short name of the deal, e.g. \\"25% off all vapes\\"",
  "details": "one or two sentences: what is included, conditions, limits",
  "category": "one of: flower, vapes, concentrates, edibles, tinctures, topicals, pre-rolls, accessories, storewide, patient-group, other",
  "valid_from": "YYYY-MM-DD or null",
  "valid_until": "YYYY-MM-DD or null",
  "schedule": "e.g. \\"every Tuesday\\", \\"one day only\\", \\"ongoing\\", or null",
  "locations": "\\"all Florida locations\\" or the specific cities",
  "source_url": "https://..."
}"""


def find_deals(client, dispensary, yesterday_titles):
    prev = "\n".join(f"- {t}" for t in yesterday_titles) or "(none)"
    prompt = f"""Find the medical marijuana patient deals, discounts and specials currently offered in
Florida by the dispensary "{dispensary}" — valid today ({TODAY}) or starting within the next 7 days.

Check their official deals / specials / promotions page and their official announcements.
Include standing discounts (veteran, senior, first-time patient, loyalty, recurring daily
specials such as "Wednesday 20% off flower") as well as limited-time sales.
Only include Florida deals. Skip expired deals.

Deals we listed for this dispensary yesterday:
{prev}
If a deal you find is the same as one above, reuse that exact title so we know it isn't new.

Return:
```json
{{"deals": [{DEAL_FIELDS}]}}
```
If you cannot find any current deals, return {{"deals": []}}."""
    return ask_claude(client, prompt, CONFIG["claude"]["searches_per_dispensary"]).get("deals", [])


def find_events(client):
    until = (NOW.date() + timedelta(days=60)).isoformat()
    prompt = f"""Find cannabis-related events happening in Florida between {TODAY} and {until}:
patient education sessions, dispensary events, cannabis expos and conferences, industry
networking, advocacy meetings (e.g. NORML chapters), public hearings about medical marijuana,
and community events. Check Eventbrite, Meetup, event listings, organizers' sites and news.

Return:
```json
{{"events": [{{
  "name": "event name",
  "date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD or null",
  "time": "e.g. \\"6:00 PM\\" or null",
  "city": "Florida city",
  "venue": "venue name or \\"Online\\"",
  "type": "one of: education, expo, industry, advocacy, government, community, dispensary",
  "description": "one or two sentences in your own words",
  "cost": "e.g. \\"Free\\", \\"$25\\", or null",
  "source_url": "https://..."
}}]}}
```"""
    return ask_claude(client, prompt, CONFIG["claude"]["searches_for_events"]).get("events", [])


def find_news(client, yesterday_headlines):
    since = (NOW.date() - timedelta(days=2)).isoformat()
    prev = "\n".join(f"- {h}" for h in yesterday_headlines[:40]) or "(none)"
    prompt = f"""Find news about cannabis in Florida published since {since}. Cover:
- legislation: bills, the Legislature, ballot initiatives, federal changes affecting Florida
- regulation: Department of Health / OMMU rules, licensing, testing, recalls, enforcement
- business: dispensary openings, closings, acquisitions, prices, products
- medical & research: studies or clinical news relevant to Florida patients
- social: community, advocacy, public opinion, court cases

Also check the Florida Senate and House bill trackers and OMMU announcements.

We already covered these stories yesterday; skip them unless there is a real new development:
{prev}

Return:
```json
{{"news": [{{
  "headline": "the article's headline",
  "summary": "two sentences in your own words: what happened and why it matters to Florida patients",
  "category": "one of: legislation, regulation, business, medical, social",
  "source": "publication name",
  "published": "YYYY-MM-DD",
  "url": "https://..."
}}]}}
```"""
    return ask_claude(client, prompt, CONFIG["claude"]["searches_for_news"]).get("news", [])


# ---------------------------------------------------------------------------
# Optional: promo emails in the bot's Gmail inbox
# ---------------------------------------------------------------------------
def _decode(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _email_text(msg):
    plain, htm = "", ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        try:
            payload = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if ctype == "text/plain" and not plain:
            plain = payload
        elif ctype == "text/html" and not htm:
            htm = payload
    if plain:
        return plain
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", htm)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text))


def read_inbox_deals(client):
    cfg = CONFIG.get("inbox") or {}
    if not cfg.get("enabled"):
        return []
    addr, pw = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
    since = (NOW - timedelta(hours=cfg.get("hours_back", 30))).strftime("%d-%b-%Y")
    log(f"Reading promo emails since {since}…")
    box = imaplib.IMAP4_SSL("imap.gmail.com")
    box.login(addr, pw)
    box.select(cfg.get("folder", "INBOX"), readonly=True)
    _, ids = box.search(None, f'(SINCE "{since}")')
    emails = []
    for num in ids[0].split()[-80:]:
        _, raw = box.fetch(num, "(RFC822)")
        msg = email.message_from_bytes(raw[0][1])
        emails.append(f"FROM: {_decode(msg['From'])}\nSUBJECT: {_decode(msg['Subject'])}\n"
                      f"{_email_text(msg)[:4000]}")
    box.logout()
    log(f"  {len(emails)} emails found")

    deals = []
    for i in range(0, len(emails), 12):
        chunk = "\n\n=====\n\n".join(emails[i:i + 12])
        prompt = f"""Below are promotional emails received in the last day by an inbox subscribed to
Florida medical marijuana dispensary mailing lists. Extract every patient deal that is valid today
({TODAY}) or starts within 7 days. Ignore emails that are not deals.

{chunk}

Return:
```json
{{"deals": [{{"dispensary": "dispensary brand name", "from_email": true,
  "title": "...", "details": "...", "category": "...", "valid_from": null, "valid_until": null,
  "schedule": null, "locations": "...", "source_url": "a link from the email, or null"}}]}}
```
Use the category list: flower, vapes, concentrates, edibles, tinctures, topicals, pre-rolls,
accessories, storewide, patient-group, other."""
        try:
            deals += ask_claude(client, prompt, 0).get("deals", [])
        except Exception as e:
            log(f"  inbox chunk failed: {e}")
    return deals


# ---------------------------------------------------------------------------
# What's new since yesterday
# ---------------------------------------------------------------------------
def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def item_id(kind, *parts):
    return hashlib.sha1((kind + "|" + "|".join(_norm(p) for p in parts)).encode()).hexdigest()[:12]


def mark_new(items, seen, kind, key_fn):
    out, ids = [], set()
    for it in items:
        iid = item_id(kind, *key_fn(it))
        if iid in ids:
            continue  # duplicate
        ids.add(iid)
        seen.setdefault(iid, TODAY)
        it["id"] = iid
        it["first_seen"] = seen[iid]
        it["is_new"] = seen[iid] == TODAY
        out.append(it)
    return out


def _date_or(s, fallback):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return fallback


def tidy(data):
    today = NOW.date()
    seen = load_json(DATA / "seen.json", {})

    deals = [d for d in data["deals"] if d.get("title")
             and _date_or(d.get("valid_until"), today) >= today]
    data["deals"] = mark_new(deals, seen, "deal", lambda d: (d.get("dispensary"), d.get("title")))
    data["deals"].sort(key=lambda d: (_norm(d.get("dispensary")), not d["is_new"], _norm(d["title"])))

    events = [e for e in data["events"] if e.get("name")
              and _date_or(e.get("end_date") or e.get("date"), today) >= today]
    data["events"] = mark_new(events, seen, "event", lambda e: (e.get("name"), e.get("date")))
    data["events"].sort(key=lambda e: str(e.get("date") or "9999"))

    news = [n for n in data["news"] if n.get("headline")]
    data["news"] = mark_new(news, seen, "news", lambda n: (n.get("url") or n.get("headline"),))
    data["news"].sort(key=lambda n: str(n.get("published") or ""), reverse=True)

    cutoff = (today - timedelta(days=120)).isoformat()
    save_json(DATA / "seen.json", {k: v for k, v in seen.items() if v >= cutoff})
    return data


# ---------------------------------------------------------------------------
# Website
# ---------------------------------------------------------------------------
def build_site(data):
    template = (ROOT / "site_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = (template.replace("__SITE_TITLE__", html.escape(CONFIG["site"]["title"]))
                    .replace("__SITE_DATA__", payload))
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    save_json(DOCS / "data.json", data)
    (DOCS / ".nojekyll").write_text("")
    log("Website rebuilt: docs/index.html")


# ---------------------------------------------------------------------------
# Email digest
# ---------------------------------------------------------------------------
E = html.escape
INK, TEAL, CORAL, MUTED, LINE, TAG = "#122A2E", "#0F5A63", "#C8452F", "#5A6F71", "#D5DFDB", "#F4B942"


def _link(url, label="Source"):
    return f'<a href="{E(url)}" style="color:{TEAL}">{E(label)}</a>' if url else ""


def _deal_row(d):
    bits = [x for x in (d.get("schedule"), d.get("locations"),
                        f"through {d['valid_until']}" if d.get("valid_until") else None) if x]
    return (f'<tr><td style="padding:10px 0;border-bottom:1px solid {LINE}">'
            f'<div style="font-weight:700;color:{INK}">{E(d.get("dispensary",""))}: '
            f'<span style="background:{TAG};padding:1px 6px;border-radius:3px">{E(d["title"])}</span></div>'
            f'<div style="color:{INK};margin-top:4px">{E(d.get("details") or "")}</div>'
            f'<div style="color:{MUTED};font-size:13px;margin-top:4px">{E(" / ".join(bits))} '
            f'{_link(d.get("source_url"))}</div></td></tr>')


def _section(title, rows, empty):
    body = "".join(rows) or f'<tr><td style="color:{MUTED};padding:8px 0">{E(empty)}</td></tr>'
    return (f'<h2 style="font-size:19px;color:{TEAL};margin:28px 0 4px">{E(title)}</h2>'
            f'<table width="100%" cellpadding="0" cellspacing="0">{body}</table>')


def build_email(data):
    new_deals = [d for d in data["deals"] if d["is_new"]]
    new_events = [e for e in data["events"] if e["is_new"]]
    soon = (NOW.date() + timedelta(days=14)).isoformat()
    upcoming = [e for e in data["events"] if not e["is_new"] and str(e.get("date", "")) <= soon]
    new_news = [n for n in data["news"] if n["is_new"]]
    site_url = CONFIG["site"].get("url")

    counts = {}
    for d in data["deals"]:
        counts[d.get("dispensary", "Other")] = counts.get(d.get("dispensary", "Other"), 0) + 1

    def ev_row(e):
        when = e.get("date", "") + (f" {e['time']}" if e.get("time") else "")
        return (f'<tr><td style="padding:10px 0;border-bottom:1px solid {LINE}">'
                f'<div style="font-weight:700;color:{INK}">{E(e["name"])}</div>'
                f'<div style="color:{MUTED};font-size:13px">{E(when)} / {E(e.get("city") or "")} '
                f'{E(e.get("venue") or "")}</div>'
                f'<div style="color:{INK};margin-top:4px">{E(e.get("description") or "")} '
                f'{_link(e.get("source_url"), "Details")}</div></td></tr>')

    def news_row(n):
        return (f'<tr><td style="padding:10px 0;border-bottom:1px solid {LINE}">'
                f'<div style="font-weight:700">{_link(n.get("url"), n["headline"])}</div>'
                f'<div style="color:{MUTED};font-size:13px">{E((n.get("category") or "").title())} / '
                f'{E(n.get("source") or "")} / {E(n.get("published") or "")}</div>'
                f'<div style="color:{INK};margin-top:4px">{E(n.get("summary") or "")}</div></td></tr>')

    still = ", ".join(f"{k} ({v})" for k, v in sorted(counts.items()))
    parts = [
        f'<div style="font-family:Arial,Helvetica,sans-serif;max-width:680px;margin:0 auto;color:{INK};line-height:1.45">',
        f'<h1 style="font-size:24px;margin:0">{E(CONFIG["site"]["title"])}</h1>',
        f'<div style="color:{MUTED}">{NOW:%A, %B %-d, %Y}</div>',
        f'<p style="margin:14px 0 0">{len(new_deals)} new deals, {len(new_events)} new events and '
        f'{len(new_news)} new stories today. {len(data["deals"])} deals are active in total.'
        + (f' {_link(site_url, "Open the website")}' if site_url else "") + "</p>",
        _section("New deals", [_deal_row(d) for d in new_deals], "No new deals found today."),
        f'<p style="color:{MUTED};font-size:13px">Still running: {E(still) or "none"}</p>',
        _section("New events", [ev_row(e) for e in new_events], "No new events found today."),
        _section("Coming up in the next two weeks", [ev_row(e) for e in upcoming], "Nothing else scheduled."),
        _section("News", [news_row(n) for n in new_news], "No new stories today."),
    ]
    if data.get("errors"):
        parts.append(f'<p style="color:{CORAL};font-size:13px;margin-top:28px">Couldn\'t check: '
                     f'{E("; ".join(data["errors"]))}</p>')
    parts.append("</div>")

    text = [f"{CONFIG['site']['title']} — {NOW:%A, %B %-d}", ""]
    text += ["NEW DEALS"] + [f"- {d.get('dispensary')}: {d['title']} {d.get('source_url') or ''}" for d in new_deals]
    text += ["", "NEW EVENTS"] + [f"- {e.get('date')} {e['name']} ({e.get('city')})" for e in new_events]
    text += ["", "NEWS"] + [f"- {n['headline']} {n.get('url') or ''}" for n in new_news]
    if site_url:
        text += ["", site_url]

    subject = (f"{CONFIG['email']['subject_prefix']} {NOW:%b %-d}: {len(new_deals)} new deals, "
               f"{len(new_events)} events, {len(new_news)} stories")
    return subject, "".join(parts), "\n".join(text)


def send_email(subject, html_body, text_body):
    addr = os.environ.get("GMAIL_ADDRESS")
    pw = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "")
    to = os.environ.get("DIGEST_TO") or addr
    if not (addr and pw):
        log("Email skipped: GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, addr, to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(addr, pw)
        s.sendmail(addr, [x.strip() for x in to.split(",")], msg.as_string())
    log(f"Email sent to {to}")


# ---------------------------------------------------------------------------
# Collecting everything
# ---------------------------------------------------------------------------
def collect():
    import anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Missing ANTHROPIC_API_KEY. Add it under Settings > Secrets and variables > Actions.")
    client = anthropic.Anthropic(max_retries=4, timeout=600)

    yesterday = load_json(DATA / "latest.json", {})
    prev_titles = {}
    for d in yesterday.get("deals", []):
        prev_titles.setdefault(d.get("dispensary"), []).append(d.get("title"))

    data = {"deals": [], "events": [], "news": [], "errors": []}
    names = CONFIG["dispensaries"]
    log(f"Checking deals at {len(names)} dispensaries…")
    with cf.ThreadPoolExecutor(max_workers=CONFIG["claude"].get("parallel_jobs", 3)) as pool:
        jobs = {pool.submit(find_deals, client, n, prev_titles.get(n, [])): n for n in names}
        for job in cf.as_completed(jobs):
            name = jobs[job]
            try:
                found = job.result()
                for d in found:
                    d["dispensary"] = name
                data["deals"] += found
                log(f"  {name}: {len(found)} deals")
            except Exception as e:
                data["errors"].append(f"{name} deals")
                log(f"  {name}: FAILED ({e})")

    try:
        data["deals"] += read_inbox_deals(client)
    except Exception as e:
        data["errors"].append("promo inbox")
        log(f"Inbox FAILED ({e})")

    for label, fn in (("events", lambda: find_events(client)),
                      ("news", lambda: find_news(client, [n.get("headline") for n in yesterday.get("news", [])]))):
        log(f"Finding {label}…")
        try:
            data[label] = fn()
            log(f"  {len(data[label])} {label}")
        except Exception as e:
            data["errors"].append(label)
            log(f"  {label}: FAILED ({e})")
    return data


def demo_data():
    t = NOW.date()
    return {
        "deals": [
            {"dispensary": "Sample Dispensary A", "title": "25% off all vapes", "category": "vapes",
             "details": "Sample deal for testing. Applies to all cartridges and disposables; excludes accessories.",
             "valid_until": (t + timedelta(days=3)).isoformat(), "schedule": "limited time",
             "locations": "all Florida locations", "source_url": "https://example.com/deals"},
            {"dispensary": "Sample Dispensary A", "title": "Veterans save 20% every day", "category": "patient-group",
             "details": "Sample standing discount with a valid VA ID.", "schedule": "ongoing",
             "locations": "all Florida locations", "source_url": "https://example.com/veterans"},
            {"dispensary": "Sample Dispensary B", "title": "Buy 2 edibles, get 1 free", "category": "edibles",
             "details": "Sample BOGO-style offer on gummies and chocolates.", "schedule": "every Wednesday",
             "locations": "Tampa, Orlando", "source_url": "https://example.com/b"},
        ],
        "events": [
            {"name": "Sample patient education night", "date": (t + timedelta(days=5)).isoformat(), "time": "6:30 PM",
             "city": "Fort Lauderdale", "venue": "Community center", "type": "education", "cost": "Free",
             "description": "Sample event: a Q&A on dosing basics and product types.", "source_url": "https://example.com/e1"},
            {"name": "Sample cannabis industry expo", "date": (t + timedelta(days=21)).isoformat(),
             "end_date": (t + timedelta(days=22)).isoformat(), "city": "Orlando", "venue": "Convention center",
             "type": "expo", "cost": "$25", "description": "Sample two-day trade show.", "source_url": "https://example.com/e2"},
        ],
        "news": [
            {"headline": "Sample: new bill filed on patient card renewals", "category": "legislation",
             "summary": "Sample summary. A proposed change to renewal timing could reduce visits for established patients.",
             "source": "Sample News", "published": t.isoformat(), "url": "https://example.com/n1"},
            {"headline": "Sample: dispensary opens third Broward location", "category": "business",
             "summary": "Sample summary for layout testing.", "source": "Sample Times",
             "published": (t - timedelta(days=1)).isoformat(), "url": "https://example.com/n2"},
        ],
        "errors": [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="use sample data; no API calls, no email, nothing saved")
    ap.add_argument("--no-email", action="store_true")
    args = ap.parse_args()

    if args.demo:
        global DATA, DOCS
        DATA = DOCS = ROOT / "demo_output"
    data = demo_data() if args.demo else collect()
    data = tidy(data)
    data["generated_at"] = NOW.isoformat()
    data["site"] = CONFIG["site"]

    if not args.demo:
        save_json(DATA / "latest.json", data)
        save_json(DATA / "archive" / f"{TODAY}.json", data)
    build_site(data)

    subject, html_body, text_body = build_email(data)
    (DATA / "last_email_preview.html").parent.mkdir(exist_ok=True)
    (DATA / "last_email_preview.html").write_text(html_body, encoding="utf-8")
    log(f"Subject: {subject}")
    if not (args.demo or args.no_email):
        send_email(subject, html_body, text_body)

    total = len(data["deals"]) + len(data["events"]) + len(data["news"])
    if not args.demo and total == 0 and data["errors"]:
        sys.exit("Every step failed — see the messages above.")
    log("Done.")


if __name__ == "__main__":
    main()
