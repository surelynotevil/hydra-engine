"""Decides whether this run posts, and tests posting times to find the best ones.

There are 10 candidate slots spread around the clock (config.json -> slots_local) so a global audience
is covered. Each day posts_per_day of them are used (social/schedule.csv):

  Explore (first 14 days, or until 6+ slots have 3+ measured Reels): alternate days use the even and the
  odd slots, so every slot gets tried every other day and each day's posts are spread ~5 hours apart.
  Learn (after that): the 3 best slots by average score per 1,000 reach (insights.csv, Reels with 20+
  reach) always post, and the other 2 rotate through the remaining slots so new times keep being tested.

No third-party packages, so the workflow can check before installing anything.
Head config: launch_date (day 1). Env: CRON (the schedule that fired, from GitHub), SLOT (HH:MM override), FORCE=1.
Writes active=true/false and slot=HH:MM to $GITHUB_OUTPUT when present.
"""
import csv, datetime, json, os
from collections import defaultdict

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import HEAD as HERE, CFG, TZ  # noqa: E402
LOCAL = TZ   # local time from the head config (utc_offset_minutes)
SLOTS = CFG["slots_local"]
EXPLORE_DAYS, MIN_POINTS, MIN_SLOTS_KNOWN, BEST_KEEP = 14, 3, 6, 3


def now_local():
    return datetime.datetime.now(LOCAL)


def ramp_day(today=None):
    today = today or now_local().date()
    launch = CFG.get("launch_date") or os.environ.get("LAUNCH_DATE") or ""
    return (today - datetime.date.fromisoformat(launch)).days + 1 if launch else 1


def posts_per_day(day):
    for r in csv.DictReader(open(os.path.join(HERE, "schedule.csv"))):
        lo = int(r["day_from"]); hi = int(r["day_to"]) if r["day_to"] else 10 ** 6
        if lo <= day <= hi:
            return int(r["posts_per_day"])
    return 0


def slot_scores():
    """Average score per 1,000 reach for each slot, from Reels with 20+ reach."""
    path = os.path.join(HERE, "insights", "insights.csv")
    got = defaultdict(list)
    if os.path.exists(path):
        for r in csv.DictReader(open(path)):
            try:
                if r.get("slot") in SLOTS and float(r.get("reach") or 0) >= 20 and r.get("score_per_1k"):
                    got[r["slot"]].append(float(r["score_per_1k"]))
            except ValueError:
                pass
    return {s: (sum(v) / len(v), len(v)) for s, v in got.items()}


def _intel():
    try:
        import intel
        return intel.load()
    except Exception:  # noqa: BLE001
        return {}


def plan(day=None):
    """The slots that post today, and why."""
    day = day or ramp_day()
    I = _intel()
    dp = {k: v for k, v in (I.get("daypart") or {}).items() if v.get("solid")}
    explore_days = 7 if len(dp) >= 4 else EXPLORE_DAYS   # Hydra already knows which times work: learn faster
    lift = lambda s: dp.get(_daypart(s), {}).get("lift", 1.0)
    n = min(posts_per_day(day), len(SLOTS))
    if n <= 0:
        return [], "no posts scheduled"
    scores = slot_scores()
    known = {s: v for s, v in scores.items() if v[1] >= MIN_POINTS}
    if day <= explore_days or len(known) < MIN_SLOTS_KNOWN:
        start = 0 if day % 2 else 1
        picks = [SLOTS[(start + 2 * i) % len(SLOTS)] for i in range(n)]
        if n > len(SLOTS) // 2:   # more posts than half the slots: fill from the other set
            picks += [s for s in SLOTS if s not in picks][: n - len(picks)]
        return sorted(set(picks)), "explore: testing every time slot evenly"
    best = sorted(known, key=lambda s: known[s][0], reverse=True)[: min(BEST_KEEP, n)]
    rest = [s for s in SLOTS if s not in best]
    k = n - len(best)
    if dp and k:   # one test slot from the strongest Hydra daypart, the rest rotate
        strong = sorted(rest, key=lift, reverse=True)[:2]
        first = strong[day % len(strong)]
        rest2 = [s for s in rest if s != first]
        picks = best + [first] + [rest2[(day * k + i) % len(rest2)] for i in range(k - 1)]
    else:
        picks = best + [rest[(day * k + i) % len(rest)] for i in range(k)]
    return sorted(set(picks)), "learn: best " + ", ".join(best) + " + testing others"


def _daypart(hhmm):
    h = int(hhmm.split(":")[0])
    return f"{h // 3 * 3:02d}-{h // 3 * 3 + 3:02d}"


def cron_to_local(cron):
    """'41 0 * * *' (UTC) -> '06:11' (local)."""
    m, h = cron.split()[:2]
    t = datetime.datetime(2000, 1, 1, int(h), int(m), tzinfo=datetime.timezone.utc).astimezone(LOCAL)
    return t.strftime("%H:%M")


def current_slot():
    """The slot this run is for: the head slot within 20 minutes of the time the cron fired (or of now)."""
    if (os.environ.get("SLOT") or "").strip():
        return os.environ["SLOT"].strip()
    if (os.environ.get("CRON") or "").strip():
        hh, mm = map(int, cron_to_local(os.environ["CRON"]).split(":"))
        n = now_local().replace(hour=hh, minute=mm)
        window = 20
    else:
        n = now_local()
        window = 50
    best = None
    for hhmm in SLOTS:
        h, m = map(int, hhmm.split(":"))
        t = n.replace(hour=h, minute=m, second=0, microsecond=0)
        diff = min(abs((n - t).total_seconds()), 86400 - abs((n - t).total_seconds())) / 60
        if diff <= window and (best is None or diff < best[0]):
            best = (diff, hhmm)
    return best[1] if best else ""


def decide():
    day = ramp_day()
    s = current_slot()
    today, why = plan(day)
    act = os.environ.get("FORCE") == "1" or s in today
    return act, s or "manual", day, today, why


def write_plan():
    """plan.json: today's and tomorrow's posting slots (local times), for the dashboard's next-post timer.
    Only rewritten when the plan changes, so it is committed about once a day."""
    today = now_local().date()
    days = {}
    for k in (0, 1):
        d = today + datetime.timedelta(days=k)
        days[d.isoformat()] = plan(ramp_day(d))[0]
    out = {"utc_offset_minutes": int(CFG.get("utc_offset_minutes", 0)), "days": days, "why": plan(ramp_day(today))[1]}
    path = os.path.join(HERE, "plan.json")
    try:
        if json.load(open(path)) == out:
            return
    except Exception:  # noqa: BLE001
        pass
    json.dump(out, open(path, "w"), indent=1)


if __name__ == "__main__":
    if os.environ.get("WRITE_PLAN") == "1":
        write_plan()
    act, s, day, today, why = decide()
    print(f"day {day}, slot {s}, today's slots {today} ({why}) -> {'POST' if act else 'skip'}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"active={'true' if act else 'false'}\nslot={s}\n")
