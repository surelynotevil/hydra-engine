"""Reel publisher for one Hydra head (HEAD_DIR).

One run = one Reel: the next unused quote on the fixed mascot card, 15 seconds, with trending music picked
by Instagram's Audio API and attached at publish time.

Safety gates (checklist standard):
  - SOCIAL_LIVE must be "true" (repo variable) or nothing is published: the run renders and stops.
  - DRY_RUN=1 renders (and uploads, if Cloudflare keys exist) but never publishes.
  - Duplicate check: a quote already posted in the last 20 hours is never posted again.

Env: META_TOKEN, CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, SOCIAL_LIVE, DRY_RUN, SLOT, LAUNCH_DATE,
     FB_PAGE_ID + FB_LIVE=true to copy the Reel to the Facebook Page (without the music, see README).
"""
import csv, datetime, json, os, random, shutil, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import HEAD as HERE, CFG, OUT, mask  # noqa: E402
import audio, render, slot  # noqa: E402

G = "https://graph.facebook.com/v22.0"
TOKEN = os.environ.get("META_TOKEN", "")
LIVE = os.environ.get("SOCIAL_LIVE", "").lower() == "true" and CFG.get("live") is True   # master switch AND per-head switch
DRY_ASKED = os.environ.get("DRY_RUN") == "1"
DRY = DRY_ASKED or not LIVE
UA = {"User-Agent": "HydraEngine/1.0"}
QUOTES = os.path.join(HERE, "quotes.csv")
POSTED = os.path.join(HERE, "posted.csv")
PCOLS = ["posted_utc", "slot", "quote_id", "theme", "media_id", "permalink", "audio_id", "audio_title", "audio_artist", "audio_source", "cta", "promo"]
import intel  # noqa: E402
INTEL = intel.load()

CAP = CFG["captions"]   # per head: theme_cycle, hashtags {theme: [...]}, ctas [...], hashtags_per_post
THEME_CYCLE = CAP["theme_cycle"]
HASHTAGS = CAP["hashtags"]
CTAS = CAP["ctas"]


def call(method, path, params=None):
    params = dict(params or {}, access_token=TOKEN)
    url = G + path
    body = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    else:
        body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} failed {e.code}: {e.read().decode(errors='replace')[:500]}")


# ---------- quotes ----------

def load_posted():
    return list(csv.DictReader(open(POSTED))) if os.path.exists(POSTED) else []


def pick_quote(posted):
    quotes = list(csv.DictReader(open(QUOTES)))
    used = {p["quote_id"] for p in posted}
    theme = THEME_CYCLE[len(posted) % len(THEME_CYCLE)]
    best_len = intel.solid_best(INTEL, "length")   # Hydra intel: lean towards the line length that works best
    for t in [theme] + [x for x in dict.fromkeys(THEME_CYCLE) if x != theme]:
        fresh = [q for q in quotes if q["id"] not in used and q["theme"] == t]
        if fresh:
            if best_len:
                near = [q for q in fresh[:8] if intel.length_bucket(q["text"]) == best_len[0]]
                if near:
                    return near[0]
            return fresh[0]
    # everything used once: repost the best performer not used in 60 days (scores from insights)
    scores = {}
    ins = os.path.join(HERE, "insights", "insights.csv")
    if os.path.exists(ins):
        for r in csv.DictReader(open(ins)):
            if r.get("quote_id") and r.get("score_per_1k"):
                scores[r["quote_id"]] = max(scores.get(r["quote_id"], 0), float(r["score_per_1k"]))
    cut = (datetime.datetime.utcnow() - datetime.timedelta(days=60)).strftime("%Y-%m-%d")
    recent = {p["quote_id"] for p in posted if p["posted_utc"][:10] >= cut}
    pool = [q for q in quotes if q["id"] not in recent] or quotes
    pool.sort(key=lambda q: scores.get(q["id"], 0), reverse=True)
    return pool[0]


# ---------- sister-channel mention (1 in 10 Reels) ----------
PROMO_EVERY = 10


