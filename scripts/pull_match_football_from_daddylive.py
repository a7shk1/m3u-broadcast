# scripts/pull_match_football_from_daddylive.py
# -*- coding: utf-8 -*-
"""
يسحب فقط قنوات:
  MATCH! FOOTBALL 1 RUSSIA
  MATCH! FOOTBALL 2 RUSSIA
  MATCH! FOOTBALL 3 RUSSIA
من مصدر daddylive (M3U)، ويحدّث ملف generalsports.m3u في الريبو بصيغة نظيفة:
  #EXTINF:-1,<OFFICIAL_NAME>
  <URL>

لو GITHUB_TOKEN موجود: يحدث الملف عبر GitHub Contents API.
لو ما موجود: يكتب ملف محليًا على OUTPUT_LOCAL_PATH.
"""

import os
import re
import sys
import base64
from typing import List, Tuple, Dict, Optional
import requests

# ---------- إعدادات قابلة للتعديل عبر متغيرات البيئة ----------

SOURCE_URL = os.getenv(
    "SOURCE_URL",
    "https://raw.githubusercontent.com/pigzillaaa/daddylive/bc876b2f7935aeeb0df5b1c6b62b3c5f33998368/daddylive-channels-events.m3u8"
)

# ملف الوجهة (الخام للقراءة فقط، نستخدمه كنقطة بداية لنحافظ على المحتوى الآخر)
DEST_RAW_URL = os.getenv(
    "DEST_RAW_URL",
    "https://raw.githubusercontent.com/a7shk1/m3u-broadcast/refs/heads/main/generalsports.m3u"
)

# لتحديث الملف مباشرة على GitHub:
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "").strip()  # permissions: contents:write
GITHUB_REPO    = os.getenv("GITHUB_REPO", "a7shk1/m3u-broadcast")
GITHUB_BRANCH  = os.getenv("GITHUB_BRANCH", "main")
DEST_REPO_PATH = os.getenv("DEST_REPO_PATH", "generalsports.m3u")
COMMIT_MESSAGE = os.getenv("COMMIT_MESSAGE", "chore: update MATCH! FOOTBALL 1/2/3 from daddylive")

# للكتابة محلية عند عدم توفر التوكن:
OUTPUT_LOCAL_PATH = os.getenv("OUTPUT_LOCAL_PATH", "./out/generalsports.m3u")

TIMEOUT = 25
VERIFY_SSL = True

# ---------- القنوات المطلوبة + أنماط المطابقة ----------

WANTED_CHANNELS = [
    "MATCH! FOOTBALL 1 RUSSIA",
    "MATCH! FOOTBALL 2 RUSSIA",
    "MATCH! FOOTBALL 3 RUSSIA",
]

# تعابير منتظمة مرنة لالتقاط الاسم حتى لو المصدر يحط تفاصيل قبل/بعد
ALIASES: Dict[str, List[re.Pattern]] = {
    "MATCH! FOOTBALL 1 RUSSIA": [
        re.compile(r"match!?\.?\s*football\s*1\s*russia", re.I),
        re.compile(r"match!?\.?\s*futbol\s*1", re.I),  # احتياط
    ],
    "MATCH! FOOTBALL 2 RUSSIA": [
        re.compile(r"match!?\.?\s*football\s*2\s*russia", re.I),
        re.compile(r"match!?\.?\s*futbol\s*2", re.I),
    ],
    "MATCH! FOOTBALL 3 RUSSIA": [
        re.compile(r"match!?\.?\s*football\s*3\s*russia", re.I),
        re.compile(r"match!?\.?\s*futbol\s*3", re.I),
    ],
}

# ---------- وظائف مساعدة ----------

def fetch_text(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, verify=VERIFY_SSL)
    r.raise_for_status()
    return r.text

