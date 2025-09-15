# scripts/pull_channels_and_update.py
# -*- coding: utf-8 -*-
"""
يسحب روابط القنوات (TNT 1, TNT 2, Sky Sports Main Event UK, Sky Sports Premier League UK)
من RAW مصدر (ALL.m3u) ويحدّث premierleague.m3u باستبدال **سطر الرابط فقط** الذي يلي #EXTINF
لنفس القناة، مع الإبقاء على مكانها ونص الـEXTINF كما هو تمامًا.
لا يضيف قنوات جديدة إن لم توجد في الملف الهدف.
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
    "https://raw.githubusercontent.com/DisabledAbel/daddylivehd-m3u/f582ae100c91adf8c8db905a8f97beb42f369a0b/daddylive-events.m3u8"
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

# ===== فقط القنوات المطلوبة =====
WANTED_CHANNELS = [
    "TNT 1",
    "TNT 2",
    "Sky Sports Main Event UK",
    "Sky Sports Premier League UK",
]

# قوائم أسماء/مرادفات صريحة (بدون ريجكس) تُطبَّق على اسم القناة بعد الفاصلة
NAME_ALIASES: Dict[str, List[str]] = {
    "TNT 1": [
        "tnt 1", "tnt sports 1"
    ],
    "TNT 2": [
        "tnt 2", "tnt sports 2"
    ],
    "Sky Sports Main Event UK": [
        "sky sports main event", "sky sports main event uk"
    ],
    "Sky Sports Premier League UK": [
        # نمنع مطابقة "sky premier league" بدون "sports"
        "sky sports premier league", "sky sports premier league uk"
    ],
}

UK_MARKERS = (" uk", "(uk", "[uk", "🇬🇧", " united kingdom")

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
            out.append((lines[i], url))
            i += 2
            continue
        i += 1
    return out

def extract_channel_name_from_extinf(extinf_line: str) -> str:
    """
    يأخذ سطر EXTINF الكامل ويستخرج اسم القناة بعد أول فاصلة ','.
    مثال: '#EXTINF:-1, Sky Sports Premier League HD' -> 'Sky Sports Premier League HD'
    """
    try:
        return extinf_line.split(",", 1)[1].strip()
    except Exception:
        return extinf_line

def norm_name(name: str) -> str:
    """
    تبسيط الاسم للمقارنة: حروف صغيرة، إزالة تكرار المسافات،
    إزالة كلمات الجودة (hd/fhd/uhd/4k)، إزالة رموز زائدة.
    """
    n = name.lower()
    # شيل أقواس/رموز شائعة
    n = re.sub(r"[\[\]\(\)]+", " ", n)
    # شيل كلمات الجودة
    n = re.sub(r"\b(uhd|4k|fhd|hd|sd)\b", " ", n)
    # شيل فواصل/نقاط إضافية
    n = re.sub(r"[^\w\s]+", " ", n)
    # مسافة واحدة
    n = re.sub(r"\s+", " ", n).strip()
    return n

def name_matches_target(extinf_line: str, target: str) -> bool:
    """
    مطابقة صارمة على اسم القناة بعد الفاصلة مع aliases الخاصة بالهدف.
    تمنع ماتش لـ 'Sky Premier League' بدون 'Sports'.
    """
    ch_name = extract_channel_name_from_extinf(extinf_line)
    n = norm_name(ch_name)
    allowed = [norm_name(a) for a in NAME_ALIASES.get(target, [])]
    return n in allowed or any(n.startswith(a + " ") for a in allowed)

def pick_wanted(source_pairs: List[Tuple[str, Optional[str]]]) -> Dict[str, str]:
    """
    يرجّع dict: wanted_name -> stream_url
    يلتقط **أفضل مرشّح** لكل قناة مطلوبة من المصدر، مع تفضيل 'UK/🇬🇧' إن وُجد.
    """
    candidates: Dict[str, List[Tuple[str, str]]] = {name: [] for name in WANTED_CHANNELS}

    def has_uk_tag(s: str) -> bool:
        s_low = s.lower()
        return any(tag in s_low for tag in UK_MARKERS) or "🇬🇧" in s

    for extinf, url in source_pairs:
        if not url:
            continue
        # نفحص اسم القناة في المصدر بعد الفاصلة
        src_name = extract_channel_name_from_extinf(extinf)
        for official_name in WANTED_CHANNELS:
            # نرفض أي مطابقة ما تحتوي "sports" لقناة سكاي PL
            if "Premier League" in official_name and "sports" not in norm_name(src_name):
                continue
            # نستخدم aliases للمطابقة الدقيقة
            if name_matches_target(extinf, official_name):
                candidates[official_name].append((extinf, url))

    picked: Dict[str, str] = {}

    for name, lst in candidates.items():
        if not lst:
            continue

        def score(item: Tuple[str, str]) -> int:
            ext = item[0]
            sc = 0
            if has_uk_tag(ext): sc += 5
            ext_low = ext.lower()
            if " fhd" in ext_low or " hd" in ext_low or " uhd" in ext_low or " 4k" in ext_low: sc += 2
            if re.search(r"\b(en|english)\b", ext_low): sc += 1
            return sc

        best = sorted(lst, key=score, reverse=True)[0]
        picked[name] = best[1]

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
    يمشي على ملف الوجهة سطر-بسطر:
      - إذا صادف #EXTINF لقناة مطلوبة ولدينا URL جديد لها:
        * يبقي سطر الـEXTINF كما هو (بدون أي تعديل على الاسم/النص)
        * يستبدل السطر التالي (إذا كان URL) بالرابط الجديد أو يدرجه إذا مفقود.
      - لا يضيف قنوات جديدة إن لم تكن موجودة أساسًا.
    """
    lines = [ln.rstrip("\n") for ln in dest_text.splitlines()]
    if not lines or not lines[0].strip().upper().startswith("#EXTM3U"):
        lines = ["#EXTM3U"] + lines

    out: List[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("#EXTINF"):
            # نحدد هل هذا الـEXTINF يخص قناة مطلوبة عبر الاسم بعد الفاصلة
            matched_name = None
            for official_name in WANTED_CHANNELS:
                if name_matches_target(ln, official_name):
                    matched_name = official_name
                    break

            if matched_name and matched_name in picked_urls:
                # أبقِ الـEXTINF كما هو حرفيًا
                out.append(ln)
                new_url = picked_urls[matched_name]

                # إذا السطر اللي بعده URL: استبدله، وإلا أدرجه
                if i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].strip().startswith("#"):
                    out.append(new_url)
                    i += 2
                    continue
                else:
                    out.append(new_url)
                    i += 1
                    continue

        # الحالة العادية: انسخ السطر كما هو
        out.append(ln)
        i += 1

    return "\n".join(out).rstrip() + "\n"

def main():
    # 1) حمّل المصدر والوجهة
    src_text = fetch_text(SOURCE_URL)
    dest_text = fetch_text(DEST_RAW_URL)

    # 2) التقط أفضل روابط القنوات المطلوبة من المصدر
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

