# scripts/pull_channels_and_update.py
# -*- coding: utf-8 -*-
"""
يسحب مداخل قنوات محددة من RAW مصدر (ALL.m3u)
ويقوم بتحديث ملف وجهة (generalsports.m3u):
- يستبدل/يضيف القنوات التالية:
  Match! Futbol 1, Match! Futbol 2, Match! Futbol 3,
  TNT 1, TNT 2,
  Sky Sports Main Event, Sky Sports Premier League

تشغيل محلي: يكتب ملف نهائي على القرص.
تشغيل مع GITHUB_TOKEN: يحدّث الملف في الريبو عبر Contents API.
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
    "https://raw.githubusercontent.com/abusaeeidx/CricHd-playlists-Auto-Update-permanent/refs/heads/main/ALL.m3u"
)

DEST_RAW_URL = os.getenv(
    "DEST_RAW_URL",
    "https://raw.githubusercontent.com/a7shk1/m3u-broadcast/refs/heads/main/generalsports.m3u"
)

# للتحديث المباشر على GitHub (اختياري):
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "").strip()  # repo contents scope
GITHUB_REPO    = os.getenv("GITHUB_REPO", "a7shk1/m3u-broadcast")
GITHUB_BRANCH  = os.getenv("GITHUB_BRANCH", "main")
DEST_REPO_PATH = os.getenv("DEST_REPO_PATH", "generalsports.m3u")
COMMIT_MESSAGE = os.getenv("COMMIT_MESSAGE", "chore: update generalsports (Match!/TNT/Sky)")

# للكتابة محليًا إن ماكو توكن:
OUTPUT_LOCAL_PATH = os.getenv("OUTPUT_LOCAL_PATH", "./out/generalsports.m3u")

TIMEOUT = 25
VERIFY_SSL = True

# القنوات المطلوبة (بالترتيب):
WANTED_CHANNELS = [
    "Match! Futbol 1",
    "Match! Futbol 2",
    "Match! Futbol 3",
    "TNT 1",
    "TNT 2",
    "Sky Sports Main Event",
    "Sky Sports Premier League",
]

# Aliases/أنماط مطابقة مرنة (بعض المصادر تسمي القناة بصيغة مختلفة)
# المفتاح: الاسم الرسمي المطلوب
# القيمة: قائمة تعابير منتظمة (case-insensitive) للمطابقة على سطر EXTINF
ALIASES: Dict[str, List[re.Pattern]] = {
    "Match! Futbol 1": [
        re.compile(r"match!?\.?\s*futbol\s*1", re.I),
    ],
    "Match! Futbol 2": [
        re.compile(r"match!?\.?\s*futbol\s*2", re.I),
    ],
    "Match! Futbol 3": [
        re.compile(r"match!?\.?\s*futbol\s*3", re.I),
    ],
    "TNT 1": [
        re.compile(r"\btnt\s*(sports)?\s*1\b", re.I),
    ],
    "TNT 2": [
        re.compile(r"\btnt\s*(sports)?\s*2\b", re.I),
    ],
    "Sky Sports Main Event": [
        re.compile(r"sky\s*sports\s*main\s*event", re.I),
    ],
    "Sky Sports Premier League": [
        re.compile(r"sky\s*sports\s*premier\s*league", re.I),
    ],
}

# ===== وظائف مساعدة =====

def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, verify=VERIFY_SSL)
    r.raise_for_status()
    return r.text

def parse_m3u_pairs(m3u_text: str) -> List[Tuple[str, Optional[str]]]:
    """
    يحوّل ملف m3u إلى قائمة أزواج (extinf_line, url_line_or_None)
    يربط كل #EXTINF بالرابط الذي يليه إن وجد.
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

def find_first_match(extinf: str, patterns: List[re.Pattern]) -> bool:
    for p in patterns:
        if p.search(extinf):
            return True
    return False

