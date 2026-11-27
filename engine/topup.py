"""Weekly quote top-up with Google Gemini (free tier).

Asks Gemini for new short, original, relatable lines in the head's niche (prompts from its config), filters them,
and appends the keepers to quotes.csv (source=gemini). Skips the call when plenty of unused quotes remain.

Filters: a second Gemini pass scores each line 1-10 for wit and shareability (keeps 8+), 25-120 characters, no hashtags/emoji/@, no famous-quote attributions, not too close
(similarity > 0.75) to any existing line, no unsafe or off-brand words.

Env: GEMINI_API_KEY (GitHub secret), GEMINI_MODEL (default gemini-2.5-flash), MIN_STOCK (default 120).
"""
import csv, datetime, difflib, json, os, re, urllib.error, urllib.request

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import HEAD as HERE, CFG  # noqa: E402
QUOTES = os.path.join(HERE, "quotes.csv")
POSTED = os.path.join(HERE, "posted.csv")
KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash"
FAILS = []
MIN_STOCK = int(os.environ.get("MIN_STOCK", "120"))
MIN_SCORE = int(os.environ.get("MIN_SCORE", "8"))
WANT = TP["want"]   # asked for; the judge keeps only the 8+/10 ones
BANNED = re.compile(r"\b(kill|suicide|die\b|dead\b|drunk|sex|hate|stupid|fat|politic|god\b|religio)", re.I)

TP = CFG["topup"]   # per head: style (tone + examples), topics {theme: what to write about}, want {theme: n}, audience
STYLE = TP["style"]
TOPIC = TP["topics"]


def pick_model():
    """If the configured model is retired, use the newest available 'flash' model instead."""
    global MODEL
    try:
        with urllib.request.urlopen(f"https://generativelanguage.googleapis.com/v1beta/models?key={KEY}&pageSize=200", timeout=60) as r:
            names = [m["name"].split("/")[-1] for m in json.loads(r.read()).get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
    except Exception as ex:  # noqa: BLE001
        print("could not list models:", ex); return False
    flash = sorted([n for n in names if "flash" in n and "lite" not in n and "preview" not in n and "exp" not in n], reverse=True)
    if flash and flash[0] != MODEL:
        print("switching model", MODEL, "->", flash[0]); MODEL = flash[0]; return True
    return False


def ask(theme, n, retry=True, attempt=1):
    prompt = (STYLE + f"\nWrite {n} brand-new, original lines {TOPIC[theme]}. One per line, no numbering, "
              "no quotation marks, no hashtags, no emoji, each under 110 characters. Do not quote real authors.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={KEY}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 1.0}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print("Gemini failed:", e.code, e.read().decode(errors="replace")[:300])
        if e.code == 404 and retry and pick_model():
            return ask(theme, n, retry=False)
        if e.code in (429, 500, 503) and attempt < 4:   # busy: wait and try again
            import time; time.sleep(20 * attempt)
            return ask(theme, n, retry, attempt + 1)
        FAILS.append(theme)
        return []
    text = "".join(p.get("text", "") for c in d.get("candidates", []) for p in c.get("content", {}).get("parts", []))
    return [re.sub(r"^[\-\*\d\.\)\s]+", "", l).strip().strip('"') for l in text.splitlines() if l.strip()]


def judge(lines):
    """Second Gemini pass: score each candidate 1-10 for wit and shareability; keep 8+."""
    if not lines:
        return []
    numbered = "\n".join(f"{i + 1}. {l}" for i, l in enumerate(lines))
    prompt = ("You are a tough social-media editor. Score each line 1-10 for how witty it is and how likely a " + TP.get("audience", "reader") + " would share it with a friend. Be harsh: generic or flat lines score 5 or less. "
              "Reply only with lines in the form 'number: score'.\n\n" + numbered)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={KEY}"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}), timeout=120) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print("Gemini judge failed, keeping none:", e.code)
        return []
    text = "".join(p.get("text", "") for c in d.get("candidates", []) for p in c.get("content", {}).get("parts", []))
    keep = []
    for m in re.finditer(r"(\d+)\s*[:\-]\s*(\d+)", text):
        i, s = int(m.group(1)) - 1, int(m.group(2))
        if 0 <= i < len(lines) and s >= MIN_SCORE:
            keep.append(lines[i])
    return keep


def ok(line, existing):
    if not (25 <= len(line) <= 120):
        return False
    if re.search(r"[#@]|[\U0001F300-\U0001FAFF]", line) or BANNED.search(line) or " - " in line[-30:]:
        return False
    low = line.lower()
    return all(difflib.SequenceMatcher(None, low, e).ratio() <= 0.75 for e in existing)


def main():
    quotes = list(csv.DictReader(open(QUOTES)))
    used = {r["quote_id"] for r in csv.DictReader(open(POSTED))} if os.path.exists(POSTED) else set()
    stock = sum(1 for q in quotes if q["id"] not in used)
    print(f"{stock} unused quotes in stock")
    if stock >= MIN_STOCK and os.environ.get("FORCE") != "1":
        print("Enough stock, no top-up needed.")
        return
    if not KEY:
        raise SystemExit("GEMINI_API_KEY is not set")
    existing = [q["text"].lower() for q in quotes]
    n = max(int(q["id"][1:]) for q in quotes)
    today = datetime.date.today().isoformat()
    added = []
    for theme, k in WANT.items():
        fresh = [l for l in ask(theme, k) if ok(l, existing)]
        for line in judge(fresh):
            if ok(line, existing):
                n += 1
                existing.append(line.lower())
                added.append({"id": f"q{n:04d}", "theme": theme, "text": line, "source": "gemini", "added": today})
    with open(QUOTES, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=["id", "theme", "text", "source", "added"]).writerows(added)
    print(f"added {len(added)} quotes (model {MODEL})")
    if len(FAILS) == len(WANT):
        raise SystemExit("Gemini failed for every theme: check GEMINI_API_KEY / model")
    # the new lines themselves are not printed: logs of this repo are public


if __name__ == "__main__":
    main()
