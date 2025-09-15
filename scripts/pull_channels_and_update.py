# scripts/pull_channels_and_update.py
# -*- coding: utf-8 -*-
"""
يسحب روابط قنوات محددة من RAW مصدر (ALL.m3u)
ويقوم بتحديث ملف وجهة (premierleague.m3u) باستبدال **سطر الرابط فقط**
الذي يلي #EXTINF لنفس القناة، مع الحفاظ على مكانها ونص الـEXTINF كما هو.
لا يضيف قنوات جديدة إن لم توجد في الملف الهدف.

القنوات:
  Match! Futbol 1, Match! Futbol 2, Match! Futbol 3,
  TNT 1, TNT 2,
  Sky Sports Main Event UK, Sky Sports Premier League UK
"""

import os
import re
import sys
import base64
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import requests

# ===== إعدادات =====

SOURCE_URL = os.getenv(
    "SOURCE_URL",
    "https://raw.githubusercontent.com/DisabledAbel/daddylivehd-m3u/f582ae100c91adf8c8db905a8f97beb42f369a0b/daddylive-events.m3u8"
)

# الهدف صار premierleague.m3u
DEST_RAW_URL = os.getenv(
    "DEST_RAW_URL",
    "https://raw.githubusercontent.com/a7shk1/m3u-broadcast/refs/heads/main/premierleague.m3u"
)

# للتحديث المباشر على GitHub (اختياري):
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "").strip()  # repo contents scope
GITHUB_REPO    = os.getenv("GITHUB_REPO", "a7shk1/m3u-broadcast")
GITHUB_BRANCH  = os.getenv("GITHUB_BRANCH", "main")
DEST_REPO_PATH = os.getenv("DEST_REPO_PATH", "premierleague.m3u")
COMMIT_MESSAGE = os.getenv("COMMIT_MESSAGE", "chore: update premierleague URLs (Match!/TNT/Sky)")

# للكتابة محليًا إن ماكو توكن:
OUTPUT_LOCAL_PATH = os.getenv("OUTPUT_LOCAL_PATH", "./out/premierleague.m3u")

TIMEOUT = 25
VERIFY_SSL = True

# القنوات المطلوبة (بالترتيب لا يؤثر هنا لأننا لا نضيف)
WANTED_CHANNELS = [
    "Match! Futbol 1",
    "Match! Futbol 2",
    "Match! Futbol 3",
    "TNT 1",
    "TNT 2",
    "Sky Sports Main Event UK",
    "Sky Sports Premier League UK",
]

# Aliases/أنماط مطابقة مرنة لسطر EXTINF
ALIASES: Dict[str, List[re.Pattern]] = {
    "Match! Futbol 1": [re.compile(r"match!?\.?\s*futbol\s*1", re.I)],
    "Match! Futbol 2": [re.compile(r"match!?\.?\s*futbol\s*2", re.I)],
    "Match! Futbol 3": [re.compile(r"match!?\.?\s*futbol\s*3", re.I)],
    "TNT 1":            [re.compile(r"\btnt\s*(sports)?\s*1\b", re.I)],
    "TNT 2":            [re.compile(r"\btnt\s*(sports)?\s*2\b", re.I)],
    "Sky Sports Main Event UK":     [re.compile(r"sky\s*sports\s*main\s*event", re.I)],
    "Sky Sports Premier League UK": [re.compile(r"sky\s*sports\s*premier\s*league", re.I)],
}

# ===== وظائف مساعدة =====

def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, verify=VERIFY_SSL)
    r.raise_for_status()
    return r.text

def parse_m3u_pairs(m3u_text: str) -> List[Tuple[str, Optional[str]]]:
    """يحّول ملف m3u إلى [(#EXTINF..., url_or_None), ...]"""
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

def find_first_match(extinf: str, patterns: List[re.Pattern]) -> bool:
    return any(p.search(extinf) for p in patterns)

def pick_wanted(source_pairs: List[Tuple[str, Optional[str]]]) -> Dict[str, str]:
    """
    يرجّع dict: wanted_name -> stream_url
    يلتقط أول تطابق لكل قناة مطلوبة من المصدر. يحتفظ بالرابط فقط.
    """
    picked: Dict[str, str] = {}
    for extinf, url in source_pairs:
        if not url:
            continue
        for official_name in WANTED_CHANNELS:
            if official_name in picked:
                continue
            pats = ALIASES.get(official_name, [])
            if find_first_match(extinf, pats):
                picked[official_name] = url
    return picked

def upsert_github_file(repo: str, branch: str, path_in_repo: str, content_bytes: bytes, message: str, token: str):
    base = "https://api.github.com"
    url = f"{base}/repos/{repo}/contents/{path_in_repo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    sha = None
    get_res = requests.get(url, headers=headers, params={"ref": branch}, timeout=TIMEOUT)
    if get_res.status_code == 200:
        sha = get_res.json().get("sha")

    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    put_res = requests.put(url, headers=headers, json=payload, timeout=TIMEOUT)
    if put_res.status_code not in (200, 201):
        raise RuntimeError(f"GitHub PUT failed: {put_res.status_code} {put_res.text}")
    return put_res.json()

def render_updated_replace_urls_only(dest_text: str, picked_urls: Dict[str, str]) -> str:
    """
    يمرّ على ملف الوجهة سطرًا-بسطر:
      - إذا صادف #EXTINF يطابق إحدى القنوات المطلوبة وكان لدينا URL جديد لها،
        يبقي سطر الـEXTINF كما هو ويستبدل **السطر التالي** (إذا كان URL) بالرابط الجديد.
      - إذا لم يكن هناك سطر URL بعد الـEXTINF (حالة نادرة) سيقوم بإدراجه.
      - لا يضيف قنوات غير موجودة أصلًا.
    """
    lines = [ln.rstrip("\n") for ln in dest_text.splitlines()]
    if not lines or not lines[0].strip().upper().startswith("#EXTM3U"):
        lines = ["#EXTM3U"] + lines

    out: List[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("#EXTINF"):
            # هل هذا EXTINF لقناة من المطلوبين؟
            matched_name = None
            for official_name in WANTED_CHANNELS:
                pats = ALIASES.get(official_name, [])
                if find_first_match(ln, pats):
                    matched_name = official_name
                    break

            if matched_name and matched_name in picked_urls:
                # أبقِ الـEXTINF كما هو
                out.append(ln)
                new_url = picked_urls[matched_name]

                # إن كان التالي URL قديم: استبدله
                if i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].strip().startswith("#"):
                    out.append(new_url)
                    i += 2
                    continue
                else:
                    # ماكو URL بعده → أضف URL جديد
                    out.append(new_url)
                    i += 1
                    continue

        # الحالة العادية: انسخ السطر كما هو
        out.append(ln)
        i += 1

    # تأكد من نهاية سطر
    return "\n".join(out).rstrip() + "\n"

def main():
    # 1) حمّل المصدر والوجهة
    src_text = fetch_text(SOURCE_URL)
    dest_text = fetch_text(DEST_RAW_URL)

    # 2) التقط روابط القنوات المطلوبة من المصدر
    pairs = parse_m3u_pairs(src_text)
    picked_urls = pick_wanted(pairs)

    print("[i] Picked URLs:")
    for n in WANTED_CHANNELS:
        tag = "✓" if n in picked_urls else "x"
        print(f"  {tag} {n}")

    # 3) حدّث الملف الهدف باستبدال الروابط فقط
    updated = render_updated_replace_urls_only(dest_text, picked_urls)

    # 4) اكتب إلى GitHub أو محليًا
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