def parse_m3u_pairs(m3u_text: str) -> List[Tuple[str, Optional[str]]]:
    """
    يحوّل M3U إلى أزواج (EXTINF, URL)
    يربط كل #EXTINF بالرابط الذي يليه إن وجد (وليس سطر تعليق).
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
    txt = extinf
    # نطبع الاسم بعد الفاصلة إن وجد (#EXTINF:-1,NAME ...)
    m = re.search(r"#EXTINF[^,]*,(.*)$", extinf, flags=re.I)
    if m:
        txt = m.group(1)
    for p in patterns:
        if p.search(txt):
            return True
    return False

def pick_wanted_clean(source_pairs: List[Tuple[str, Optional[str]]]) -> Dict[str, Tuple[str, Optional[str]]]:
    """
    يرجّع dict: official_name -> (clean_extinf, url)
    حيث clean_extinf يكون بالصيغة البسيطة: "#EXTINF:-1,OFFICIAL_NAME"
    يلتقط أول تطابق لكل قناة مطلوبة.
    """
    picked: Dict[str, Tuple[str, Optional[str]]] = {}
    for extinf, url in source_pairs:
        if not url:
            continue
        for official_name in WANTED_CHANNELS:
            if official_name in picked:
                continue
            pats = ALIASES.get(official_name, [])
            if find_first_match(extinf, pats):
                clean_line = f"#EXTINF:-1,{official_name}"
                picked[official_name] = (clean_line, url)
    return picked

def upsert_github_file(repo: str, branch: str, path_in_repo: str, content_bytes: bytes, message: str, token: str):
    base = "https://api.github.com"
    url = f"{base}/repos/{repo}/contents/{path_in_repo}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    # احصل على sha الحالي (إن الملف موجود)
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
    - يبحث عن أي EXTINF موجود لنفس القنوات ويستبدله بسطرين نظيفين (EXTINF البسيط + URL).
    - إذا مش موجودة: يضيفها بترتيب WANTED_CHANNELS في النهاية.
    - يحافظ على #EXTM3U في بداية الملف.
    """
    lines = [ln.rstrip("\n") for ln in dest_text.splitlines()]

    # تأكد من وجود header
    if not lines or not lines[0].strip().upper().startswith("#EXTM3U"):
        lines = ["#EXTM3U"] + lines

    # حدد مواقع القنوات الحالية المطابقة (لاستبدالها)
    idx_to_official: Dict[int, str] = {}
    for i, ln in enumerate(lines):
        if not ln.strip().startswith("#EXTINF"):
            continue
        # استخرج اسم العرض بعد الفاصلة (قدر الإمكان)
        display = ln
        m = re.search(r"#EXTINF[^,]*,(.*)$", ln, flags=re.I)
        if m:
            display = m.group(1).strip()
        for official_name in WANTED_CHANNELS:
            pats = ALIASES.get(official_name, [])
            # طابق على display أو على السطر كله كاحتياط
            if any(p.search(display) for p in pats) or any(p.search(ln) for p in pats):
                idx_to_official[i] = official_name
                break

    used = set()
    out: List[str] = []
    i = 0
    while i < len(lines):
        if i in idx_to_official:
            official = idx_to_official[i]
            pair = picked.get(official)
            if pair:
                clean_extinf, url = pair
                out.append(clean_extinf)
                if url:
                    out.append(url)
                used.add(official)
                # تخطّى السطر التالي إن كان URL قديم (غير تعليق)
                if i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].strip().startswith("#"):
                    i += 2
                else:
                    i += 1
                continue
            # لو ما قدرنا نجيبها من المصدر لأي سبب، خليه القديم
            out.append(lines[i])
            i += 1
        else:
            out.append(lines[i])
            i += 1

    # أضف القنوات الناقصة بترتيب ثابت
    for name in WANTED_CHANNELS:
        if name in used:
            continue
        pair = picked.get(name)
        if not pair:
            continue
        clean_extinf, url = pair
        if out and out[-1].strip():
            out.append("")
        out.append(f"# --- {name} ---")
        out.append(clean_extinf)
        if url:
            out.append(url)

    # نظف نهايات فارغة
    while out and not out[-1].strip():
        out.pop()

    return "\n".join(out) + "\n"

# ---------- main ----------

def main():
    # 1) حمّل المصدر والوجهة
    src_text = fetch_text(SOURCE_URL)
    dest_text = fetch_text(DEST_RAW_URL)

    # 2) حلّل المصدر والتقط القنوات المطلوبة بصيغة clean
    pairs = parse_m3u_pairs(src_text)
    picked = pick_wanted_clean(pairs)

    print("[i] Picked from source:")
    for name in WANTED_CHANNELS:
        print(f"  {'✓' if name in picked else 'x'} {name}")

    # 3) إذا ولا وحدة انمسكت، لا تغيّر الملف
    if not any(n in picked for n in WANTED_CHANNELS):
        print("[!] No wanted channels found in source. Skipping update.")
        return

    # 4) ركّب ملف الوجهة المحدّث
    updated = render_updated(dest_text, picked)

    # 5) اكتب إلى GitHub أو محلياً
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
        out_path = OUTPUT_LOCAL_PATH
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(updated)
        print("[i] Wrote locally:", os.path.abspath(out_path))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[x] Error:", e)
        sys.exit(1)
