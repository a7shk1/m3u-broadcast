# scripts/update_dazn_pt.py
# -*- coding: utf-8 -*-
"""
يسحب روابط قنوات (DAZN ELEVEN 1/2/3 PORTUGAL) من المصدر
ويحدّث ملف dazn.m3u باستبدال **سطر الرابط فقط** الذي يلي #EXTINF لنفس القناة،
بدون تغيير نص الـEXTINF أو ترتيب القنوات. لا يضيف قنوات جديدة.
"""

import os
import re
import sys
import base64
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import requests

# ===== إعدادات (بنفس أسماء المعلمات) =====

SOURCE_URL = os.getenv(
    "SOURCE_URL",
    # نفس مصدرك القديم
    "https://raw.githubusercontent.com/DisabledAbel/daddylivehd-m3u/f582ae100c91adf8c8db905a8f97beb42f369a0b/daddylive-events.m3u8"
)

# الوجهة الجديدة: dazn.m3u
DEST_RAW_URL = os.getenv(
    "DEST_RAW_URL",
    "https://raw.githubusercontent.com/a7shk1/m3u-broadcast/refs/heads/main/dazn.m3u"
)

# للتحديث المباشر على GitHub (اختياري):
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "").strip()  # repo contents scope
GITHUB_REPO    = os.getenv("GITHUB_REPO", "a7shk1/m3u-broadcast")
GITHUB_BRANCH  = os.getenv("GITHUB_BRANCH", "main")
DEST_REPO_PATH = os.getenv("DEST_REPO_PATH", "dazn.m3u")
COMMIT_MESSAGE = os.getenv("COMMIT_MESSAGE", "chore: update DAZN ELEVEN PT (1/2/3) URLs")

# للكتابة محليًا إن ماكو توكن:
OUTPUT_LOCAL_PATH = os.getenv("OUTPUT_LOCAL_PATH", "./out/dazn.m3u")

TIMEOUT = 25
VERIFY_SSL = True

# ===== القنوات المطلوبة فقط =====
WANTED_CHANNELS = [
    "DAZN ELEVEN 1 PORTUGAL",
    "DAZN ELEVEN 2 PORTUGAL",
    "DAZN ELEVEN 3 PORTUGAL",
]

# مطابقة مرنة للسورس (نبحث على كامل سطر EXTINF)
SOURCE_PATTERNS: Dict[str, List[re.Pattern]] = {
    "DAZN ELEVEN 1 PORTUGAL": [
        re.compile(r"\bdazn\s*eleven\s*1\b.*\b(portugal|pt)\b", re.I),
        re.compile(r"\(.*dazn\s*eleven\s*1.*(portugal|pt).*?\)", re.I),
    ],
    "DAZN ELEVEN 2 PORTUGAL": [
        re.compile(r"\bdazn\s*eleven\s*2\b.*\b(portugal|pt)\b", re.I),
        re.compile(r"\(.*dazn\s*eleven\s*2.*(portugal|pt).*?\)", re.I),
    ],
    "DAZN ELEVEN 3 PORTUGAL": [
        re.compile(r"\bdazn\s*eleven\s*3\b.*\b(portugal|pt)\b", re.I),
        re.compile(r"\(.*dazn\s*eleven\s*3.*(portugal|pt).*?\)", re.I),
    ],
}

# مطابقة صارمة للديستنيشن: سطر EXTINF فقط (نقارن الاسم بعد الفاصلة)
DEST_NAME_ALIASES: Dict[str, List[str]] = {
    # نقبل وجود/غياب "PORTUGAL" أو "PT" في الديستنيشن، بس نحافظ على النص كما هو
    "DAZN ELEVEN 1 PORTUGAL": ["dazn eleven 1", "dazn eleven 1 portugal", "dazn eleven 1 pt"],
    "DAZN ELEVEN 2 PORTUGAL": ["dazn eleven 2", "dazn eleven 2 portugal", "dazn eleven 2 pt"],
    "DAZN ELEVEN 3 PORTUGAL": ["dazn eleven 3", "dazn eleven 3 portugal", "dazn eleven 3 pt"],
}

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

def extract_channel_name_from_extinf(extinf_line: str) -> str:
    """اسم القناة بعد أول فاصلة في سطر EXTINF"""
    try:
        return extinf_line.split(",", 1)[1].strip()
    except Exception:
        return extinf_line

def norm_name(name: str) -> str:
    """تبسيط للمقارنة: حروف صغيرة + إزالة جودة/رموز/فراغات زايدة"""
    n = name.lower()
    n = re.sub(r"[\[\]\(\)]+", " ", n)
    n = re.sub(r"\b(uhd|4k|fhd|hd|sd)\b", " ", n)
    n = re.sub(r"[^\w\s]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n

def dest_name_matches(extinf_line: str, target: str) -> bool:
    """مطابقة الديستنيشن على اسم القناة فقط (بعد الفاصلة) ضد aliases"""
    ch = norm_name(extract_channel_name_from_extinf(extinf_line))
    allowed = [norm_name(a) for a in DEST_NAME_ALIASES.get(target, [])]
    return ch in allowed or any(ch.startswith(a + " ") for a in allowed)

def source_match(extinf_line: str, target: str) -> bool:
    """مطابقة مرنة للسورس على كامل سطر EXTINF"""
    pats = SOURCE_PATTERNS.get(target, [])
    return any(p.search(extinf_line) for p in pats)

def pick_wanted(source_pairs: List[Tuple[str, Optional[str]]]) -> Dict[str, str]:
    """اختيار أفضل URL لكل قناة (تفضيل FHD/HD و EN إن وجدت)"""
    candidates: Dict[str, List[Tuple[str, str]]] = {n: [] for n in WANTED_CHANNELS}

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
            if any(q in ext for q in (" uhd", " 4k", " fhd", " hd")): sc += 2
            if re.search(r"\b(en|english)\b", ext): sc += 1
            return sc

        best = sorted(lst, key=score, reverse=True)[0]
        picked[name] = best[1]

    print("[i] Picked from source:")
    for n in WANTED_CHANNELS:
        print(f"  {'✓' if n in picked else 'x'} {n}")
    return picked

def render_updated_replace_urls_only(dest_text: str, picked_urls: Dict[str, str]) -> str:
    """
    يمرّ على الديستنيشن:
      - إذا صادف #EXTINF لقناة مطلوبة ولدينا URL لها:
        * يبقي سطر الـEXTINF كما هو حرفيًا
        * يستبدل السطر التالي بالرابط الجديد (أو يدرجه إذا مفقود)
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
            matched = None
            for name in WANTED_CHANNELS:
                if dest_name_matches(ln, name):
                    matched = name
                    break

            if matched and matched in picked_urls:
                out.append(ln)  # لا نغيّر نص الـEXTINF
                new_url = picked_urls[matched]
                # إذا اللي بعده URL: بدّله، وإلا أدرجه
                if i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].strip().startswith("#"):
                    out.append(new_url)
                    i += 2
                    continue
                else:
                    out.append(new_url)
                    i += 1
                    continue

        out.append(ln)
        i += 1

    return "\n".join(out).rstrip() + "\n"

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

    # 2) التقط روابط DAZN 1/2/3 PT من المصدر
    pairs = parse_m3u_pairs(src_text)
    picked_urls = pick_wanted(pairs)

    # 3) حدّث الديستنيشن (استبدال سطر الرابط فقط)
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
