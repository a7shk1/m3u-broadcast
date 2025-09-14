# -*- coding: utf-8 -*-
"""
يسحب القنوات MATCH! FOOTBALL 1/2/3 RUSSIA من مصدر daddylive-events.m3u8
ويحدّث ملف generalsports.m3u في ريبو a7shk1/m3u-broadcast:
- يستبدل الموجود (EXTINF + URL الذي يليه) بنفس المكان
- وإن لم يوجد يضيفه في النهاية بترتيب 1 ثم 2 ثم 3

تشغيل محلي: يكتب ملف نهائي على القرص (OUTPUT_LOCAL_PATH)
تشغيل مع GITHUB_TOKEN: يرفع مباشرة عبر GitHub Contents API إلى الفرع المحدد
"""

import os
import re
import sys
import base64
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import requests

# ===== الإعدادات =====
SOURCE_URL = os.getenv(
    "SOURCE_URL",
    "https://raw.githubusercontent.com/DisabledAbel/daddylivehd-m3u/f582ae100c91adf8c8db905a8f97beb42f369a0b/daddylive-events.m3u8"
)

DEST_RAW_URL = os.getenv(
    "DEST_RAW_URL",
    "https://raw.githubusercontent.com/a7shk1/m3u-broadcast/refs/heads/main/generalsports.m3u"
)

# لرفع مباشر على GitHub (اختياري):
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "").strip()    # يحتاج repo scope
GITHUB_REPO    = os.getenv("GITHUB_REPO", "a7shk1/m3u-broadcast")
GITHUB_BRANCH  = os.getenv("GITHUB_BRANCH", "main")
DEST_REPO_PATH = os.getenv("DEST_REPO_PATH", "generalsports.m3u")
COMMIT_MESSAGE = os.getenv("COMMIT_MESSAGE", "chore: update Match! Football 1/2/3 from daddylive")

# لو ماكو توكن، اكتب محليًا:
OUTPUT_LOCAL_PATH = os.getenv("OUTPUT_LOCAL_PATH", "./out/generalsports.m3u")

TIMEOUT = 25
VERIFY_SSL = True

# القنوات المطلوبة بالترتيب
WANTED = [
    "MATCH! FOOTBALL 1 RUSSIA",
    "MATCH! FOOTBALL 2 RUSSIA",
    "MATCH! FOOTBALL 3 RUSSIA",
]

# ===== Aliases للمصدر (EXTINF في daddylive) =====
SRC_PATTERNS: Dict[str, List[re.Pattern]] = {
    "MATCH! FOOTBALL 1 RUSSIA": [re.compile(r"match!?[\s\-]*football\s*1\s*russia", re.I)],
    "MATCH! FOOTBALL 2 RUSSIA": [re.compile(r"match!?[\s\-]*football\s*2\s*russia", re.I)],
    "MATCH! FOOTBALL 3 RUSSIA": [re.compile(r"match!?[\s\-]*football\s*3\s*russia", re.I)],
}

# ===== Aliases للوجهة (قد تكون Futbol أو Football أو بدون Russia) =====
DEST_PATTERNS: Dict[str, List[re.Pattern]] = {
    # نبحث عن أي EXTINF يمثل نفس القناة بصيغ مختلفة
    "MATCH! FOOTBALL 1 RUSSIA": [
        re.compile(r"match!?[\s\-]*(?:football|futbol)\s*1(?:\s*russia)?", re.I),
    ],
    "MATCH! FOOTBALL 2 RUSSIA": [
        re.compile(r"match!?[\s\-]*(?:football|futbol)\s*2(?:\s*russia)?", re.I),
    ],
    "MATCH! FOOTBALL 3 RUSSIA": [
        re.compile(r"match!?[\s\-]*(?:football|futbol)\s*3(?:\s*russia)?", re.I),
    ],
}

# ===== أدوات =====
def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, verify=VERIFY_SSL)
    r.raise_for_status()
    return r.text

def parse_m3u_pairs(m3u_text: str) -> List[Tuple[str, Optional[str]]]:
    """
    يحوّل ملف m3u(m3u8) إلى أزواج (extinf_line, url_line_or_None)
    يربط كل #EXTINF بالرابط التالي إن وجد (غير تعليقي).
    """
    lines = [ln.rstrip("\n") for ln in m3u_text.splitlines()]
    out: List[Tuple[str, Optional[str]]] = []
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("#EXTINF"):
            url = None
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if nxt and not nxt.startswith("#"):
                    url = nxt
            out.append((ln, url))
            i += 2
            continue
        i += 1
    return out

