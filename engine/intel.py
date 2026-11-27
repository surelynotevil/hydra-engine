"""Hydra intel: pool every head's results and learn what works across channels.

Each Reel's score (per 1,000 reach) is divided by its own head's median, so a small channel counts as much
as a big one. Then the "lift" of each feature (posting daypart, weekday, music source, call-to-action style,
line length, line format) is the average relative score of Reels with that feature: 1.20 = 20% better than
that channel's usual. Heads use these lifts:
  slot.py    shortens exploration for new heads and tests the strongest dayparts first
  audio.py   tries the music source that works best (calm mood search vs trending)
  publish.py leans towards the CTA style and line length that work best
  topup.py   tells Gemini what works across Hydra and shows top lines from other channels

Writes $CONTENT_DIR/intel/intel.json (read by the engine and O.D.I.N) and intel/INTEL.md (for Amit).
Run: python3 engine/intel.py   (no secrets needed; reads only the content repo)
"""
import csv, datetime, glob, json, os, re, statistics
from collections import defaultdict

CONTENT = os.environ.get("CONTENT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "content")
OUTDIR = os.path.join(CONTENT, "intel")
MIN_POSTS_PER_HEAD = 5     # a head needs this many measured Reels before it joins the pool
MIN_REACH = 20
CONFIDENT_N = 12           # a lift counts as solid once this many Reels back it

DAYPARTS = [(0, 3, "00-03"), (3, 6, "03-06"), (6, 9, "06-09"), (9, 12, "09-12"), (12, 15, "12-15"), (15, 18, "15-18"), (18, 21, "18-21"), (21, 24, "21-24")]


def daypart(hhmm):
    try:
        h = int(hhmm.split(":")[0])
    except Exception:  # noqa: BLE001
        return ""
    return next(n for a, b, n in DAYPARTS if a <= h < b)


def cta_style(cta):
    c = (cta or "").lower()
    if not c:
        return ""
    if "tag" in c:
        return "tag someone"
    if "save" in c:
        return "save it"
    if any(w in c for w in ("send", "bhejo", "share")):
        return "send / share"
    return "other"


def music_class(src):
    s = (src or "").lower()
    if not s:
        return ""
    if s == "silent":
        return "silent"
    if s.startswith("original_sound"):
        return "trending original sound"
    return "trending music" if s.endswith(":trending") else "mood search"


def length_bucket(text):
    n = len(text or "")
    return "" if not n else "short (<55)" if n < 55 else "medium (55-85)" if n <= 85 else "long (>85)"


def formats(text):
    t = text or ""
    out = []
    if "?" in t:
        out.append("asks a question")
    if re.search(r"\d", t):
        out.append("has a number or time")
    if re.search(r'"|\b(me|main|mummy|beta|boss)\s*:', t, re.I):
        out.append("dialogue / quote")
    if re.search(r"\b(also me|bhi main|bhi dimaag)\b", t, re.I):
        out.append("me vs also-me")
    if not out:
        out.append("plain statement")
    return out


def load_head(d):
    name = os.path.basename(d)
    cfg = json.load(open(os.path.join(d, "config.json")))
    ins = os.path.join(d, "insights", "insights.csv")
    if not os.path.exists(ins):
        return name, cfg, []
    quotes = {}
    qp = os.path.join(d, "quotes.csv")
    if os.path.exists(qp):
        quotes = {q["id"]: q["text"] for q in csv.DictReader(open(qp))}
    posted = {}
    pp = os.path.join(d, "posted.csv")
    if os.path.exists(pp):
        posted = {p.get("media_id"): p for p in csv.DictReader(open(pp))}
    rows = []
    for r in csv.DictReader(open(ins)):
        try:
            reach, score = float(r.get("reach") or 0), float(r.get("score_per_1k") or "nan")
        except ValueError:
            continue
        if reach < MIN_REACH or score != score:
            continue
        p = posted.get(r.get("media_id"), {})
        text = quotes.get(r.get("quote_id"), r.get("caption_start", ""))
        try:
            wd = datetime.date.fromisoformat((r.get("posted_local") or "")[:10]).strftime("%a")
        except ValueError:
            wd = ""
        rows.append({"head": name, "score": score, "views": float(r.get("views") or 0), "reach": reach,
                     "text": text, "slot": r.get("slot") or p.get("slot", ""), "weekday": wd,
                     "music": r.get("audio_source") or p.get("audio_source") or "", "cta": p.get("cta", "")})
    return name, cfg, rows


def lifts(rows, key):
    got = defaultdict(list)
    for r in rows:
        vals = key(r)
        for v in (vals if isinstance(vals, list) else [vals]):
            if v:
                got[v].append(r["rel"])
    out = {k: {"lift": round(sum(v) / len(v), 3), "n": len(v), "solid": len(v) >= CONFIDENT_N} for k, v in got.items()}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["lift"]))


