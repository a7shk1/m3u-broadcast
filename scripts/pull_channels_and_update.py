# scripts/pull_channels_and_update.py
# -*- coding: utf-8 -*-
"""
يسحب روابط القنوات (TNT 1, TNT 2, Sky Sports Main Event UK, Sky Sports Premier League UK)
من مصدر M3U ويحدث premierleague.m3u باستبدال **سطر الرابط فقط** الذي يلي #EXTINF
لنفس القناة، بدون أي تغيير على نص الـEXTINF. لا يضيف قنوات جديدة.

إصلاحات مهمة:
- مطابقة مرنة للسورس (حتى لو الاسم داخل عنوان طويل/أقواس).
- مطابقة صارمة للديستنيشن عبر regex للقناة على سطر الـEXTINF فقط.
- لوج تفصيلي لمعرفة شنو انمسك وتبدّل.
"""

import os
import re
import sys
import base64
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import requests

# ===== إعدادات (بدون تغيير معلماتك) =====

SOURCE_URL = os.getenv(
    "SOURCE_URL",
    "https://raw.githubusercontent.com/pigzillaaa/daddylive/refs/heads/main/daddylive-events.m3u8"
)

DEST_RAW_URL = os.getenv(
    "DEST_RAW_URL",
    "https://raw.githubusercontent.com/a7shk1/m3u-broadcast/refs/heads/main/premierleague.m3u"
)

GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO    = os.getenv("GITHUB_REPO", "a7shk1/m3u-broadcast")
GITHUB_BRANCH  = os.getenv("GITHUB_BRANCH", "main")
DEST_REPO_PATH = os.getenv("DEST_REPO_PATH", "premierleague.m3u")
COMMIT_MESSAGE = os.getenv("COMMIT_MESSAGE", "🔄 auto-update premierleague.m3u (every 5min)")

OUTPUT_LOCAL_PATH = os.getenv("OUTPUT_LOCAL_PATH", "./out/premierleague.m3u")

TIMEOUT = 25
VERIFY_SSL = True

# ===== القنوات =====
WANTED_CHANNELS = [
    "TNT 1",
    "TNT 2",
    "Sky Sports Main Event UK",
    "Sky Sports Premier League UK",
]

# مطابقة السورس: نبحث على **سطر EXTINF كله** (حتى لو الاسم داخل العنوان/الأقواس)
SOURCE_PATTERNS: Dict[str, List[re.Pattern]] = {
    "TNT 1": [re.compile(r"\btnt\s*(sports)?\s*1\b", re.I)],
    "TNT 2": [re.compile(r"\btnt\s*(sports)?\s*2\b", re.I)],
    "Sky Sports Main Event UK": [
        re.compile(r"\bsky\s*sports\s*main\s*event\b", re.I),
        re.compile(r"\(.*sky\s*sports\s*main\s*event\s*(uk)?\).*", re.I),
    ],
    "Sky Sports Premier League UK": [
        re.compile(r"\bsky\s*sports\s*premier\s*league\b", re.I),
        re.compile(r"\(.*sky\s*sports\s*premier\s*league\s*(uk)?\).*", re.I),
    ],
}

# مطابقة الديستنيشن: **سطر EXTINF فقط**. مانغيّر نصه نهائيًا.
DEST_EXTINF_PATTERNS: Dict[str, re.Pattern] = {
    "TNT 1": re.compile(r"^#EXTINF[^,]*,\s*.*\btnt(\s*sports)?\s*1\b.*$", re.I),
    "TNT 2": re.compile(r"^#EXTINF[^,]*,\s*.*\btnt(\s*sports)?\s*2\b.*$", re.I),
    "Sky Sports Main Event UK": re.compile(
        r"^#EXTINF[^,]*,\s*.*\bsky\s*sports\s*main\s*event\b.*$", re.I
    ),
    "Sky Sports Premier League UK": re.compile(
        r"^#EXTINF[^,]*,\s*.*\bsky\s*sports\s*premier\s*league\b.*$", re.I
    ),
}

UK_MARKERS = (" uk", "(uk", "[uk", " united kingdom", "🇬🇧")

# ===== وظائف مساعدة =====

def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, verify=VERIFY_SSL)
    r.raise_for_status()
    return r.text

def parse_m3u_pairs(m3u_text: str) -> List[Tuple[str, Optional[str]]]:
    """[(extinf_line, url_or_None), ...]"""
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
            out.append((lines[i], url))
            i += 2
            continue
        i += 1
    return out

