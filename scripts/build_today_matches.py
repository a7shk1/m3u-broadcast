# scripts/build_today_matches.py
import os, re, json, sys, datetime as dt
from zoneinfo import ZoneInfo
import requests, yaml

# ---------- تحميل الإعدادات ----------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH  = os.path.join(REPO_ROOT, "scripts", "config.yaml")
OUT_PATH  = os.path.join(REPO_ROOT, "matches", "today.json")

with open(CFG_PATH, "r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

TZ = ZoneInfo(CFG.get("timezone", "Asia/Baghdad"))

# استثناءات
EXC_LEAGUE = CFG.get("exclude_if_league_matches") or []
EXC_TEAM   = CFG.get("exclude_if_team_matches") or []

# فلاتر المسموح (اسم+دولة) أو أسماء فقط كاحتياطي
ALLOW_COMP = CFG.get("allowed_competitions") or []
ALLOW_LEAGUES_RX = [re.compile(p, re.I) for p in CFG.get("allowed_leagues", [])]

# أولوية القنوات الافتراضية + مرشّحات حسب البطولة
PRIORITY_PATS = [re.compile(p, re.I) for p in CFG.get("channel_priority_patterns", [])]
LEAGUE_RULES  = CFG.get("league_channels") or []

# تفضيلات بلد القناة + ماب تحويل الاسم لاسم قناتك
PREF_COUNTRIES_RX = [re.compile(p, re.I) for p in CFG.get("tv_preferred_countries", [])]
BROADCAST_MAP = [(re.compile(p, re.I), v) for p, v in (CFG.get("broadcaster_map") or {}).items()]

# المفاتيح
API_KEY = os.environ.get("APIFOOTBALL_KEY", "")
SM_TOKEN= os.environ.get("SPORTMONKS_TOKEN", "")

if not API_KEY:
    print("ERROR: missing APIFOOTBALL_KEY", file=sys.stderr); sys.exit(1)

# ---------- أدوات مساعدة ----------
def utc_iso(x: dt.datetime) -> str:
    return x.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")

def map_status(short: str) -> str:
    s = (short or "").upper()
    if s in ("NS", "TBD", "PST", "CANC"): return "NS"
    if s in ("FT", "AET", "PEN"):         return "FT"
    return "LIVE"

def human_status(short: str) -> str:
    s = map_status(short)
    return {"NS": "قريبًا", "LIVE": "جارية الآن", "FT": "انتهت"}.get(s, "غير معلوم")

def within_today_local(start_utc: dt.datetime) -> bool:
    local = start_utc.astimezone(TZ)
    start = dt.datetime(local.year, local.month, local.day, tzinfo=TZ)
    end   = start + dt.timedelta(days=1)
    return start <= local < end

def any_match(text: str, patterns: list[str]) -> bool:
    t = text or ""
    for p in patterns:
        if re.search(p, t, flags=re.I):
            return True
    return False

def match_allowed(league_name: str, league_country: str) -> bool:
    # إذا محدد allowed_competitions (اسم + دولة)
    if ALLOW_COMP:
        for comp in ALLOW_COMP:
            name_rx    = comp.get("name")
            country_rx = comp.get("country")
            ok_name    = re.search(name_rx, league_name or "", re.I) if name_rx else True
            ok_country = re.search(country_rx, league_country or "", re.I) if country_rx else True
            if ok_name and ok_country:
                return True
        return False
    # احتياطي: allowed_leagues فقط بالاسم
    return any(rx.search(league_name or "") for rx in ALLOW_LEAGUES_RX)

def collect_candidates(league_name: str) -> list[str]:
    out, seen = [], set()
    for rule in LEAGUE_RULES:
        pat = rule.get("if_league")
        if pat and re.search(pat, league_name or "", re.I):
            for c in rule.get("candidates") or []:
                if c not in seen:
                    seen.add(c); out.append(c)
    return out

def apply_priority(cands: list[str]) -> str | None:
    for rx in PRIORITY_PATS:
        for c in cands:
            if rx.search(c): return c
    return cands[0] if cands else None

def fallback_channel(league_name: str) -> str:
    # نعتمد مرشّحات + أولوية
    cands = collect_candidates(league_name)
    ch = apply_priority(cands)
    return ch or "beIN Sports 1"

def map_broadcaster_to_app(name: str | None) -> str | None:
    if not name: return None
    for rx, target in BROADCAST_MAP:
        if rx.search(name): return target
    return None

# ---------- API Calls ----------
def fetch_fixtures_apifootball(date_iso: str):
    url = "https://v3.football.api-sports.io/fixtures"
    headers = {"x-apisports-key": API_KEY}
    params  = {"date": date_iso, "timezone": "UTC"}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("response", [])

def sportmonks_fixtures_by_date(date_iso: str):
    """نجيب كل مباريات اليوم من سبورت مونكس مع الفرق/الدوري (للمطابقة)."""
    if not SM_TOKEN: return []
    url = f"https://api.sportmonks.com/v3/football/fixtures/date/{date_iso}"
    headers = {"Authorization": SM_TOKEN, "Accept": "application/json"}
    params  = {"include": "participants;league;country"}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    if r.status_code != 200:
        return []
    data = r.json().get("data") or []
    out = []
    for fx in data:
        participants = fx.get("participants") or []
        names = [ (p or {}).get("name","") for p in participants ]
        out.append({
            "id": fx.get("id"),
            "teams": [n.lower().strip() for n in names],
            "league": (fx.get("league") or {}).get("name","") or "",
        })
    return out

def sportmonks_tvstations_for_fixture(fixture_id: int):
    if not SM_TOKEN: return []
    url = f"https://api.sportmonks.com/v3/football/tv-stations/fixtures/{fixture_id}"
    headers = {"Authorization": SM_TOKEN, "Accept": "application/json"}
    r = requests.get(url, headers=headers, timeout=30)
    if r.status_code != 200: return []
    return r.json().get("data") or []

# ---------- مطابقة Fixture بين المصدرين ----------
def match_fixture_id_sm(home: str, away: str, sm_fixtures: list[dict]) -> int | None:
    h, a = (home or "").lower().strip(), (away or "").lower().strip()
    for fx in sm_fixtures:
        t = fx["teams"]
        if h in t and a in t:  # مطابقة مباشرة
            return fx["id"]
    # محاولة ثانية: احتواء جزئي لو أسماء طويلة
    for fx in sm_fixtures:
        join = " ".join(fx["teams"])
        if h in join and a in join:
            return fx["id"]
    return None

def pick_channel_from_tvstations(stations: list[dict]) -> tuple[str | None, str | None]:
    """يرجع (channel_src, channel_app)"""
    if not stations: return (None, None)
    # جرّب حسب تفضيلات الدول أولاً
    for rx in PREF_COUNTRIES_RX:
        for st in stations:
            cname = st.get("name") or ""
            country = (st.get("country") or {}).get("name","") or ""
            if rx.search(country):
                return (cname, map_broadcaster_to_app(cname))
    # وإلا خذ أول عنصر
    cname = (stations[0] or {}).get("name")
    return (cname, map_broadcaster_to_app(cname))

# ---------- Main ----------
def main():
    # التاريخ حسب المنطقة الزمنية (نرسل طلب اليوم نفسه لـ API-FOOTBALL)
    today_local = dt.datetime.now(TZ).date()
    date_iso = today_local.isoformat()

    fixtures = fetch_fixtures_apifootball(date_iso)
    sm_fixtures = sportmonks_fixtures_by_date(date_iso)  # قد تكون []

    out = {"date": date_iso, "matches": []}

    for fx in fixtures:
        league_obj = fx.get("league") or {}
        league     = league_obj.get("name") or ""
        league_cty = league_obj.get("country") or ""

        # استبعاد شباب/سيدات/رديف بالاسم
        if any_match(league, EXC_LEAGUE): continue

        teams = fx.get("teams") or {}
        home  = (teams.get("home") or {}).get("name") or ""
        away  = (teams.get("away") or {}).get("name") or ""
        if any_match(home, EXC_TEAM) or any_match(away, EXC_TEAM):
            continue

        # قبول البطولة؟
        if not match_allowed(league, league_cty):
            continue

        # وقت البداية ضمن "اليوم" المحلي
        dt_str = ((fx.get("fixture") or {}).get("date") or "").strip()
        if not dt_str: 
            continue
        start_utc = dt.datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        if not within_today_local(start_utc):
            continue

        # الحالة والنتيجة النهائية إن وُجدت
        status_short = ((fx.get("fixture") or {}).get("status") or {}).get("short","") or ""
        status = map_status(status_short)

        goals = fx.get("goals") or {}
        score = None
        if status == "FT" and isinstance(goals.get("home"), int) and isinstance(goals.get("away"), int):
            score = f"{goals['home']}-{goals['away']}"

        # قناة من Sportmonks (إن أمكن)
        channel_src = None
        channel_app = None
        if sm_fixtures and SM_TOKEN:
            sm_id = match_fixture_id_sm(home, away, sm_fixtures)
            if sm_id:
                stations = sportmonks_tvstations_for_fixture(sm_id)
                channel_src, channel_app = pick_channel_from_tvstations(stations)

        # fallback لو ما لقيت قناة من Sportmonks
        if not channel_app:
            channel_app = fallback_channel(league)

        # اكتب النتيجة
        out["matches"].append({
            "id": f"{home[:10]}-{away[:10]}-{date_iso}".replace(" ", ""),
            "home": home,
            "away": away,
            "league": league,
            "league_country": league_cty,
            "channel_src": channel_src,         # القناة الرسمية من المزود (قد تكون None)
            "channel": channel_app,             # اسم قناتك داخل التطبيق
            "start_utc": utc_iso(start_utc),
            "status": status,                   # NS/LIVE/FT
            "status_label": human_status(status_short),
            "score": score
        })

    out["matches"].sort(key=lambda m: m["start_utc"])
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Wrote {OUT_PATH} with {len(out['matches'])} matches.")

if __name__ == "__main__":
    main()
