import os, re, json, sys, datetime as dt
from zoneinfo import ZoneInfo
import requests, yaml

def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def utc_iso(dt_utc: dt.datetime) -> str:
    return dt_utc.replace(tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")

def map_status(api_status: str) -> str:
    s = (api_status or "").upper()
    if s in ("NS", "PST", "CANC"): return "NS"
    if s in ("FT", "AET", "PEN"):  return "FT"
    return "LIVE"

def within_today_local(kickoff_utc: dt.datetime, tz: ZoneInfo) -> bool:
    local = kickoff_utc.astimezone(tz)
    start = dt.datetime(local.year, local.month, local.day, tzinfo=tz)
    end = start + dt.timedelta(days=1)
    return start <= local < end

def fetch_apifootball(date_local: dt.date, api_key: str):
    url = "https://v3.football.api-sports.io/fixtures"
    params = {"date": date_local.isoformat(), "timezone": "UTC"}
    headers = {"x-apisports-key": api_key}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("response", [])

def _collect_candidates(league_name: str, league_rules: list[dict]) -> list[str]:
    cands = []
    for rule in league_rules or []:
        pat = rule.get("if_league")
        lst = rule.get("candidates") or []
        if pat and re.search(pat, league_name or "", flags=re.IGNORECASE):
            cands.extend(lst)
    # unique keep order
    seen, out = set(), []
    for x in cands:
        if x not in seen:
            seen.add(x); out.append(x)
    return out

def _apply_priority(candidates: list[str], priority_patterns: list[str]) -> str | None:
    for pat in priority_patterns or []:
        rx = re.compile(pat, flags=re.IGNORECASE)
        for c in candidates:
            if rx.search(c): return c
    return candidates[0] if candidates else None

def pick_channel(league_name: str, home: str, away: str, cfg: dict) -> str:
    key = f"{home}|{away}"
    for pattern, ch in (cfg.get("overrides") or {}).items():
        if re.search(pattern, key, flags=re.IGNORECASE):
            return ch
    cands = _collect_candidates(league_name, cfg.get("league_channels"))
    chosen = _apply_priority(cands, cfg.get("channel_priority_patterns"))
    return chosen or "beIN Sports 1"

def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_path = os.path.join(repo_root, "matches", "today.json")
    cfg_path = os.path.join(repo_root, "scripts", "config.yaml")

    cfg = load_config(cfg_path)
    tz = ZoneInfo(cfg.get("timezone", "Asia/Baghdad"))
    allow_patterns = [re.compile(p, flags=re.IGNORECASE) for p in cfg.get("allowed_leagues", [])]

    api_key = os.environ.get("APIFOOTBALL_KEY")
    if not api_key:
        print("ERROR: missing APIFOOTBALL_KEY", file=sys.stderr)
        sys.exit(1)

    today_local = dt.datetime.now(tz).date()
    fixtures = fetch_apifootball(today_local, api_key)

    result = {"date": today_local.isoformat(), "matches": []}

    for fx in fixtures:
        league = (fx.get("league") or {}).get("name") or ""
        if not any(p.search(league) for p in allow_patterns):
            continue

        teams = fx.get("teams") or {}
        home = (teams.get("home") or {}).get("name") or ""
        away = (teams.get("away") or {}).get("name") or ""

        dt_str = ((fx.get("fixture") or {}).get("date") or "").strip()
        if not dt_str: continue
        start_utc = dt.datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
        if not within_today_local(start_utc, tz): continue

        status_short = ((fx.get("fixture") or {}).get("status") or {}).get("short") or ""
        status = map_status(status_short)

        goals = fx.get("goals") or {}
        score = None
        if status == "FT":
            hg, ag = goals.get("home"), goals.get("away")
            if isinstance(hg, int) and isinstance(ag, int):
                score = f"{hg}-{ag}"

        channel = pick_channel(league, home, away, cfg)
        match_id = f"{home[:10]}-{away[:10]}-{today_local.isoformat()}".replace(" ", "")

        result["matches"].append({
            "id": match_id,
            "home": home,
            "away": away,
            "channel": channel,
            "start_utc": utc_iso(start_utc),
            "status": status,   # NS / LIVE / FT
            "score": score
        })

    result["matches"].sort(key=lambda m: m["start_utc"])

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Wrote {out_path} with {len(result['matches'])} matches.")

if __name__ == "__main__":
    main()
