"""Picks today's Reel music from Instagram's own trending audio (Instagram Audio API).

GET /ig_audio with no search_query returns what's trending and authorised for third-party use.
We take licensed music first, skip tracks used in the last few days, and fall back to keyword
searches (cozy, lofi, piano...) and then trending original sounds. Returns None if nothing usable,
in which case the Reel goes out silent rather than not at all.
"""
import csv, datetime, json, os, random, urllib.error, urllib.parse, urllib.request

G = "https://graph.facebook.com/v22.0"
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import HEAD as HERE, CFG as _ALL  # noqa: E402
CFG = _ALL["audio"]
UA = {"User-Agent": "HydraEngine/1.0"}


def _get(params, token):
    url = f"{G}/ig_audio?" + urllib.parse.urlencode(dict(params, access_token=token))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            d = json.loads(r.read())
            return d.get("audio") or d.get("data") or []   # search results come wrapped in "audio"
    except urllib.error.HTTPError as e:
        print("audio search failed:", e.code, e.read().decode(errors="replace")[:200])
        return []


def recently_used(days):
    path = os.path.join(HERE, "posted.csv")
    if not os.path.exists(path):
        return set()
    cut = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    return {r["audio_id"] for r in csv.DictReader(open(path)) if r.get("audio_id") and r["posted_utc"][:10] >= cut}


def pick(ig_user_id, token, seconds=15):
    used = recently_used(CFG["avoid_repeat_days"])
    order = ["music", "original_sound"] if CFG.get("music_first", True) else ["original_sound", "music"]
    moods = [(order[0], q) for q in random.sample(CFG["fallback_searches"], len(CFG["fallback_searches"]))]
    mood_first = CFG.get("mood_first", True)
    try:
        import intel
        mu = {k: v for k, v in (intel.load().get("music") or {}).items() if v.get("solid")}
        if "trending music" in mu and "mood search" in mu:   # Hydra intel overrides the head default when the gap is clear
            gap = mu["trending music"]["lift"] - mu["mood search"]["lift"]
            if abs(gap) >= 0.15:
                mood_first = gap < 0
    except Exception:  # noqa: BLE001
        pass
    if mood_first:
        # global audience: calm mood searches first, the (country-based) trending list only as a fallback
        tries = moods + [(order[0], None), (order[1], None)]
    else:
        tries = [(order[0], None)] + moods + [(order[1], None)]
    for kind, query in tries:
        params = {"audio_type": kind, "user_id": ig_user_id}
        if query:
            params["search_query"] = query
        items = _get(params, token)
        ok = [a for a in items if a.get("audio_id") and a["audio_id"] not in used
              and (a.get("duration_in_ms") or 0) >= seconds * 1000]
        if ok:
            # trending lists come ranked: lean towards the top but don't always take #1
            a = random.choice(ok[:5])
            a["_source"] = f"{kind}:{query or 'trending'}"
            print("music:", a.get("title"), "-", a.get("display_artist") or a.get("ig_username"), f"({a['_source']})")
            return a
    print("no usable audio found; posting silent")
    return None


def configuration(audio):
    return json.dumps({"audio_id": audio["audio_id"], "audio_volume": CFG["audio_volume"], "video_volume": CFG["video_volume"]})
