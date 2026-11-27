"""Facebook Page posting for a Hydra head. Uses the Page's own token, fetched with the system-user META_TOKEN
(which needs pages_show_list + pages_manage_posts)."""
import json, os, urllib.error, urllib.parse, urllib.request

G = "https://graph.facebook.com/v22.0"
UA = {"User-Agent": "HydraEngine/1.0"}


def _req(method, path, params):
    data = urllib.parse.urlencode(params).encode() if method == "POST" else None
    url = G + path + ("" if method == "POST" else "?" + urllib.parse.urlencode(params))
    req = urllib.request.Request(url, data=data, method=method, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} failed {e.code}: {e.read().decode(errors='replace')[:400]}")


def page_token(page_id, token=None):
    d = _req("GET", "/me/accounts", {"fields": "id,access_token", "limit": 50, "access_token": token or os.environ["META_TOKEN"]})
    for p in d.get("data", []):
        if p["id"] == str(page_id):
            return p["access_token"]
    raise RuntimeError(f"Facebook Page {page_id} is not visible to this token")


def already_posted(page_id, message, hours=20):
    """True if the Page already has a post starting like `message` from the last `hours` hours."""
    import datetime
    tok = page_token(page_id)
    d = _req("GET", f"/{page_id}/posts", {"fields": "message,created_time", "limit": 15, "access_token": tok})
    cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    head = (message or "")[:60]
    for p in d.get("data", []):
        t = datetime.datetime.strptime(p["created_time"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        if t >= cut and (p.get("message") or "")[:60] == head:
            return True
    return False


def album(page_id, urls, message):
    """One feed post with every slide attached (Facebook's multi-photo post)."""
    tok = page_token(page_id)
    ids = [_req("POST", f"/{page_id}/photos", {"url": u, "published": "false", "access_token": tok})["id"] for u in urls[:10]]
    params = {"message": message, "access_token": tok}
    for i, pid in enumerate(ids):
        params[f"attached_media[{i}]"] = json.dumps({"media_fbid": pid})
    r = _req("POST", f"/{page_id}/feed", params)
    print("facebook post:", r.get("id"))
    return r


def video(page_id, url, description):
    tok = page_token(page_id)
    r = _req("POST", f"/{page_id}/videos", {"file_url": url, "description": description, "access_token": tok})
    print("facebook video:", r.get("id"))
    return r


def for_facebook(caption, link):
    """Instagram captions say 'link in bio'; on Facebook links are clickable, so put the real one in."""
    return caption.replace("link in bio", link).replace("Link in bio", link)
