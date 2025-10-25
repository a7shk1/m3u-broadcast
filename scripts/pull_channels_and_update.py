import os
import re
import sys
import time
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ===== إعدادات عامة =====
WATCH_URL = os.getenv("WATCH_URL", "https://dlhd.dad/watch.php?id=91")
BUTTON_TITLE = os.getenv("BUTTON_TITLE", "PLAYER 6")
M3U_PATH = "bein.m3u"
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
ALLOW_INSECURE_SSL = os.getenv("ALLOW_INSECURE_SSL", "true").lower() == "true"
CAPTURE_TIMEOUT_SEC = int(os.getenv("CAPTURE_TIMEOUT_SEC", "60"))  # وقت أطول

if ALLOW_INSECURE_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

M3U8_REGEX = re.compile(r'https?://[^\s\'"]+\.m3u8(?:[^\s\'"]*)?', re.IGNORECASE)

SESSION = requests.Session()
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Connection": "keep-alive",
}


def http_get(url, referer=None, retries=3, timeout=20):
    headers = DEFAULT_HEADERS.copy()
    if referer:
        headers["Referer"] = referer
    last_exc = None
    for i in range(retries):
        try:
            resp = SESSION.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
                verify=not ALLOW_INSECURE_SSL,
            )
            if resp.status_code == 200:
                return resp
            last_exc = RuntimeError(f"HTTP {resp.status_code} for {url}")
        except Exception as e:
            last_exc = e
        time.sleep(1.1 * (i + 1))
    raise last_exc


def extract_player_url_from_watch(html, base_url, title="PLAYER 6"):
    soup = BeautifulSoup(html, "html.parser")
    # 1) زر بالعنوان مباشرة
    btn = soup.find("button", attrs={"title": title})
    if btn and btn.get("data-url"):
        return urljoin(base_url, btn["data-url"])

    # 2) محاولات احتياطية
    for b in soup.find_all("button", class_=lambda c: c and "player-btn" in c):
        data_url = b.get("data-url")
        text = (b.get_text() or "").strip().lower()
        ttl = (b.get("title") or "").strip().lower()
        if (data_url and "stream-91.php" in data_url) or text.endswith("player 6") or ttl.endswith("player 6"):
            if data_url:
                return urljoin(base_url, data_url)

    # 3) افتراضي معروف
    return urljoin(base_url, "/player/stream-91.php")


def sniff_m3u8_with_playwright(player_url, referer):
    """
    يفتح صفحة الـ Player (مع كل iframes)، يعطي تفاعل وتشغيل للفيديو،
    ويراقب كل الطلبات من السياق لالتقاط أول .m3u8.
    """
    print(f"[BROWSER] Launch Chromium headless…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=DEFAULT_UA,
            ignore_https_errors=ALLOW_INSECURE_SSL,
            java_script_enabled=True,
            timezone_id="Asia/Baghdad",
            extra_http_headers={"Referer": referer, "Accept": "*/*"},
        )
        page = context.new_page()

        found_url = {"val": None}

        def maybe_set(u):
            if M3U8_REGEX.search(u) and found_url["val"] is None:
                found_url["val"] = u
                print(f"[CAPTURE] {u}")

        # راقب كل الطلبات/الردود على مستوى الـ context (يشمل الإطارات)
        context.on("request", lambda req: maybe_set(req.url))
        context.on("response", lambda res: maybe_set(res.url))

        print(f"[NAV] Goto watch page: {WATCH_URL}")
        page.goto(WATCH_URL, wait_until="domcontentloaded", timeout=45000)

        # انقر على الزر Player 6 إن وجد
        try:
            locator = page.locator(f"button[title='{BUTTON_TITLE}']")
            if locator.count() == 0:
                locator = page.get_by_text(BUTTON_TITLE, exact=False)
            locator.first.click(timeout=8000)
            print(f"[CLICK] Clicked '{BUTTON_TITLE}'")
        except Exception:
            print("[CLICK] Could not click watch-page button; may open player directly.")

        # انتقل لصفحة المشغّل
        print(f"[NAV] Goto player page: {player_url}")
        page.goto(player_url, wait_until="domcontentloaded", timeout=45000)

        # 1) محاولات تشغيل وتفاعل على الصفحة الأم
        try:
            page.mouse.click(200, 200)
        except Exception:
            pass
        try:
            page.keyboard.press("Space")
            page.keyboard.press("KeyK")
        except Exception:
            pass

        # 2) اشتغل داخل كل iframe: انقر/شغل الفيديو إن وُجد
        def poke_frame(f):
            try:
                # انقر وسط الإطار
                f.click("body", timeout=2000)
            except Exception:
                pass
            # حاول تشغيل أي <video>
            try:
                f.evaluate(
                    """
                    () => {
                        const v = document.querySelector('video');
                        if (v) {
                            v.muted = true;
                            const p = v.play?.();
                            return 'video-found';
                        }
                        return 'no-video';
                    }
                    """
                )
            except Exception:
                pass

        # نفّذ poke لكل إطار، وراقب أي إطارات جديدة لاحقًا
        for fr in page.frames:
            if fr != page.main_frame:
                poke_frame(fr)

        # بعض المشغلات تتأخر بتحميل iframe لاحقًا
        end_time = time.time() + CAPTURE_TIMEOUT_SEC
        last_poke = 0
        while time.time() < end_time and not found_url["val"]:
            # إذا جاء iframe جديد حركه
            for fr in page.frames:
                if fr != page.main_frame:
                    try:
                        # حاول العثور على زر تشغيل شائع (jwplayer, plyr, etc.)
                        for sel in ["button[aria-label='Play']", ".jw-icon-playback", ".vjs-big-play-button", ".plyr__control--overlaid"]:
                            try:
                                fr.click(sel, timeout=1000)
                            except Exception:
                                pass
                    except Exception:
                        pass
            # أعد المحاولات كل ~3 ثواني
            if time.time() - last_poke > 3:
                try:
                    page.mouse.click(220, 220)
                    page.keyboard.press("Space")
                except Exception:
                    pass
                last_poke = time.time()
            time.sleep(0.25)

        # Fallback أخير: لو ما التقطنا عبر الشبكة، جرّب نقرأ HTML ونستخرج .m3u8
        if not found_url["val"]:
            try:
                html = page.content()
                m = M3U8_REGEX.search(html or "")
                if m:
                    found_url["val"] = m.group(0)
                    print("[FALLBACK] Captured from HTML content.")
            except Exception:
                pass

        context.close()
        browser.close()

        if not found_url["val"]:
            raise ValueError("تعذر استخراج رابط m3u8 من حركة الشبكة (Playwright).")

        return found_url["val"]


