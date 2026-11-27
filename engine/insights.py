"""Twice-daily analytics for one Hydra head (Instagram account and its Facebook Page).

Writes social/insights/:
  insights.csv   one row per Reel: views, reach, likes, comments, shares, saves, follows, watch time,
                 score per 1,000 reach, plus the quote and the music it used (joined from posted.csv)
  accounts.csv   one row per day: followers, posts, 7-day totals, Facebook followers and comments
  REPORT.md      plain-English summary: best Reels, book vs plant, music vs silent, best slots

Score per 1,000 reach (shared standard): like 1, comment 3, share or save 5, follow 10, profile visit 2.
Reels under 20 reach are ignored in rankings.
Env: META_TOKEN.
"""
import csv, datetime, json, os, urllib.error, urllib.parse, urllib.request
from collections import defaultdict

G = "https://graph.facebook.com/v22.0"
TOKEN = os.environ.get("META_TOKEN", "")
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import HEAD as HERE, CFG, mask, TZ  # noqa: E402
OFF = TZ.utcoffset(None)
DIR = os.path.join(HERE, "insights")
USER = CFG["instagram_username"]
UA = {"User-Agent": "HydraEngine/1.0"}
METRICS = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions", "follows", "profile_visits",
           "ig_reels_avg_watch_time"]
COLS = ["media_id", "posted_local", "slot", "theme", "quote_id", "views", "reach", "likes", "comments", "shares", "saved",
        "follows", "profile_visits", "avg_watch_s", "score_per_1k", "audio_title", "audio_artist", "audio_source",
        "updated", "caption_start", "permalink"]
ACOLS = ["date", "account", "followers", "posts", "views_7d", "reach_7d", "interactions_7d", "shares_saves_7d",
         "best_post", "best_post_views", "fb_followers", "fb_comments_7d"]
WEIGHTS = {"likes": 1, "comments": 3, "shares": 5, "saved": 5, "follows": 10, "profile_visits": 2}


def get(path, params, token=None):
    req = urllib.request.Request(f"{G}{path}?{urllib.parse.urlencode(dict(params, access_token=token or TOKEN))}", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode(errors="replace")[:300]}


def metric(mid, name):
    """One metric at a time: Meta renames metrics and one bad name fails the whole request."""
    d = get(f"/{mid}/insights", {"metric": name})
    if "error" in d:
        return None
    return ((d.get("data") or [{}])[0].get("values") or [{}])[0].get("value")


def n(v):
    try:
        return int(float(v or 0))
    except ValueError:
        return 0


def fb_comments(page_id, page_token, since):
    d = get(f"/{page_id}/posts", {"fields": "created_time,comments.summary(true).limit(0)", "limit": 50,
                                  "since": int(since.timestamp())}, page_token)
    return sum(((p.get("comments") or {}).get("summary") or {}).get("total_count", 0) for p in d.get("data", []))