def sister():
    """The next live sister channel in the same language, rotating; None if there isn't one."""
    me = os.path.basename(HERE.rstrip("/"))
    lang = CFG.get("language", "en")
    sisters = []
    for cp in sorted(__import__("glob").glob(os.path.join(os.path.dirname(HERE.rstrip("/")), "*", "config.json"))):
        name = os.path.basename(os.path.dirname(cp))
        try:
            c = json.load(open(cp))
        except Exception:  # noqa: BLE001
            continue
        if name != me and c.get("live") is True and not c.get("paused") and c.get("language", "en") == lang and c.get("promo_line"):
            sisters.append(c)
    if not sisters:
        return None
    done = sum(1 for p in load_posted() if p.get("promo"))
    return sisters[done % len(sisters)]


def promo_comment(media_id, n_posted):
    """Every 10th Reel: comment on it pointing to a sister channel. Never fails the post."""
    if n_posted % PROMO_EVERY:
        return ""
    s = sister()
    if not s:
        return ""
    msg = f"{s['promo_line']} 👉 @{s['instagram_username']}"
    try:
        call("POST", f"/{media_id}/comments", {"message": msg})
        print("sister mention posted")
        return s["instagram_username"]
    except Exception as ex:  # noqa: BLE001
        print("sister mention failed (post is fine):", str(ex)[:200])
        return ""


def pick_cta():
    """Weight this head's CTAs by how their style (tag / send / save) performs across Hydra; keep some variety."""
    lifts = (INTEL.get("cta_style") or {})
    w = []
    for c in CTAS:
        v = lifts.get(intel.cta_style(c), {})
        w.append(max(0.3, v.get("lift", 1.0)) if v.get("solid") else 1.0)
    return random.choices(CTAS, weights=w)[0]


def caption(q):
    tags = HASHTAGS.get(q["theme"]) or sum(HASHTAGS.values(), [])
    chosen = random.sample(tags, min(CAP.get("hashtags_per_post", 5), len(tags)))
    cta = pick_cta()
    return f"{q['text']}\n\n{cta}\n\n{' '.join(chosen)}", cta


# ---------- media hosting (Cloudflare Pages, free) ----------