def validate_m3u8_head(url, referer):
    try:
        headers = DEFAULT_HEADERS.copy()
        headers["Referer"] = referer
        r = SESSION.head(
            url,
            headers=headers,
            timeout=15,
            allow_redirects=True,
            verify=not ALLOW_INSECURE_SSL,
        )
        if r.status_code < 400:
            return True
    except Exception:
        pass
    try:
        r = SESSION.get(
            url,
            headers={"Referer": referer, **DEFAULT_HEADERS},
            timeout=20,
            stream=True,
            verify=not ALLOW_INSECURE_SSL,
        )
        if r.status_code < 400:
            chunk = next(r.iter_content(chunk_size=2048), b"")
            if b"#EXTM3U" in chunk:
                return True
    except Exception:
        pass
    return False


def update_bein_m3u(file_path, new_url):
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    idx = None
    for i, line in enumerate(lines):
        if "bein sports 1" in line.lower():
            idx = i
            break
    if idx is None:
        raise ValueError("تعذر العثور على قناة 'bein sports 1' داخل الملف.")

    # سطر الرابط الذي يلي الاسم
    url_line_i = None
    for j in range(idx + 1, min(idx + 5, len(lines))):
        if lines[j].strip().startswith("http"):
            url_line_i = j
            break

    if url_line_i is None:
        url_line_i = idx + 1
        while url_line_i > len(lines):
            lines.append("")
        lines.insert(url_line_i, new_url)
    else:
        lines[url_line_i] = new_url

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return True


def main():
    print(f"[INFO] Fetch watch page: {WATCH_URL}")
    watch_resp = http_get(WATCH_URL)

    player_url = extract_player_url_from_watch(watch_resp.text, WATCH_URL, title=BUTTON_TITLE)
    print(f"[INFO] Player URL: {player_url}")

    # الترصّد عبر Playwright مثل Network tab (مع إطارات وتفاعل)
    m3u8_url = sniff_m3u8_with_playwright(player_url, referer=WATCH_URL)
    print(f"[OK] Extracted m3u8: {m3u8_url}")

    is_valid = validate_m3u8_head(m3u8_url, referer=player_url)
    print(f"[CHECK] m3u8 validation: {'PASS' if is_valid else 'WARN'}")

    if DRY_RUN:
        print("[DRY-RUN] لن يتم تعديل bein.m3u في هذا الوضع.")
        return

    changed = update_bein_m3u(M3U_PATH, m3u8_url)
    print("[WRITE] bein.m3u updated." if changed else "[WRITE] No change needed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