def main():
    heads = sorted(glob.glob(os.path.join(CONTENT, "heads", "*", "config.json")))
    pool, per_head, names, best_lines = [], {}, {}, []
    for cp in heads:
        name, cfg, rows = load_head(os.path.dirname(cp))
        names[name] = cfg.get("brand", name)
        per_head[name] = len(rows)
        if len(rows) < MIN_POSTS_PER_HEAD:
            continue
        med = statistics.median(r["score"] for r in rows) or 1.0
        for r in rows:
            r["rel"] = r["score"] / med
        pool += rows
        best_lines += sorted(rows, key=lambda r: -r["rel"])[:5]
    intel = {
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "reels_pooled": len(pool), "heads_pooled": sorted({r["head"] for r in pool}),
        "measured_per_head": per_head,
        "daypart": lifts(pool, lambda r: daypart(r["slot"])),
        "weekday": lifts(pool, lambda r: r["weekday"]),
        "music": lifts(pool, lambda r: music_class(r["music"])),
        "cta_style": lifts(pool, lambda r: cta_style(r["cta"])),
        "length": lifts(pool, lambda r: length_bucket(r["text"])),
        "format": lifts(pool, lambda r: formats(r["text"])),
        "top_lines": [{"head": names.get(r["head"], r["head"]), "folder": r["head"], "text": r["text"], "lift": round(r["rel"], 2)}
                      for r in sorted(best_lines, key=lambda r: -r["rel"])[:12]],
    }
    intel["takeaways"] = takeaways(intel)
    os.makedirs(OUTDIR, exist_ok=True)
    json.dump(intel, open(os.path.join(OUTDIR, "intel.json"), "w"), indent=1, ensure_ascii=False)
    open(os.path.join(OUTDIR, "INTEL.md"), "w").write(report(intel, names))
    print(f"intel: {len(pool)} Reels pooled from {len(intel['heads_pooled'])} head(s); {len(intel['takeaways'])} takeaway(s)")


def takeaways(I):
    out = []
    for key, label in [("daypart", "Posting time"), ("music", "Music"), ("cta_style", "Call to action"), ("length", "Line length"), ("format", "Line format"), ("weekday", "Weekday")]:
        solid = {k: v for k, v in I[key].items() if v["solid"]}
        if len(solid) >= 2:
            (b, bv), (w, wv) = list(solid.items())[0], list(solid.items())[-1]
            if bv["lift"] - wv["lift"] >= 0.15:
                out.append(f"{label}: '{b}' beats '{w}' by {round((bv['lift'] / max(wv['lift'], 0.01) - 1) * 100)}% ({bv['n']} vs {wv['n']} Reels)")
    return out


def report(I, names):
    L = [f"# Hydra intel\n\nUpdated {I['updated']}. {I['reels_pooled']} Reels pooled from {len(I['heads_pooled'])} channel(s). "
         "Lift = how a Reel did against its own channel's usual (1.00 = usual, 1.20 = 20% better). "
         f"Rows marked ✔ have {CONFIDENT_N}+ Reels behind them.\n"]
    L.append("## Takeaways\n")
    L += [f"- {t}" for t in I["takeaways"]] or ["- Not enough data yet. Each channel joins the pool after 5 measured Reels."]
    for key, label in [("daypart", "Posting time (local)"), ("weekday", "Weekday"), ("music", "Music source"), ("cta_style", "Call to action"), ("length", "Line length"), ("format", "Line format")]:
        if I[key]:
            L.append(f"\n## {label}\n\n| | lift | Reels |\n|---|---|---|")
            L += [f"| {k} | {v['lift']:.2f}{' ✔' if v['solid'] else ''} | {v['n']} |" for k, v in I[key].items()]
    if I["top_lines"]:
        L.append("\n## Best lines across Hydra\n")
        L += [f"- **{t['head']}** ({t['lift']:.2f}×): {t['text']}" for t in I["top_lines"]]
    L.append("\n## Measured Reels per channel\n")
    L += [f"- {names.get(k, k)}: {v}" for k, v in I["measured_per_head"].items()]
    return "\n".join(L) + "\n"


# ---------- helpers the other engine parts call ----------

def load():
    p = os.path.join(CONTENT, "intel", "intel.json")
    try:
        return json.load(open(p))
    except Exception:  # noqa: BLE001
        return {}


def solid_best(I, key, min_gap=0.10):
    """(best value, its lift) when the best solid value beats the solid average by min_gap, else None."""
    solid = {k: v for k, v in (I.get(key) or {}).items() if v.get("solid")}
    if len(solid) < 2:
        return None
    best = max(solid, key=lambda k: solid[k]["lift"])
    avg = sum(v["lift"] for v in solid.values()) / len(solid)
    return (best, solid[best]["lift"]) if solid[best]["lift"] - avg >= min_gap else None


if __name__ == "__main__":
    main()