def source_match(extinf_line: str, target: str) -> bool:
    pats = SOURCE_PATTERNS.get(target, [])
    return any(p.search(extinf_line) for p in pats)

def pick_wanted(source_pairs: List[Tuple[str, Optional[str]]]) -> Dict[str, str]:
    """
    التقط أفضل URL من السورس لكل قناة مطلوبة (تفضيل UK/🇬🇧 و HD/FHD/UHD و EN).
    """
    candidates: Dict[str, List[Tuple[str, str]]] = {name: [] for name in WANTED_CHANNELS}

    def has_uk_tag(s: str) -> bool:
        s_low = s.lower()
        return any(tag in s_low for tag in UK_MARKERS) or "🇬🇧" in s

    for extinf, url in source_pairs:
        if not url:
            continue
        for name in WANTED_CHANNELS:
            if source_match(extinf, name):
                candidates[name].append((extinf, url))

    picked: Dict[str, str] = {}
    for name, lst in candidates.items():
        if not lst:
            continue

        def score(item: Tuple[str, str]) -> int:
            ext = item[0].lower()
            sc = 0
            if has_uk_tag(ext): sc += 5
            if any(q in ext for q in (" uhd", " 4k", " fhd", " hd")): sc += 2
            if re.search(r"\b(en|english)\b", ext): sc += 1
            return sc

        best = sorted(lst, key=score, reverse=True)[0]
        picked[name] = best[1]

    # لوج
    print("[i] Source candidates picked:")
    for n in WANTED_CHANNELS:
        print(f"  {'✓' if n in picked else 'x'} {n}")
    return picked

def update_dest_urls_only(dest_text: str, picked_urls: Dict[str, str]) -> Tuple[str, int]:
    """
    يمر على الديستنيشن ويبدّل **سطر الرابط فقط** بعد كل EXTINF مطابق.
    يرجّع (النص النهائي، عدد التحديثات).
    """
    lines = [ln.rstrip("\n") for ln in dest_text.splitlines()]
    if not lines or not lines[0].strip().upper().startswith("#EXTM3U"):
        lines = ["#EXTM3U"] + lines

    out: List[str] = []
    i = 0
    updates = 0

    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("#EXTINF"):
            matched_name = None
            for name, pat in DEST_EXTINF_PATTERNS.items():
                if pat.search(ln):
                    matched_name = name
                    break

            if matched_name and matched_name in picked_urls:
                # إبقي الـEXTINF كما هو
                out.append(ln)
                new_url = picked_urls[matched_name]

                # إذا السطر البعده URL (مو تعليق): بدّله، وإلا أدرجه
                if i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].strip().startswith("#"):
                    old_url = lines[i + 1]
                    if old_url != new_url:
                        updates += 1
                        print(f"[i] Updated URL for: {matched_name}")
                    else:
                        print(f"[i] URL already up-to-date: {matched_name}")
                    out.append(new_url)
                    i += 2
                    continue
                else:
                    updates += 1
                    print(f"[i] Inserted URL for: {matched_name}")
                    out.append(new_url)
                    i += 1
                    continue

        out.append(ln)
        i += 1

    return ("\n".join(out).rstrip() + "\n", updates)

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

def main():
    # 1) حمّل المصدر والوجهة
    src_text = fetch_text(SOURCE_URL)
    dest_text = fetch_text(DEST_RAW_URL)

    # 2) اختَر أفضل روابط من السورس
    pairs = parse_m3u_pairs(src_text)
    picked_urls = pick_wanted(pairs)

    # 3) حدّث الديستنيشن (سطر URL فقط)
    updated_text, updates = update_dest_urls_only(dest_text, picked_urls)

    # 4) اكتب إلى GitHub أو محليًا
    if updates == 0:
        print("[i] No changes to write.")
        # حتى لو ماكو تغيير، نكتب محليًا إذا ماكو توكن (للتحقق)
    token = GITHUB_TOKEN
    if token:
        print(f"[i] Writing to GitHub: {GITHUB_REPO}@{GITHUB_BRANCH}:{DEST_REPO_PATH}")
        res = upsert_github_file(
            repo=GITHUB_REPO,
            branch=GITHUB_BRANCH,
            path_in_repo=DEST_REPO_PATH,
            content_bytes=updated_text.encode("utf-8"),
            message=COMMIT_MESSAGE,
            token=token,
        )
        print("[✓] Updated:", res.get("content", {}).get("path"))
    else:
        p = Path(OUTPUT_LOCAL_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(updated_text, encoding="utf-8")
        print("[i] Wrote locally to:", p.resolve())

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[x] Error:", e)
        sys.exit(1)
