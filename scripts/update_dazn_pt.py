# scripts/update_dazn_pt.py
# -*- coding: utf-8 -*-
"""
يجلب روابط DAZN ELEVEN PT (1/2/3) من المصدر
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

# ===== إعدادات (نفس معلماتك) =====
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

# ===== القنوات الهدف =====
# نلتقط من "المصدر": DAZN ELEVEN {1..3} PORTUGAL / ELEVEN SPORTS {1..3} (PT)
# ونحدّث في "الوجهة": DAZN {1..3} حصراً (لا نلمس DAZN 4..6).
WANTED = {
    "DAZN ELEVEN 1 PORTUGAL": 1,
    "DAZN ELEVEN 2 PORTUGAL": 2,
    "DAZN ELEVEN 3 PORTUGAL": 3,
}

# ===== مطابقة المصدر (EXTINF كامل) =====
def source_patterns_for(num: int) -> list[re.Pattern]:
    n = str(num)
    return [
        re.compile(rf"\bdazn\s*eleven\s*{n}\b.*\b(portugal|pt)\b", re.I),
        re.compile(rf"\beleven\s*sports?\s*{n}\b.*\b(portugal|pt)\b", re.I),
        re.compile(rf"\(.*(dazn\s*eleven|eleven\s*sports?)\s*{n}.*(portugal|pt).*?\)", re.I),
    ]

# ===== مطابقة الوجهة (اسم القناة بعد الفاصلة) =====
# انت كاتبها "DAZN 1/2/3" — نخليها مطابقة حرفية لهذه الصيغة حتى نتأكد نلمس بس هذني.
def dest_regex_for(num: int) -> re.Pattern:
    # أمثلة صالحة: "#EXTINF:-1,DAZN 1", "#EXTINF:-1 tvg-id=...,DAZN 1 HD"
    return re.compile(rf"^#EXTINF[^,]*,\s*DAZN\s*{num}\b.*$", re.I)

# ===== مساعدات =====
def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, verify=VERIFY_SSL)
    r.raise_for_status()
    return r.text

def parse_pairs(m3u_text: str) -> List[Tuple[str, Optional[str]]]:
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

def pick_from_source(pairs: List[Tuple[str, Optional[str]]]) -> Dict[str, str]:
    picked: Dict[str, str] = {}
    for name, num in WANTED.items():
        pats = source_patterns_for(num)
        cands: list[Tuple[str,str]] = []
        for extinf, url in pairs:
            if not url: 
                continue
            if any(p.search(extinf) for p in pats):
                cands.append((extinf, url))

        if cands:
            # نفضّل UHD/4K/FHD/HD + EN إذا موجود
            def score(item: Tuple[str, str]) -> int:
                ext = item[0].lower()
                sc = 0
                if any(q in ext for q in (" uhd", " 4k", " fhd", " hd")): sc += 2
                if re.search(r"\b(en|english)\b", ext): sc += 1
                return sc
            best = sorted(cands, key=score, reverse=True)[0]
            picked[name] = best[1]

    print("[i] Source picks:")
    for k in WANTED.keys():
        print("   ", ("✓" if k in picked else "x"), k)
    return picked

def update_dest_urls_only(dest_text: str, picked: Dict[str,str]) -> Tuple[str,int]:
    lines = [ln.rstrip("\n") for ln in dest_text.splitlines()]
    if not lines or not lines[0].strip().upper().startswith("#EXTM3U"):
        lines = ["#EXTM3U"] + lines

    # بُنيّة مطابقة الوجهة فقط لـ DAZN 1/2/3
    dest_pats: Dict[int,re.Pattern] = {num: dest_regex_for(num) for num in (1,2,3)}
    # خريطة تحويل: أي DAZN {n} بالوجهة -> أي قناة مصدر نقابلها
    wanted_by_num: Dict[int, str] = {num: f"DAZN ELEVEN {num} PORTUGAL" for num in (1,2,3)}

    out: List[str] = []
    i = 0
    updates = 0

    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("#EXTINF"):
            matched_num = None
            for num, pat in dest_pats.items():
                if pat.search(ln):
                    matched_num = num
                    break

            if matched_num:
                wanted_key = wanted_by_num[matched_num]
                if wanted_key in picked:
                    out.append(ln)  # لا تغيّر الـEXTINF إطلاقًا
                    new_url = picked[wanted_key]
                    # إذا اللي بعده URL: بدّله، وإلا أدرجه
                    if i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].strip().startswith("#"):
                        old_url = lines[i + 1]
                        if old_url != new_url:
                            updates += 1
                            print(f"[i] Updated URL for: DAZN {matched_num}")
                        else:
                            print(f"[i] URL already up-to-date: DAZN {matched_num}")
                        out.append(new_url)
                        i += 2
                        continue
                    else:
                        updates += 1
                        print(f"[i] Inserted URL for: DAZN {matched_num}")
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

    payload = {"message": message, "content": base64.b64encode(content_bytes).decode("utf-8"), "branch": branch}
    if sha:
        payload["sha"] = sha

    put_res = requests.put(url, headers=headers, json=payload, timeout=TIMEOUT)
    if put_res.status_code not in (200, 201):
        raise RuntimeError(f"GitHub PUT failed: {put_res.status_code} {put_res.text}")
    return put_res.json()

def main():
    # 1) المصدر & الوجهة
    src_text = fetch_text(SOURCE_URL)
    dest_text = fetch_text(DEST_RAW_URL)

    # 2) التقط روابط DAZN ELEVEN PT 1/2/3 من المصدر
    pairs = parse_pairs(src_text)
    picked = pick_from_source(pairs)

    # 3) حدّث فقط DAZN 1/2/3 في الوجهة (استبدال سطر الرابط الذي يلي الـEXTINF)
    updated, n_up = update_dest_urls_only(dest_text, picked)

    # 4) كتابة
    token = GITHUB_TOKEN
    if token:
        print(f"[i] Writing to GitHub: {GITHUB_REPO}@{GITHUB_BRANCH}:{DEST_REPO_PATH}")
        upsert_github_file(GITHUB_REPO, GITHUB_BRANCH, DEST_REPO_PATH, updated.encode("utf-8"), COMMIT_MESSAGE, token)
        print(f"[✓] Done. Updates: {n_up}")
    else:
        p = Path(OUTPUT_LOCAL_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(updated, encoding="utf-8")
        print(f"[i] Wrote locally: {p.resolve()} | Updates: {n_up}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[x] Error:", e)
        sys.exit(1)
