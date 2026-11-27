"""Runs one job for every Hydra head, so a time slot costs the same whether there is 1 head or 20.

  python engine/run.py check    -> prints whether any head posts in this slot (and sets active=true/false)
  python engine/run.py post     -> posts for every head whose plan includes this slot
  python engine/run.py insights -> pulls insights for every live head
  python engine/run.py topup    -> tops up quotes for every head

Heads are the folders under $CONTENT_DIR/heads that contain a config.json. A head with "paused": true is
skipped. One head failing never stops the others; the job fails at the end if any head failed.
"""
import glob, json, os, subprocess, sys

ENGINE = os.path.dirname(os.path.abspath(__file__))
CONTENT = os.path.abspath(os.environ.get("CONTENT_DIR") or os.path.join(ENGINE, "..", "content"))


def heads():
    out = []
    for cfg in sorted(glob.glob(os.path.join(CONTENT, "heads", "*", "config.json"))):
        c = json.load(open(cfg))
        if not c.get("paused"):
            out.append(os.path.dirname(cfg))
    return out


def run(script, head, extra=None):
    env = dict(os.environ, HEAD_DIR=head, **(extra or {}))
    return subprocess.run([sys.executable, os.path.join(ENGINE, script)], env=env, capture_output=True, text=True)


def main(cmd):
    hs = heads()
    import paths
    for h in hs:   # hide head names before anything is printed
        paths.mask_head(json.load(open(os.path.join(h, "config.json"))), os.path.basename(h))
    if not hs:
        print("no heads found")
        return 0
    if cmd == "check":
        active = []
        for h in hs:
            r = run("slot.py", h, {"GITHUB_OUTPUT": "", "WRITE_PLAN": "1"})
            if "-> POST" in r.stdout:
                active.append(os.path.basename(h))
        print("heads posting in this slot:", len(active))
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(f"active={'true' if active else 'false'}\n")
        return 0
    script = {"post": "publish.py", "insights": "insights.py", "topup": "topup.py"}[cmd]
    failed = []
    for i, h in enumerate(hs, 1):
        name = os.path.basename(h)
        r = run(script, h)
        print(f"===== head {i} =====")
        sys.stdout.write(r.stdout)
        sys.stdout.write(r.stderr[-2000:] if r.returncode else "")
        if r.returncode:
            failed.append(name)
    if failed:
        print("FAILED:", len(failed), "head(s)")
        return 1
    return 0


if __name__ == "__main__":
    sys.path.insert(0, ENGINE)
    os.environ.setdefault("HEAD_DIR", next(iter(heads()), ENGINE))   # so paths' log filter loads here too
    import paths  # noqa: F401  (log redaction for this process)
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "check"))
