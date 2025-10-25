import os
import re
import sys
import time
import contextlib

from seleniumwire import webdriver  # يلتقط كل الشبكة عبر بروكسي داخلي
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager
import requests

WATCH_URL = os.getenv("WATCH_URL", "https://dlhd.dad/watch.php?id=91")
BUTTON_TITLE = os.getenv("BUTTON_TITLE", "PLAYER 6")
M3U_PATH = "bein.m3u"
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
CAPTURE_TIMEOUT_SEC = int(os.getenv("CAPTURE_TIMEOUT_SEC", "90"))

M3U8_RE = re.compile(r'https?://[^\s\'"]+\.m3u8(?:\?[^\s\'"]*)?', re.IGNORECASE)

def build_driver():
    chrome_opts = Options()
    # تشغيل Headless حديث (يدعم مزايا أحدث)
    chrome_opts.add_argument("--headless=new")
    chrome_opts.add_argument("--disable-gpu")
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--disable-dev-shm-usage")
    chrome_opts.add_argument("--disable-features=AutomationControlled")
    chrome_opts.add_argument("--window-size=1280,800")
    chrome_opts.add_argument("--lang=en-US")
    chrome_opts.add_argument("--mute-audio")
    # مهم لبعض المواقع
    chrome_opts.add_argument("--autoplay-policy=no-user-gesture-required")

    # قدرات لجمع الأداء/الشبكة من CDP أيضًا (ازدواج للضمان)
    caps = DesiredCapabilities.CHROME.copy()
    caps["goog:loggingPrefs"] = {"performance": "ALL", "browser": "ALL"}

    # إعدادات selenium-wire (بروكسي داخلي لالتقاط HTTPS أيضاً)
    seleniumwire_opts = {
        "request_storage": "memory",
        "request_storage_max_size": 3000,
        # بإمكانك تمرير headers ثابتة هنا إن احتجت
    }

    driver = webdriver.Chrome(
        driver=ChromeDriverManager().install(),
        options=chrome_opts,
        seleniumwire_options=seleniumwire_opts,
        desired_capabilities=caps,
    )
    driver.set_page_load_timeout(45)
    driver.implicitly_wait(8)
    return driver

def get_m3u8_from_requests(driver):
    # افحص الطلبات الحية
    for req in driver.requests:
        url = getattr(req, "url", "") or ""
        if M3U8_RE.search(url):
            return url
    return None

def click_player_6(driver):
    # حاول بالنص/العنوان/الـ data-url
    # 1) حسب العنوان
    with contextlib.suppress(Exception):
        btn = driver.find_element(By.CSS_SELECTOR, f"button[title='{BUTTON_TITLE}']")
        btn.click()
        return True
    # 2) حسب النص
    with contextlib.suppress(Exception):
        btns = driver.find_elements(By.CSS_SELECTOR, "button")
        for b in btns:
            txt = (b.text or "").strip().lower()
            if "player 6" in txt:
                b.click()
                return True
    return False

def stimulate_play(driver):
    # بعض المشغلات تحتاج تفاعل/کیبورد
    try:
        ActionChains(driver).move_by_offset(200, 200).click().perform()
        ActionChains(driver).send_keys(Keys.SPACE).perform()
        ActionChains(driver).send_keys("k").perform()
    except Exception:
        pass

def validate_m3u8(url):
    try:
        r = requests.get(url, timeout=20, stream=True)
        if r.status_code >= 400:
            return False
        chunk = next(r.iter_content(chunk_size=2048), b"")
        return (b"#EXTM3U" in chunk) or url.lower().endswith(".m3u8")
    except Exception:
        return False

def update_bein_m3u(file_path, new_url):
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    # ابحث عن سطر اسم القناة
    idx = None
    for i, line in enumerate(lines):
        if "bein sports 1" in line.lower():
            idx = i
            break
    if idx is None:
        raise ValueError("تعذر العثور على قناة 'bein sports 1' داخل bein.m3u")

    # السطر التالي عادةً هو الرابط
    url_line = None
    for j in range(idx + 1, min(idx + 6, len(lines))):
        if lines[j].strip().startswith("http"):
            url_line = j
            break
    if url_line is None:
        lines.insert(idx + 1, new_url)
    else:
        if lines[url_line].strip() == new_url.strip():
            return False
        lines[url_line] = new_url

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return True

def main():
    print(f"[NAV] {WATCH_URL}")
    driver = build_driver()
    try:
        driver.get(WATCH_URL)

        # اضغط Player 6
        clicked = click_player_6(driver)
        print(f"[CLICK] Player 6: {'OK' if clicked else 'NOT FOUND (continuing)'}")

        # أعطِ الصفحة فرصة لتحميل الـ player
        time.sleep(2)

        # حاول فتح صفحة المشغل مباشرة إذا كان موجود رابط data-url داخلي (fallback)
        with contextlib.suppress(Exception):
            # نحاول التقاط redirect لصفحة المشغل من DOM (بدون parsing ثقيل)
            links = driver.find_elements(By.CSS_SELECTOR, "button.player-btn,[data-url]")
            for el in links:
                url = el.get_attribute("data-url") or ""
                if "stream-91.php" in url:
                    from urllib.parse import urljoin
                    player_url = urljoin(WATCH_URL, url)
                    driver.get(player_url)
                    print(f"[NAV] Player page: {player_url}")
                    break

        # حفّز التشغيل
        stimulate_play(driver)

        # انتظر لحد ما يظهر طلب m3u8 في الشبكة
        deadline = time.time() + CAPTURE_TIMEOUT_SEC
        captured = None
        seen = set()
        while time.time() < deadline and not captured:
            # فتّش الطلبات الجديدة فقط
            for req in driver.requests:
                if req.id in seen:
                    continue
                seen.add(req.id)
                url = getattr(req, "url", "") or ""
                if M3U8_RE.search(url):
                    captured = url
                    break
            if not captured:
                time.sleep(0.25)

        if not captured:
            raise RuntimeError("تعذر استخراج رابط m3u8 من حركة الشبكة (Selenium-Wire).")

        print(f"[OK] Extracted m3u8: {captured}")

        # تحقق خفيف
        print(f"[CHECK] Validation: {'PASS' if validate_m3u8(captured) else 'WARN'}")

        if DRY_RUN:
            print("[DRY-RUN] لن يتم تعديل bein.m3u في هذا الوضع.")
            return

        changed = update_bein_m3u(M3U_PATH, captured)
        print("[WRITE] bein.m3u updated." if changed else "[WRITE] No change needed.")
    finally:
        with contextlib.suppress(Exception):
            driver.quit()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