def write(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def report(rows, snap):
    good = [r for r in rows if n(r["reach"]) >= 20]
    lines = [f"# @{USER} report", "", f"Updated {datetime.datetime.utcnow() + OFF:%d %b %Y %H:%M} (local)", "",
             f"Followers **{snap.get('followers', '?')}** · Reels **{snap.get('posts', '?')}** · last 7 days: "
             f"views {snap.get('views_7d', 0)}, reach {snap.get('reach_7d', 0)}, shares+saves {snap.get('shares_saves_7d', 0)}", ""]
    if not good:
        return "\n".join(lines + ["Not enough data yet (Reels need 20+ reach to count)."]) + "\n"
    lines += ["## Top 5 Reels by score per 1,000 reach", ""]
    for r in sorted(good, key=lambda r: float(r["score_per_1k"] or 0), reverse=True)[:5]:
        lines.append(f"- **{r['score_per_1k']}** · {r['views']} views · {r['caption_start']} · music: {r['audio_title'] or 'none'}")

    def avg_by(key, label):
        g = defaultdict(list)
        for r in good:
            g[r.get(key) or "?"].append(float(r["score_per_1k"] or 0))
        out = ["", f"## By {label}", ""]
        for k, v in sorted(g.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
            out.append(f"- {k}: avg score {sum(v) / len(v):.1f} over {len(v)} Reels")
        return out
    lines += avg_by("theme", "theme (book / plant / both)")
    lines += avg_by("slot", "posting slot (local time)")
    for r in good:
        r["_music"] = "silent" if (r.get("audio_source") or "silent") == "silent" else r["audio_source"].split(":")[0]
    lines += avg_by("_music", "audio type")
    return "\n".join(lines) + "\n"


def main():
    if not TOKEN:
        raise SystemExit("META_TOKEN is not set")
    os.makedirs(DIR, exist_ok=True)
    pages = get("/me/accounts", {"fields": "id,access_token,followers_count,fan_count,instagram_business_account{id,username,followers_count,media_count}",
                                 "limit": 50}).get("data", [])
    page = next((p for p in pages if ((p.get("instagram_business_account") or {}).get("username") or "").lower() == USER), None)
    if not page:
        raise SystemExit(f"@{USER} is not linked to a Page this token can see yet")
    ig = page["instagram_business_account"]
    mask(ig["id"]); mask(page["id"]); mask(page.get("access_token"))
    posted = {r["media_id"]: r for r in csv.DictReader(open(os.path.join(HERE, "posted.csv")))} \
        if os.path.exists(os.path.join(HERE, "posted.csv")) else {}
    path = os.path.join(DIR, "insights.csv")
    rows = {r["media_id"]: r for r in csv.DictReader(open(path))} if os.path.exists(path) else {}
    now = datetime.datetime.utcnow()
    media = get(f"/{ig['id']}/media", {"fields": "id,caption,timestamp,permalink,media_product_type", "limit": 50})
    for m in media.get("data", []):
        t = datetime.datetime.strptime(m["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")
        if m["id"] in rows and now - t > datetime.timedelta(days=7) and now.hour < 12:
            continue   # older Reels: refresh only on the evening run, to stay inside Meta's call limits
        v = {k: metric(m["id"], k) for k in METRICS}
        reach = n(v["reach"])
        score = round(1000 * sum(w * n(v[k]) for k, w in WEIGHTS.items()) / reach, 1) if reach else ""
        p = posted.get(m["id"], {})
        rows[m["id"]] = {
            "media_id": m["id"], "posted_local": (t + OFF).strftime("%Y-%m-%d %H:%M"),
            "slot": p.get("slot", ""), "theme": p.get("theme", ""), "quote_id": p.get("quote_id", ""),
            **{k: n(v[k]) for k in ["views", "reach", "likes", "comments", "shares", "saved", "follows", "profile_visits"]},
            "avg_watch_s": round(n(v["ig_reels_avg_watch_time"]) / 1000, 1), "score_per_1k": score,
            "audio_title": p.get("audio_title", ""), "audio_artist": p.get("audio_artist", ""), "audio_source": p.get("audio_source", ""),
            "updated": now.strftime("%Y-%m-%d %H:%M"), "caption_start": (m.get("caption") or "").split("\n")[0][:80],
            "permalink": m.get("permalink", ""),
        }
    ordered = sorted(rows.values(), key=lambda r: r["posted_local"], reverse=True)
    write(path, COLS, ordered)
    week = (now + OFF - datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    recent = [r for r in ordered if r["posted_local"] >= week]
    best = max(recent, key=lambda r: n(r["views"]), default=None)
    today = (now + OFF).date().isoformat()
    snap = {"date": today, "account": USER, "followers": ig.get("followers_count", ""), "posts": ig.get("media_count", ""),
            "views_7d": sum(n(r["views"]) for r in recent), "reach_7d": sum(n(r["reach"]) for r in recent),
            "interactions_7d": sum(n(r["likes"]) + n(r["comments"]) + n(r["shares"]) + n(r["saved"]) for r in recent),
            "shares_saves_7d": sum(n(r["shares"]) + n(r["saved"]) for r in recent),
            "best_post": (best or {}).get("caption_start", ""), "best_post_views": n(best["views"]) if best else "",
            "fb_followers": page.get("followers_count") or page.get("fan_count") or "",
            "fb_comments_7d": fb_comments(page["id"], page["access_token"], now - datetime.timedelta(days=7))}
    apath = os.path.join(DIR, "accounts.csv")
    old = [r for r in csv.DictReader(open(apath))] if os.path.exists(apath) else []
    write(apath, ACOLS, [r for r in old if r["date"] != today] + [snap])
    open(os.path.join(DIR, "REPORT.md"), "w").write(report(ordered, snap))
    print(json.dumps(snap))


if __name__ == "__main__":
    main()
