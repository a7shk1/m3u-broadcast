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
CAPTURE_TIMEOUT_SEC = int(os.getenv("CAPTURE_TIMEOUT_SEC", "90"))

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

captured_urls = []  # لتجميع كل الروابط المطلوبة (للتشخيص/الأرتيفاكت)

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
    btn = soup.find("button", attrs={"title": title})
    if btn and btn.get("data-url"):
        return urljoin(base_url, btn["data-url"])
    for b in soup.find_all("button", class_=lambda c: c and "player-btn" in c):
        data_url = b.get("data-url")
        text = (b.get_text() or "").strip().lower()
        ttl = (b.get("title") or "").strip().lower()
        if (data_url and "stream-91.php" in data_url) or text.endswith("player 6") or ttl.endswith("player 6"):
            if data_url:
                return urljoin(base_url, data_url)
    return urljoin(base_url, "/player/stream-91.php")

# كود JS يُحقن مبكرًا لالتقاط fetch/XHR في كل الإطارات
INIT_PATCH_JS = r"""
(() => {
  const log = (u) => {
    try {
      if (typeof window !== 'undefined' && window.__reportM3U8) {
        window.__reportM3U8(u);
      } else {
        console.log("M3U8::" + u);
      }
    } catch (e) {
      console.log("M3U8::" + u);
    }
  };

  const isM3U8 = (u) => /https?:\/\/[^\s'"]+\.m3u8[^\s'"]*/i.test(String(u || ""));

  // Patch fetch
  try {
    const _fetch = window.fetch;
    if (_fetch && !_fetch.__m3u8_patched) {
      window.fetch = async (...args) => {
        try {
          const url = args[0];
          if (isM3U8(url)) log(url);
        } catch (e) {}
        const res = await _fetch(...args);
        try {
          const url = res?.url;
          if (isM3U8(url)) log(url);
        } catch (e) {}
        return res;
      };
      window.fetch.__m3u8_patched = true;
    }
  } catch (e) {}

  // Patch XMLHttpRequest
  try {
    const _open = XMLHttpRequest.prototype.open;
    const _send = XMLHttpRequest.prototype.send;
    if (!_open.__m3u8_patched) {
      let lastUrl = null;
      XMLHttpRequest.prototype.open = function(method, url, ...rest) {
        lastUrl = url;
        return _open.call(this, method, url, ...rest);
      };
      XMLHttpRequest.prototype.send = function(...args) {
        try { if (isM3U8(lastUrl)) log(lastUrl); } catch (e) {}
        return _send.apply(this, args);
      };
      _open.__m3u8_patched = true;
    }
  } catch (e) {}
})();
"""

def sniff_m3u8_with_playwright(player_url, referer):
    """
    يفتح المشغّل، يحقن باتش fetch/XHR مبكرًا، ويراقب كل الإطارات والـ console + الشبكة.
    يرجّع أول m3u8 يُلتقط.
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
        # استلام إشعارات من JS
        m3u8_holder = {"val": None}

        def on_console(msg):
            txt = msg.text()
            if txt.startswith("M3U8::"):
                url = txt.split("M3U8::", 1)[1].strip()
                if url and m3u8_holder["val"] is None:
                    m3u8_holder["val"] = url
                    print(f"[CAPTURE][console] {url}")

        context.on("console", on_console)

        def on_request(req):
            url = req.url
            captured_urls.append(url)
            if M3U8_REGEX.search(url) and m3u8_holder["val"] is None:
                m3u8_holder["val"] = url
                print(f"[CAPTURE][request] {url}")

        def on_response(res):
            url = res.url
            captured_urls.append(url)
            if M3U8_REGEX.search(url) and m3u8_holder["val"] is None:
                m3u8_holder["val"] = url
                print(f"[CAPTURE][response] {url}")

        context.on("request", on_request)
        context.on("response", on_response)

        # الدالة التي نحقنها في كل صفحة/إطار
        context.add_init_script(INIT_PATCH_JS)
        # قناة رجوع من JS → بايثون
        context.expose_function("__reportM3U8", lambda u: on_console(type("X", (), {"text": lambda: "M3U8::"+u})()))

        page = context.new_page()

        print(f"[NAV] Goto watch page: {WATCH_URL}")
        page.goto(WATCH_URL, wait_until="domcontentloaded", timeout=45000)

        # انقر زر Player 6 في صفحة المشاهدة
        try:
            locator = page.locator(f"button[title='{BUTTON_TITLE}']")
            if locator.count() == 0:
                locator = page.get_by_text(BUTTON_TITLE, exact=False)
            locator.first.click(timeout=8000)
            print(f"[CLICK] Clicked '{BUTTON_TITLE}'")
        except Exception:
            print("[CLICK] Could not click watch-page button; may open player directly.")

        # إلى صفحة المشغل
        print(f"[NAV] Goto player page: {player_url}")
        page.goto(player_url, wait_until="domcontentloaded", timeout=45000)

        # تفاعل وتحفيز تشغيل
        try:
            page.mouse.click(200, 200)
            page.keyboard.press("Space")
            page.keyboard.press("KeyK")
        except Exception:
            pass

        # حَقِن الباتش داخل أي إطار يُضاف لاحقًا وحاول نقر زر Play شائع
        seen_frames = set()

        def poke_frame(fr):
            if fr in seen_frames:
                return
            seen_frames.add(fr)
            try:
                fr.add_init_script(INIT_PATCH_JS)
            except Exception:
                pass
            for sel in ["button[aria-label='Play']", ".jw-icon-playback", ".vjs-big-play-button", ".plyr__control--overlaid"]:
                try:
                    fr.click(sel, timeout=1500)
                except Exception:
                    pass
            try:
                fr.evaluate("""() => { const v = document.querySelector('video'); if (v){ v.muted = true; v.play?.(); } }""")
            except Exception:
                pass

        # إطارات موجودة حاليًا
        for fr in page.frames:
            if fr != page.main_frame:
                poke_frame(fr)

        # انتظر حتى CAPTURE_TIMEOUT_SEC أو العثور على m3u8
        end_time = time.time() + CAPTURE_TIMEOUT_SEC
        last_poke = 0
        while time.time() < end_time and not m3u8_holder["val"]:
            # إطارات جديدة
            for fr in page.frames:
                if fr != page.main_frame:
                    poke_frame(fr)
            if time.time() - last_poke > 3:
                try:
                    page.mouse.click(220, 220)
                    page.keyboard.press("Space")
                except Exception:
                    pass
                last_poke = time.time()
            time.sleep(0.25)

        # Fallback: بحث بالنص
        if not m3u8_holder["val"]:
            try:
                html = page.content()
                m = M3U8_REGEX.search(html or "")
                if m:
                    m3u8_holder["val"] = m.group(0)
                    print("[FALLBACK] Captured from HTML content.")
            except Exception:
                pass

        # اكتب الدامب
        try:
            with open("network_dump.txt", "w", encoding="utf-8") as f:
                for u in captured_urls:
                    f.write(u + "\n")
        except Exception:
            pass

        context.close()
        browser.close()

        if not m3u8_holder["val"]:
            raise ValueError("تعذر استخراج رابط m3u8 من حركة الشبكة (Playwright).")

        return m3u8_holder["val"]

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
        # اكتب الدامب حتى عند الفشل
        try:
            with open("network_dump.txt", "w", encoding="utf-8") as f:
                for u in captured_urls:
                    f.write(u + "\n")
        except Exception:
            pass
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
