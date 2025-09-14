# scripts/pull_bein_6to9.py
# -*- coding: utf-8 -*-
"""
يسحب beIN SPORTS 6,7,8,9 من المصدر (daddylive-events.m3u8)
ويحدّث ملف الوجهة bein.m3u بنفس الهيكلية.
"""

import os, re, sys, base64, requests
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Dict, Optional

# ===== إعدادات =====
SOURCE_URL = "https://raw.githubusercontent.com/DisabledAbel/daddylivehd-m3u/f582ae100c91adf8c8db905a8f97beb42f369a0b/daddylive-events.m3u8"
DEST_RAW_URL = "https://raw.githubusercontent.com/a7shk1/m3u-broadcast/refs/heads/main/bein.m3u"

GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO    = os.getenv("GITHUB_REPO", "a7shk1/m3u-broadcast")
GITHUB_BRANCH  = os.getenv("GITHUB_BRANCH", "main")
DEST_REPO_PATH = os.getenv("DEST_REPO_PATH", "bein.m3u")
COMMIT_MESSAGE = "chore: update bein.m3u (beIN SPORTS 6-9)"

OUTPUT_LOCAL_PATH = "./out/bein.m3u"

TIMEOUT = 25

# القنوات المطلوبة
WANTED = ["beIN SPORTS 6", "beIN SPORTS 7", "beIN SPORTS 8", "beIN SPORTS 9"]

ALIASES: Dict[str, List[re.Pattern]] = {
    "beIN SPORTS 6": [re.compile(r"bein\s*sports?\s*6", re.I)],
    "beIN SPORTS 7": [re.compile(r"bein\s*sports?\s*7", re.I)],
    "beIN SPORTS 8": [re.compile(r"bein\s*sports?\s*8", re.I)],
    "beIN SPORTS 9": [re.compile(r"bein\s*sports?\s*9", re.I)],
}

def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def parse_pairs(m3u_text: str) -> List[Tuple[str, Optional[str]]]:
    lines = [ln.strip() for ln in m3u_text.splitlines()]
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            url = None
            if i + 1 < len(lines) and lines[i+1] and not lines[i+1].startswith("#"):
                url = lines[i+1]
            out.append((lines[i], url))
            i += 2
        else:
            i += 1
    return out

def find_match(extinf: str, patterns: List[re.Pattern]) -> bool:
    return any(p.search(extinf) for p in patterns)

def pick(pairs: List[Tuple[str, Optional[str]]]) -> Dict[str, Tuple[str, Optional[str]]]:
    picked = {}
    for extinf, url in pairs:
        for n in WANTED:
            if n in picked: continue
            if find_match(extinf, ALIASES[n]):
                picked[n] = (f"#EXTINF:-1,{n}", url)
    return picked

def upsert_github(repo, branch, path, content_bytes, message, token):
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    sha = None
    res = requests.get(api, headers=headers, params={"ref": branch}, timeout=TIMEOUT)
    if res.status_code == 200:
        sha = res.json().get("sha")
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode(),
        "branch": branch,
    }
    if sha: payload["sha"] = sha
    put = requests.put(api, headers=headers, json=payload, timeout=TIMEOUT)
    if put.status_code not in (200,201):
        raise RuntimeError(f"GitHub PUT failed {put.status_code} {put.text}")
    return put.json()

def render(dest_text: str, picked: Dict[str, Tuple[str, Optional[str]]]) -> str:
    lines = [ln.rstrip("\n") for ln in dest_text.splitlines()]
    if not lines or not lines[0].upper().startswith("#EXTM3U"):
        lines.insert(0, "#EXTM3U")
    # add updated timestamp
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    if len(lines) > 1 and lines[1].startswith("# UPDATED:"):
        lines[1] = f"# UPDATED: {ts}"
    else:
        lines.insert(1, f"# UPDATED: {ts}")

    # clear old wanted
    lines = [ln for ln in lines if not any(find_match(ln, ALIASES[n]) for n in WANTED)]
    # append fresh in order
    for n in WANTED:
        if n in picked:
            extinf, url = picked[n]
            lines.append(extinf)
            if url: lines.append(url)
    return "\n".join(lines) + "\n"

def main():
    src = fetch_text(SOURCE_URL)
    dest = fetch_text(DEST_RAW_URL)
    pairs = parse_pairs(src)
    picked = pick(pairs)
    print("[i] Picked:", picked.keys())
    updated = render(dest, picked)

    if GITHUB_TOKEN:
        upsert_github(GITHUB_REPO, GITHUB_BRANCH, DEST_REPO_PATH,
                      updated.encode(), COMMIT_MESSAGE, GITHUB_TOKEN)
        print("[✓] Updated on GitHub:", DEST_REPO_PATH)
    else:
        Path(OUTPUT_LOCAL_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(OUTPUT_LOCAL_PATH).write_text(updated, encoding="utf-8")
        print("[i] Wrote locally:", OUTPUT_LOCAL_PATH)

if __name__ == "__main__":
    main()
