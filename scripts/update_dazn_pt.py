# scripts/update_dazn_pt.py
# -*- coding: utf-8 -*-
"""
يجلب روابط قنوات DAZN ELEVEN PT (1/2/3) من المصدر
ويحدّث dazn.m3u باستبدال **سطر الرابط فقط** الذي يلي #EXTINF لنفس القناة،
بدون تغيير نص الـEXTINF أو ترتيب القنوات. لا يضيف قنوات جديدة.
"""

import os
import re
import sys
import base64
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import requests

# ===== إعدادات (نفس المعلمات) =====

SOURCE_URL = os.getenv(
    "SOURCE_URL",
    "https://raw.githubusercontent.com/DisabledAbel/daddylivehd-m3u/f582ae100c91adf8c8db905a8f97beb42f369a0b/daddylive-events.m3u8"
)

DEST_RAW_URL = os.getenv(
    "DEST_RAW_URL",
    "https://raw.githubusercontent.com/a7shk1/m3u-broadcast/refs/heads/main/dazn.m3u"
)

GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO    = os.getenv("GITHUB_REPO", "a7shk1/m3u-broadcast")
GITHUB_BRANCH  = os.getenv("GITHUB_BRANCH", "main")
DEST_REPO_PATH = os.getenv("DEST_REPO_PATH", "dazn.m3u")
COMMIT_MESSAGE = os.getenv("COMMIT_MESSAGE", "chore: update DAZN ELEVEN PT (1/2/3) URLs")
OUTPUT_LOCAL_PATH = os.getenv("OUTPUT_LOCAL_PATH", "./out/dazn.m3u")

TIMEOUT = 25
VERIFY_SSL = True

# ===== القنوات المطلوبة =====
WANTED_CHANNELS = [
    "DAZN ELEVEN 1 PORTUGAL",
    "DAZN ELEVEN 2 PORTUGAL",
    "DAZN ELEVEN 3 PORTUGAL",
]

# ===== مطابقة السورس (EXTINF كامل) =====
SOURCE_PATTERNS: Dict[str, List[re.Pattern]] = {
    "DAZN ELEVEN 1 PORTUGAL": [
        re.compile(r"\bdazn\s*eleven\s*1\b.*\b(portugal|pt)\b", re.I),
        re.compile(r"\beleven\s*sports?\s*1\b.*\b(portugal|pt)\b", re.I),
        re.compile(r"\(.*(dazn\s*eleven|eleven\s*sports?)\s*1.*(portugal|pt).*?\)", re.I),
    ],
    "DAZN ELEVEN 2 PORTUGAL": [
        re.compile(r"\bdazn\s*eleven\s*2\b.*\b(portugal|pt)\b", re.I),
        re.compile(r"\beleven\s*sports?\s*2\b.*\b(portugal|pt)\b", re.I),
        re.compile(r"\(.*(dazn\s*eleven|eleven\s*sports?)\s*2.*(portugal|pt).*?\)", re.I),
    ],
    "DAZN ELEVEN 3 PORTUGAL": [
        re.compile(r"\bdazn\s*eleven\s*3\b.*\b(portugal|pt)\b", re.I),
        re.compile(r"\beleven\s*sports?\s*3\b.*\b(portugal|pt)\b", re.I),
        re.compile(r"\(.*(dazn\s*eleven|eleven\s*sports?)\s*3.*(portugal|pt).*?\)", re.I),
    ],
}

# ===== مطابقة الوجهة (على "اسم القناة بعد الفاصلة" فقط) =====
# نقبل صيغ عديدة: DAZN ELEVEN 1 / ELEVEN SPORTS 1 / ELEVEN 1 (+ PT/PORTUGAL اختياري)
DEST_NAME_VARIANTS: Dict[str, List[str]] = {
    "DAZN ELEVEN 1 PORTUGAL": [
        r"\bdazn\s*eleven\s*1\b",
        r"\beleven\s*sports?\s*1\b",
        r"\beleven\s*1\b",
    ],
    "DAZN ELEVEN 2 PORTUGAL": [
        r"\bdazn\s*eleven\s*2\b",
        r"\beleven\s*sports?\s*2\b",
        r"\beleven\s*2\b",
    ],
    "DAZN ELEVEN 3 PORTUGAL": [
        r"\bdazn\s*eleven\s*3\b",
        r"\beleven\s*sports?\s*3\b",
        r"\beleven\s*3\b",
    ],
}

