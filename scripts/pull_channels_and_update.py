import os
import re
import sys
import time
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ===== إعدادات عامة =====
WATCH_URL = os.getenv("WATCH_URL", "https://dlhd.dad/watch.php?id=91")
BUTTON_TITLE = os.getenv("BUTTON_TITLE", "PLAYER 6")
M3U_PATH = "bein.m3u"
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
ALLOW_INSECURE_SSL = os.getenv("ALLOW_INSECURE_SSL", "true").lower() == "true"

if ALLOW_INSECURE_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

M3U8_REGEX = re.compile(r'https?://[^\s\'"]+\.m3u8[^\s\'"]*', re.IGNORECASE)

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

    # 3) افتراضي معروف عندك
    return urljoin(base_url, "/player/stream-91.php")


def sniff_m3u8_with_playwright(player_url, referer):
    """
    يفتح صفحة الـ Player عبر Chromium headless،
    يترصّد حركة الشبكة ويرجع أول رابط .m3u8 يتم طلبه/الرد عليه.
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

        def handle_request(req):
            url = req.url
            if M3U8_REGEX.search(url) and found_url["val"] is None:
                found_url["val"] = url
                print(f"[CAPTURE][request] {url}")

        def handle_response(res):
            url = res.url
            if M3U8_REGEX.search(url) and found_url["val"] is None:
                found_url["val"] = url
                print(f"[CAPTURE][response] {url}")

        page.on("request", handle_request)
        page.on("response", handle_response)

        print(f"[NAV] Goto watch page: {WATCH_URL}")
        page.goto(WATCH_URL, wait_until="domcontentloaded", timeout=30000)

        # حاول النقر على الزر مثل ما تعمل يدويًا
        try:
            locator = page.locator(f"button[title='{BUTTON_TITLE}']")
            if locator.count() == 0:
                # بديل نصي
                locator = page.get_by_text(BUTTON_TITLE, exact=False)
            locator.first.click(timeout=8000)
            print(f"[CLICK] Clicked '{BUTTON_TITLE}'")
        except Exception:
            # لو فشل النقر، نروح مباشرة لعنوان المشغل
            print("[CLICK] Could not click button; will navigate directly to player URL.")

        # نضمن وصولنا لصفحة المشغل نفسها
        print(f"[NAV] Goto player page: {player_url}")
        page.goto(player_url, wait_until="domcontentloaded", timeout=30000)

        # انتظر حتى يظهر أي طلب m3u8
        deadline = time.time() + 25  # ثواني
        while time.time() < deadline and found_url["val"] is None:
            time.sleep(0.25)

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

    # الترصّد عبر Playwright مثل Network tab
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