def upload(files):
    """Deploy this run's files to the media Pages project and return their public URLs."""
    if not (os.environ.get("CLOUDFLARE_API_TOKEN") and os.environ.get("CLOUDFLARE_ACCOUNT_ID")):
        print("No Cloudflare keys: not uploading.")
        return None
    site = os.path.join(OUT, "site")
    shutil.rmtree(site, ignore_errors=True)
    os.makedirs(os.path.join(site, "m"))
    for f in files:
        shutil.copy(f, os.path.join(site, "m", os.path.basename(f)))
    with open(os.path.join(site, "index.html"), "w") as fh:
        fh.write("<!doctype html><title>media</title>")
    proj = CFG["media_project"]
    cmd = ["npx", "--yes", "wrangler@3", "pages", "deploy", site, "--project-name", proj, "--branch", "main", "--commit-dirty=true"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and "not found" in (r.stdout + r.stderr).lower():
        subprocess.run(["npx", "--yes", "wrangler@3", "pages", "project", "create", proj, "--production-branch", "main"], check=True)
        r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("Cloudflare deploy failed: " + (r.stdout + r.stderr)[-800:])
    urls = [f"{CFG['media_base']}/m/{os.path.basename(f)}" for f in files]
    for u in urls:   # wait until the files are actually served
        for _ in range(30):
            try:
                with urllib.request.urlopen(urllib.request.Request(u, method="HEAD", headers=UA), timeout=20) as resp:
                    if resp.status == 200:
                        break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(4)
        print("uploaded", os.path.basename(u))
    return urls


# ---------- Instagram ----------

def ig_user():
    want = CFG["instagram_username"].lower()
    for p in call("GET", "/me/accounts", {"fields": "id,name,instagram_business_account{id,username}", "limit": 50}).get("data", []):
        ig = p.get("instagram_business_account") or {}
        if (ig.get("username") or "").lower() == want:
            mask(ig["id"]); mask(p["id"])
            return ig["id"], p["id"]
    raise SystemExit(f"@{want} is not linked to a Page this token can see.")


def wait_ready(cid):
    for _ in range(60):
        s = call("GET", f"/{cid}", {"fields": "status_code,status"})
        if s.get("status_code") == "FINISHED":
            return
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {s.get('status_code')}: {s.get('status')}")
        time.sleep(10)
    raise RuntimeError("container not ready after 10 minutes")


def already_up(ig, text):
    cut = (datetime.datetime.utcnow() - datetime.timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        data = call("GET", f"/{ig}/media", {"fields": "caption,timestamp", "limit": 15}).get("data", [])
    except RuntimeError as ex:
        print("duplicate check skipped:", ex)
        return False
    return any((m.get("caption") or "")[:60] == text[:60] and m["timestamp"][:19] >= cut for m in data)


def main():
    act, slot_name, day, _today, _why = slot.decide()
    if not act:
        print(f"Not a posting slot today (ramp day {day}, slot {slot_name}).")
        return
    posted = load_posted()
    q = pick_quote(posted)
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M")
    out = OUT
    os.makedirs(out, exist_ok=True)
    png = render.card(q["text"], os.path.join(out, f"pb-{stamp}.png"))
    mp4 = render.video(png, os.path.join(out, f"pb-{stamp}.mp4"))
    cover = os.path.join(out, f"pb-{stamp}.jpg")
    from PIL import Image
    Image.open(png).convert("RGB").save(cover, quality=92)
    cap, cta = caption(q)
    open(os.path.join(out, "caption.txt"), "w").write(cap)
    print(f"slot {slot_name} | quote {q['id']} ({q['theme']})")
    if not LIVE and not DRY_ASKED:
        print("SOCIAL_LIVE is not 'true': rendered only, nothing uploaded or published.")
        return
    urls = upload([mp4, cover])
    if DRY_ASKED and TOKEN:   # test run: check the account and the music lookup without publishing
        try:
            ig, _ = ig_user()
            t = audio.pick(ig, TOKEN, CFG["reel_seconds"])
            print("DRY_RUN check: account OK, music", "found: " + (t or {}).get("title", "") if t else "not found")
        except BaseException as ex:  # noqa: BLE001
            print("DRY_RUN check failed:", ex)
    if DRY or not urls or not TOKEN:
        print("DRY_RUN (or missing keys): not published.")
        return
    ig, page = ig_user()
    if already_up(ig, q["text"]):
        print("This quote is already up from the last 20 hours: skipping.")
        return
    track = audio.pick(ig, TOKEN, CFG["reel_seconds"])
    params = {"media_type": "REELS", "video_url": urls[0], "cover_url": urls[1], "caption": cap, "share_to_feed": "true"}
    try:
        c = call("POST", f"/{ig}/media", {**params, **({"audio_configuration": audio.configuration(track)} if track else {})})
    except RuntimeError as ex:
        if not track:
            raise
        print("music refused, posting without it:", str(ex)[:300])
        track = None
        c = call("POST", f"/{ig}/media", params)
    wait_ready(c["id"])
    m = call("POST", f"/{ig}/media_publish", {"creation_id": c["id"]})
    link = call("GET", f"/{m['id']}", {"fields": "permalink"}).get("permalink", "")
    print("published")
    row = {"posted_utc": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"), "slot": slot_name, "quote_id": q["id"],
           "theme": q["theme"], "media_id": m.get("id", ""), "permalink": link,
           "audio_id": (track or {}).get("audio_id", ""), "audio_title": (track or {}).get("title", ""),
           "audio_artist": (track or {}).get("display_artist") or (track or {}).get("ig_username", ""),
           "audio_source": (track or {}).get("_source", "silent"), "cta": cta,
           "promo": promo_comment(m.get("id", ""), len(load_posted()) + 1)}
    new = not os.path.exists(POSTED)
    if not new:   # older logs lack newer columns: rewrite the header once
        old = list(csv.DictReader(open(POSTED)))
        if old and set(PCOLS) - set(old[0].keys()):
            with open(POSTED, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=PCOLS); w.writeheader(); w.writerows({k: r.get(k, "") for k in PCOLS} for r in old)
    with open(POSTED, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PCOLS)
        if new:
            w.writeheader()
        w.writerow(row)
    fb_page = os.environ.get("FB_PAGE_ID") or page
    if os.environ.get("FB_LIVE", "").lower() == "true" and fb_page:
        import fb
        try:
            fb.video(fb_page, urls[0], cap)
        except Exception as ex:  # noqa: BLE001
            print("FACEBOOK FAILED (Instagram posted fine):", ex)


if __name__ == "__main__":
    main()
