# scripts/update_bein_urls.py
# -*- coding: utf-8 -*-
"""
يجلب روابط beIN 6-9 من المصدر (daddylive-events.m3u8)
ويحدث bein.m3u بإبقاء الاسماء مثل ما هي (#EXTINF:-1,beIN SPORTS 6 ...).
"""

import os, re, base64, requests
from pathlib import Path

SOURCE_URL = "https://raw.githubusercontent.com/DisabledAbel/daddylivehd-m3u/f582ae100c91adf8c8db905a8f97beb42f369a0b/daddylive-events.m3u8"
DEST_RAW_URL = "https://raw.githubusercontent.com/a7shk1/m3u-broadcast/refs/heads/main/bein.m3u"

GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO    = "a7shk1/m3u-broadcast"
GITHUB_BRANCH  = "main"
DEST_REPO_PATH = "bein.m3u"
COMMIT_MESSAGE = "chore: update bein.m3u (replace 6-9 URLs)"

OUTPUT_LOCAL_PATH = "./out/bein.m3u"
TIMEOUT = 25

# mapping: الاسم في الملف -> regex بالـ source
MAP = {
    "beIN SPORTS 6": re.compile(r"BEIN\s+SPORTS.*6", re.I),
    "beIN SPORTS 7": re.compile(r"BEIN\s+SPORTS.*7", re.I),
    "beIN SPORTS 8": re.compile(r"BEIN\s+SPORTS.*8", re.I),
    "beIN SPORTS 9": re.compile(r"BEIN\s+SPORTS.*9", re.I),
}

def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def parse_pairs(m3u: str):
    lines = [ln.strip() for ln in m3u.splitlines() if ln.strip()]
    out = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            url = lines[i+1] if i+1 < len(lines) and not lines[i+1].startswith("#") else None
            out.append((lines[i], url))
            i += 2
        else:
            i += 1
    return out

def pick_urls(src_text: str):
    pairs = parse_pairs(src_text)
    picked = {}
    for ext, url in pairs:
        for name, pat in MAP.items():
            if name in picked: continue
            if pat.search(ext):
                picked[name] = url
    return picked

def update_dest(dest_text: str, urls: dict):
    lines = dest_text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF") and any(name in line for name in MAP.keys()):
            out.append(line)  # keep EXTINF as is
            if i+1 < len(lines) and not lines[i+1].startswith("#"):
                channel = None
                for name in MAP:
                    if name in line:
                        channel = name
                        break
                if channel and channel in urls:
                    out.append(urls[channel])  # replace URL
                else:
                    out.append(lines[i+1])     # keep old
                i += 2
                continue
        out.append(line)
        i += 1
    return "\n".join(out) + "\n"

def upsert_github(path: str, content: str):
    import json
    api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    sha = None
    res = requests.get(api, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=TIMEOUT)
    if res.status_code == 200:
        sha = res.json().get("sha")
    payload = {
        "message": COMMIT_MESSAGE,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": GITHUB_BRANCH,
    }
    if sha: payload["sha"] = sha
    put = requests.put(api, headers=headers, json=payload, timeout=TIMEOUT)
    if put.status_code not in (200,201):
        raise RuntimeError(f"GitHub PUT failed {put.status_code}: {put.text}")

def main():
    src = fetch_text(SOURCE_URL)
    dest = fetch_text(DEST_RAW_URL)
    urls = pick_urls(src)
    print("[i] Picked URLs:", urls)
    updated = update_dest(dest, urls)
    if GITHUB_TOKEN:
        upsert_github(DEST_REPO_PATH, updated)
        print("[✓] Updated bein.m3u on GitHub")
    else:
        Path(OUTPUT_LOCAL_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(OUTPUT_LOCAL_PATH).write_text(updated, encoding="utf-8")
        print("[i] Written locally:", OUTPUT_LOCAL_PATH)

if __name__ == "__main__":
    main()