def pick_wanted(source_pairs: List[Tuple[str, Optional[str]]]) -> Dict[str, Tuple[str, Optional[str]]]:
    """
    يرجّع dict: wanted_name -> (extinf_line, url)
    يلتقط أول تطابق لكل قناة مطلوبة.
    """
    picked: Dict[str, Tuple[str, Optional[str]]] = {}
    for extinf, url in source_pairs:
        for official_name in WANTED_CHANNELS:
            if official_name in picked:
                continue
            pats = ALIASES.get(official_name, [])
            if find_first_match(extinf, pats):
                picked[official_name] = (extinf, url)
    return picked

def upsert_github_file(repo: str, branch: str, path_in_repo: str, content_bytes: bytes, message: str, token: str):
    base = "https://api.github.com"
    url = f"{base}/repos/{repo}/contents/{path_in_repo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    # احصل على sha إن الملف موجود
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

def render_updated(dest_text: str, picked: Dict[str, Tuple[str, Optional[str]]]) -> str:
    """
    يحدّث/يضيف المداخل داخل ملف الوجهة:
    - يبحث عن أي EXTINF موجود لنفس القناة (باستخدام ALIASES) ويستبدله
    - إذا مش موجود: يضيفه في النهاية بترتيب WANTED_CHANNELS
    - يحافظ على #EXTM3U في أول سطر.
    """
    lines = [ln.rstrip("\n") for ln in dest_text.splitlines()]

    # تأكد من وجود header
    if not lines or not lines[0].strip().upper().startswith("#EXTM3U"):
        lines = ["#EXTM3U"] + lines

    # نبني ماب: index -> (name_to_replace)
    # إذا وجدنا #EXTINF يطابق alias لقناة مطلوبة، نعلّم موقعه لاستبداله بسطرين (extinf + url الذي بعده إن كان).
    idx_to_name: Dict[int, str] = {}
    for i, ln in enumerate(lines):
        if not ln.strip().startswith("#EXTINF"):
            continue
        for official_name in WANTED_CHANNELS:
            pats = ALIASES.get(official_name, [])
            if find_first_match(ln, pats):
                idx_to_name[i] = official_name
                break

    # استبدال في المكان (in-place)
    used_names = set()
    i = 0
    out: List[str] = []
    while i < len(lines):
        if i in idx_to_name:
            name = idx_to_name[i]
            pair = picked.get(name)
            if pair:
                extinf, url = pair
                out.append(extinf)
                if url:
                    out.append(url)
                used_names.add(name)
                # تخطّى السطر التالي إذا كان URL قديم
                if i + 1 < len(lines) and not lines[i + 1].strip().startswith("#"):
                    i += 2
                    continue
                else:
                    i += 1
                    continue
            # لو ما قدرنا نجيبها من المصدر لأي سبب، خليه القديم كما هو:
            out.append(lines[i])
            i += 1
        else:
            out.append(lines[i])
            i += 1

    # أضف القنوات غير الموجودة أصلًا (append بالترتيب)
    for name in WANTED_CHANNELS:
        if name in used_names:
            continue
        pair = picked.get(name)
        if not pair:
            continue
        extinf, url = pair
        # أضف فاصلة مرئية بين الأقسام
        if out and out[-1].strip():
            out.append("")
        out.append(f"# --- {name} ---")
        out.append(extinf)
        if url:
            out.append(url)

    # نظّف فراغات زائدة في النهاية
    while out and not out[-1].strip():
        out.pop()

    return "\n".join(out) + "\n"

def main():
    # 1) حمّل المصدر والوجهة
    src_text = fetch_text(SOURCE_URL)
    dest_text = fetch_text(DEST_RAW_URL)

    # 2) حلّل المصدر والتقط القنوات المطلوبة
    pairs = parse_m3u_pairs(src_text)
    picked = pick_wanted(pairs)

    # quick log
    print("[i] Picked:")
    for n in WANTED_CHANNELS:
        tag = "✓" if n in picked else "x"
        print(f"  {tag} {n}")

    # 3) ركّب ملف الوجهة المحدّث
    updated = render_updated(dest_text, picked)

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
