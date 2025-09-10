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

DEBUG = os.environ.get("DEBUG_MATCHES", "0") == "1"

EXC_LEAGUE = CFG.get("exclude_if_league_matches") or []
EXC_TEAM   = CFG.get("exclude_if_team_matches") or []
ALLOW_COMP = CFG.get("allowed_competitions") or []
ALLOW_LEAGUES_RX = [re.compile(p, re.I) for p in CFG.get("allowed_leagues", [])]

PRIORITY_PATS = [re.compile(p, re.I) for p in CFG.get("channel_priority_patterns", [])]
LEAGUE_RULES  = CFG.get("league_channels") or []
PREF_COUNTRIES_RX = [re.compile(p, re.I) for p in CFG.get("tv_preferred_countries", [])]
BROADCAST_MAP = [(re.compile(p, re.I), v) for p, v in (CFG.get("broadcaster_map") or {}).items()]

# ---------- مفاتيح SoccersAPI ----------
SA_USER  = os.environ.get("SOCCERAPI_USER", "")
SA_TOKEN = os.environ.get("SOCCERAPI_TOKEN", "")
if not SA_USER or not SA_TOKEN:
    print("ERROR: missing SOCCERAPI_USER or SOCCERAPI_TOKEN", file=sys.stderr)
    sys.exit(1)

SA_BASE = "https://api.soccersapi.com/v2.2"

# ---------- أدوات ----------
def utc_iso(x: dt.datetime) -> str:
    return x.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")

def map_status(short: str) -> str:
    s = (short or "").upper()
    if s in ("NS", "TBD", "PST", "CANC", "POSTP", "SUSP", "INT"):
        return "NS"
    if s in ("FT", "AET", "PEN", "FT_PEN", "AFTER_PEN"):
        return "FT"
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
    for p in patterns:
        if re.search(p, text or "", flags=re.I):
            return True
    return False

def match_allowed(league_name: str, league_country: str) -> bool:
    if ALLOW_COMP:
        for comp in ALLOW_COMP:
            name_rx    = comp.get("name")
            country_rx = comp.get("country")
            ok_name    = re.search(name_rx, league_name or "", re.I) if name_rx else True
            ok_country = re.search(country_rx, league_country or "", re.I) if country_rx else True
            if ok_name and ok_country:
                return True
        return False
    return any(rx.search(league_name or "") for rx in ALLOW_LEAGUES_RX)

def collect_candidates(league_name: str) -> list[str]:
    out, seen = [], set()
    for rule in LEAGUE_RULES:
        if re.search(rule.get("if_league",""), league_name or "", re.I):
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
    cands = collect_candidates(league_name)
    return apply_priority(cands) or "beIN Sports 1"

def map_broadcaster_to_app(name: str | None) -> str | None:
    if not name: return None
    for rx, target in BROADCAST_MAP:
        if rx.search(name): return target
    return None

# ---------- استدعاءات API ----------
def sa_get(path: str, **params):
    q = {"user": SA_USER, "token": SA_TOKEN, **params}
    r = requests.get(f"{SA_BASE}{path}", params=q, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"SoccersAPI {path} failed: {r.status_code} {r.text[:200]}")
    return r.json()

def sa_broadcast_schedule(date_iso: str):
    data = sa_get("/broadcast/", t="schedule", d=date_iso, include="tvs")
    return data.get("data") or []

def sa_match_tvs(match_id: str | int):
    data = sa_get("/broadcast/", t="match_tvs", match_id=match_id)
    return data.get("data") or []

def pick_channel_from_tvstations(stations: list[dict]) -> tuple[str | None, str | None]:
    if not stations: return (None, None)
    for rx in PREF_COUNTRIES_RX:
        for st in stations:
            cname = st.get("name") or st.get("tvstation") or ""
            country = (st.get("country") or {}).get("name","") or st.get("country_name","") or ""
            if rx.search(country):
                return (cname, map_broadcaster_to_app(cname))
    cname = (stations[0] or {}).get("name") or (stations[0] or {}).get("tvstation")
    return (cname, map_broadcaster_to_app(cname))

# ---------- Main ----------
def main():
    forced = os.environ.get("FORCE_DATE")
    if forced:
        today_local = dt.date.fromisoformat(forced)
    else:
        today_local = dt.datetime.now(TZ).date()
    date_iso = today_local.isoformat()

    schedule = sa_broadcast_schedule(date_iso)

    out = {"date": date_iso, "matches": []}
    for it in schedule:
        league_name = (it.get("league") or {}).get("name") or it.get("league_name") or ""
        league_cty  = (it.get("league") or {}).get("country", {}).get("name") or it.get("country_name") or ""

        if any_match(league_name, EXC_LEAGUE): continue

        home = (it.get("home") or {}).get("name") or it.get("home_name") or ""
        away = (it.get("away") or {}).get("name") or it.get("away_name") or ""
        if any_match(home, EXC_TEAM) or any_match(away, EXC_TEAM): continue

        if not match_allowed(league_name, league_cty): continue

        dt_str = ((it.get("time") or {}).get("utc") or it.get("kickoff_utc") or "").strip()
        if not dt_str: continue
        start_utc = dt.datetime.fromisoformat(dt_str.replace("Z","+00:00")).astimezone(dt.timezone.utc)
        if not within_today_local(start_utc): continue

        status_short = (it.get("status") or {}).get("short") or it.get("status") or ""
        status = map_status(status_short)
        score = None
        if status == "FT":
            scores = it.get("scores") or {}
            h = scores.get("home"); a = scores.get("away")
            if isinstance(h,int) and isinstance(a,int): score = f"{h}-{a}"

        tvs = it.get("tvs") if isinstance(it.get("tvs"), list) else []
        channel_src, channel_app = pick_channel_from_tvstations(tvs)
        if not channel_app:
            match_id = it.get("id") or it.get("match_id")
            if match_id: 
                stations = sa_match_tvs(match_id)
                channel_src, channel_app = pick_channel_from_tvstations(stations)
        if not channel_app:
            channel_app = fallback_channel(league_name)

        out["matches"].append({
            "id": f"{home[:10]}-{away[:10]}-{date_iso}".replace(" ", ""),
            "home": home,
            "away": away,
            "league": league_name,
            "league_country": league_cty,
            "channel_src": channel_src,
            "channel": channel_app,
            "start_utc": utc_iso(start_utc),
            "status": status,
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