def pick_from_source(pairs: List[Tuple[str, Optional[str]]]) -> Dict[str, Tuple[str, Optional[str]]]:
    picked: Dict[str, Tuple[str, Optional[str]]] = {}
    for extinf, url in pairs:
        for key in WANTED:
            if key in picked:
                continue
            for pat in SRC_PATTERNS[key]:
                if pat.search(extinf):
                    picked[key] = (extinf, url)
                    break
    return picked

def render_updated(dest_text: str, picked: Dict[str, Tuple[str, Optional[str]]]) -> str:
    """
    يستبدل إن وُجدت، ويضيف الناقص بترتيب WANTED.
    يحافظ على #EXTM3U أعلى الملف.
    """
    lines = [ln.rstrip("\n") for ln in dest_text.splitlines()]
    if not lines or not lines[0].strip().upper().startswith("#EXTM3U"):
        lines = ["#EXTM3U"] + lines

    # مواقع العناصر الموجودة
    idx_to_key: Dict[int, str] = {}
    for i, ln in enumerate(lines):
        if not ln.strip().startswith("#EXTINF"):
            continue
        for key in WANTED:
            for pat in DEST_PATTERNS[key]:
                if pat.search(ln):
                    idx_to_key[i] = key
                    break

    used = set()
    i = 0
    out: List[str] = []
    while i < len(lines):
        if i in idx_to_key:
            key = idx_to_key[i]
            pair = picked.get(key)
            if pair:
                ext, url = pair
                out.append(ext)
                if url:
                    out.append(url)
                used.add(key)
                # تخطي الرابط القديم إن كان السطر التالي URL
                if i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].strip().startswith("#"):
                    i += 2
                else:
                    i += 1
                continue
        out.append(lines[i])
        i += 1

    # أضف الناقص في النهاية
    for key in WANTED:
        if key in used:
            continue
        pair = picked.get(key)
        if not pair:
            continue
        ext, url = pair
        if out and out[-1].strip():
            out.append("")
        out.append(f"# --- {key} ---")
        out.append(ext)
        if url:
            out.append(url)

    while out and not out[-1].strip():
        out.pop()

    return "\n".join(out) + "\n"

def upsert_github_file(repo: str, branch: str, path_in_repo: str, content_bytes: bytes, message: str, token: str):
    base = "https://api.github.com"
    url = f"{base}/repos/{repo}/contents/{path_in_repo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    sha = None
    res_get = requests.get(url, headers=headers, params={"ref": branch}, timeout=TIMEOUT)
    if res_get.status_code == 200:
        sha = res_get.json().get("sha")

    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    res_put = requests.put(url, headers=headers, json=payload, timeout=TIMEOUT)
    if res_put.status_code not in (200, 201):
        raise RuntimeError(f"GitHub PUT failed: {res_put.status_code} {res_put.text}")
    return res_put.json()

def main():
    # 1) المصدر والوجهة
    src_text = fetch_text(SOURCE_URL)
    dest_text = fetch_text(DEST_RAW_URL)

    # 2) اختَر القنوات من المصدر
    pairs = parse_m3u_pairs(src_text)
    picked = pick_from_source(pairs)

    print("[i] Picked from source:")
    for k in WANTED:
        print(f"  {'✓' if k in picked else 'x'} {k}")

    # 3) ركّب نسخة الوجهة المحدّثة
    updated = render_updated(dest_text, picked)

    # 4) اكتب
    token = GITHUB_TOKEN
    if token:
        print(f"[i] Updating GitHub: {GITHUB_REPO}@{GITHUB_BRANCH}:{DEST_REPO_PATH}")
        res = upsert_github_file(
            repo=GITHUB_REPO,
            branch=GITHUB_BRANCH,
            path_in_repo=DEST_REPO_PATH,
            content_bytes=updated.encode("utf-8"),
            message=COMMIT_MESSAGE,
            token=token,
        )
        print("[✓] Updated:", res.get("content", {}).get("path"))
    else:
        p = Path(OUTPUT_LOCAL_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(updated, encoding="utf-8")
        print("[i] Wrote locally to:", p.resolve())

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[x] Error:", e)
        sys.exit(1)