PT_HINT = re.compile(r"\b(pt|portugal)\b", re.I)

# ===== وظائف مساعدة =====

def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, verify=VERIFY_SSL)
    r.raise_for_status()
    return r.text

def parse_m3u_pairs(m3u_text: str) -> List[Tuple[str, Optional[str]]]:
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

def extract_display_name(extinf_line: str) -> str:
    try:
        return extinf_line.split(",", 1)[1].strip()
    except Exception:
        return extinf_line

def norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[\[\]\(\),]+", " ", s)
    s = re.sub(r"\b(uhd|4k|fhd|hd|sd)\b", " ", s)
    s = re.sub(r"[^\w\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def source_match(extinf_line: str, target: str) -> bool:
    pats = SOURCE_PATTERNS.get(target, [])
    return any(p.search(extinf_line) for p in pats)

def dest_match(extinf_line: str, target: str) -> bool:
    """مطابقة الاسم بعد الفاصلة ضد مجموعة أنماط واسعة + تلميح PT/PORTUGAL لو موجود"""
    name = extract_display_name(extinf_line)
    n = norm(name)
    pats = [re.compile(p, re.I) for p in DEST_NAME_VARIANTS.get(target, [])]
    # لازم يطابق أحد الأنماط الأساسية (رقم القناة)، ويفضّل وجود PT/PORTUGAL إن ذُكر
    base_ok = any(p.search(n) for p in pats)
    if not base_ok:
        return False
    # إذا الاسم يذكر بلد، نتحقق يكون PT/PORTUGAL (حتى ما نلمس قنوات Eleven من دول أخرى)
    # إذا ما مذكور بلد، نعتبره صالح (نفس منطقك: لا نغيّر النص، بس نبدّل URL).
    if re.search(r"\b(italy|spain|deutsch|germany|austria|poland|belgium|france|usa|uk)\b", n):
        return bool(PT_HINT.search(n))
    return True

def pick_wanted(source_pairs: List[Tuple[str, Optional[str]]]) -> Dict[str, str]:
    """اختيار أفضل URL لكل قناة (نفضّل UHD/4K/FHD/HD + EN إن وجدت)"""
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

def render_updated_replace_urls_only(dest_text: str, picked_urls: Dict[str, str]) -> Tuple[str, int]:
    """
    يمرّ على الديستنيشن:
      - إذا صادف #EXTINF لقناة مطلوبة ولدينا URL جديد لها: يبقي سطر الـEXTINF كما هو، ويبدّل السطر التالي بالرابط (أو يدرجه).
      - لا يضيف قنوات جديدة.
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
            matched = None
            for name in WANTED_CHANNELS:
                if dest_match(ln, name):
                    matched = name
                    break
            if matched and matched in picked_urls:
                out.append(ln)  # لا تغيّر نص EXTINF
                new_url = picked_urls[matched]
                # إذا اللي بعده URL: بدّله، وإلا أدرجه
                if i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].strip().startswith("#"):
                    old_url = lines[i + 1]
                    if old_url != new_url:
                        updates += 1
                        print(f"[i] Updated URL for: {matched}")
                    else:
                        print(f"[i] URL already up-to-date: {matched}")
                    out.append(new_url)
                    i += 2
                    continue
                else:
                    updates += 1
                    print(f"[i] Inserted URL for: {matched}")
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

    # 2) التقط روابط DAZN 1/2/3 PT من المصدر
    pairs = parse_m3u_pairs(src_text)
    picked_urls = pick_wanted(pairs)

    # 3) حدّث الديستنيشن (استبدال سطر الرابط فقط)
    updated, updates = render_updated_replace_urls_only(dest_text, picked_urls)

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
