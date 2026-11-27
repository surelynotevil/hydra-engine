"""Where things live, and log hygiene for a public repo.

The engine (this repo) is public; each head's content (config, card, quotes, logs, insights) lives in a
private content repo checked out next to it. HEAD_DIR points at one head's folder in that checkout.

Because Actions logs of a public repo are public, everything printed goes through a filter that hides long
numbers (account, page and media ids), tokens and e-mail addresses.
"""
import json, os, re, sys

ENGINE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
HEAD = os.path.abspath(os.environ.get("HEAD_DIR") or os.path.join(ENGINE, "example-head"))
NAME = os.path.basename(HEAD.rstrip("/"))
CFG = json.load(open(os.path.join(HEAD, "config.json")))
OUT = os.path.join(ENGINE, "out", NAME)
FONTS = os.path.join(ENGINE, "assets", "fonts")
import datetime as _dt
TZ = _dt.timezone(_dt.timedelta(minutes=int(CFG.get("utc_offset_minutes", 0))))   # the head's local time

_SECRET = re.compile(r"(access_token=|key=)[^&\s\"']+|\b\d{8,}\b|[\w.+-]+@[\w-]+\.[\w.]+|EAA[A-Za-z0-9]{20,}")


class _Redact:
    def __init__(self, s):
        self.s = s

    def write(self, t):
        return self.s.write(_SECRET.sub(lambda m: (m.group(1) or "") + "***", t))

    def flush(self):
        self.s.flush()

    def __getattr__(self, k):
        return getattr(self.s, k)


if not isinstance(sys.stdout, _Redact):
    sys.stdout = _Redact(sys.stdout)
    sys.stderr = _Redact(sys.stderr)


def mask(value):
    """Tell GitHub to hide a value in this job's logs from now on."""
    if value and os.environ.get("GITHUB_ACTIONS"):
        sys.__stdout__.write(f"::add-mask::{value}\n")


def mask_head(cfg, name):
    """Hide everything that names a head (folder, handle, brand, media address) from this job's logs."""
    for v in (name, cfg.get("instagram_username"), cfg.get("brand"), cfg.get("media_project"),
              (cfg.get("media_base") or "").replace("https://", "")):
        mask(v)


mask_head(CFG, NAME)
