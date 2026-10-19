#!/usr/bin/env python3
"""ScoreBuzz static SEO site generator.

Fetches live sports data from ESPN's free public API and generates a fully
static, crawlable website into dist/: one HTML page per match (all content
baked in, no client-side fetching), league pages, team pages, sitemap.xml
and robots.txt.

Idempotent: re-running produces the same URLs for the same events
(eventId-based), so Google indexing persists across runs. Designed to run
on a 15-minute schedule.

Usage: python3 generate.py
"""
import json
import os
import re
import sys
import time
import html as htmllib
from datetime import datetime, timedelta, timezone

try:
    import requests
    HAVE_REQUESTS = True
except ImportError:
    HAVE_REQUESTS = False
    import urllib.request

BASE_URL = "https://scorebuzz.net"
SITE_NAME = "ScoreBuzz"
GA_MEASUREMENT_ID = "G-3HJBET23YB"  # GA4 property "ScoreBuzz Web" (leonmessi account)
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "dist")

# Standard browser UA: Akamai blocks obvious bot UAs with 403.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
REQ_TIMEOUT = 20
REQ_DELAY = 0.2          # polite pause between API calls
SUMMARY_WINDOW_DAYS = 7  # summaries for live + matches within +-7 days
MAX_SUMMARIES_PER_LEAGUE = 50

# (espn_sport, espn_league, display name, url slug, sport label, espncdn logo sport)
LEAGUES = [
    ("soccer", "eng.1", "Premier League", "england-premier-league", "Football", "soccer"),
    ("soccer", "esp.1", "La Liga", "la-liga", "Football", "soccer"),
    ("soccer", "ita.1", "Serie A", "serie-a", "Football", "soccer"),
    ("soccer", "ger.1", "Bundesliga", "bundesliga", "Football", "soccer"),
    ("soccer", "fra.1", "Ligue 1", "ligue-1", "Football", "soccer"),
    ("soccer", "uefa.champions", "Champions League", "champions-league", "Football", "soccer"),
    ("soccer", "uefa.europa", "Europa League", "europa-league", "Football", "soccer"),
    ("soccer", "ned.1", "Eredivisie", "eredivisie", "Football", "soccer"),
    ("soccer", "por.1", "Primeira Liga", "primeira-liga", "Football", "soccer"),
    ("soccer", "eng.2", "Championship", "championship", "Football", "soccer"),
    ("soccer", "usa.1", "MLS", "mls", "Football", "soccer"),
    ("football", "nfl", "NFL", "nfl", "American Football", "nfl"),
    ("football", "college-football", "College Football", "college-football", "American Football", "nfl"),
    ("basketball", "nba", "NBA", "nba", "Basketball", "nba"),
    ("basketball", "wnba", "WNBA", "wnba", "Basketball", "nba"),
    ("basketball", "mens-college-basketball", "NCAA Basketball", "ncaa-basketball", "Basketball", "nba"),
    ("hockey", "nhl", "NHL", "nhl", "Ice Hockey", "nhl"),
    ("baseball", "mlb", "MLB", "mlb", "Baseball", "mlb"),
    ("tennis", "atp", "ATP", "atp", "Tennis", "tennis"),
    ("tennis", "wta", "WTA", "wta", "Tennis", "tennis"),
    ("mma", "ufc", "UFC", "ufc", "MMA", "mma"),
    ("soccer", "mex.1", "Liga MX", "liga-mx", "Football", "soccer"),
    ("soccer", "bra.1", "Brasileirão", "brasileirao", "Football", "soccer"),
    ("soccer", "arg.1", "Argentine Primera", "argentina-primera-division", "Football", "soccer"),
    ("soccer", "tur.1", "Süper Lig", "super-lig", "Football", "soccer"),
    ("soccer", "jpn.1", "J-League", "j-league", "Football", "soccer"),
    ("soccer", "kor.1", "K League", "k-league-1", "Football", "soccer"),
    ("soccer", "aus.1", "A-League", "a-league", "Football", "soccer"),
    ("soccer", "sau.1", "Saudi Pro League", "saudi-pro-league", "Football", "soccer"),
    ("soccer", "ind.1", "Indian Super League", "indian-super-league", "Football", "soccer"),
    ("soccer", "uefa.nations", "UEFA Nations League", "uefa-nations-league", "Football", "soccer"),
    ("soccer", "fifa.worldcup", "FIFA World Cup", "fifa-world-cup", "Football", "soccer"),
    ("soccer", "eng.w.1", "Women's Super League", "womens-super-league", "Football", "soccer"),
    ("soccer", "usa.w.1", "NWSL", "nwsl", "Football", "soccer"),
    ("soccer", "fra.w.1", "Première Ligue", "premiere-ligue", "Football", "soccer"),
    ("soccer", "esp.w.1", "Liga F", "liga-f", "Football", "soccer"),
    ("soccer", "sco.1", "Scottish Premiership", "scottish-premiership", "Football", "soccer"),
    ("soccer", "den.1", "Danish Superliga", "danish-superliga", "Football", "soccer"),
    ("soccer", "nor.1", "Norwegian Eliteserien", "eliteserien", "Football", "soccer"),
    ("soccer", "swe.1", "Swedish Allsvenskan", "allsvenskan", "Football", "soccer"),
    ("soccer", "sui.1", "Swiss Super League", "swiss-super-league", "Football", "soccer"),
    ("soccer", "aut.1", "Austrian Bundesliga", "austrian-bundesliga", "Football", "soccer"),
    ("soccer", "bel.1", "Belgian Pro League", "belgian-pro-league", "Football", "soccer"),
    ("soccer", "gre.1", "Greek Super League", "greek-super-league", "Football", "soccer"),
    ("soccer", "rou.1", "Romanian Liga I", "romanian-liga-1", "Football", "soccer"),
    ("soccer", "chn.1", "Chinese Super League", "chinese-super-league", "Football", "soccer"),
    ("soccer", "conmebol.libertadores", "Copa Libertadores", "copa-libertadores", "Football", "soccer"),
    ("soccer", "conmebol.sudamericana", "Copa Sudamericana", "copa-sudamericana", "Football", "soccer"),
    ("soccer", "chi.1", "Chilean Primera División", "chile-primera-division", "Football", "soccer"),
    ("soccer", "col.1", "Colombian Primera A", "colombia-primera-a", "Football", "soccer"),
    ("soccer", "ecu.1", "LigaPro Ecuador", "ligapro-ecuador", "Football", "soccer"),
    ("soccer", "per.1", "Peruvian Liga 1", "peru-liga-1", "Football", "soccer"),
    ("soccer", "uru.1", "Liga AUF Uruguaya", "uruguay-primera-division", "Football", "soccer"),
    ("soccer", "par.1", "Paraguayan Primera División", "paraguay-primera-division", "Football", "soccer"),
    ("soccer", "ven.1", "Venezuelan Primera División", "venezuela-primera-division", "Football", "soccer"),
    ("soccer", "crc.1", "Costa Rican Primera División", "costa-rica-primera-division", "Football", "soccer"),
    ("soccer", "wal.1", "Welsh Premier League", "welsh-premier-league", "Football", "soccer"),
    ("soccer", "nir.1", "Northern Irish Premiership", "northern-irish-premiership", "Football", "soccer"),
    ("soccer", "irl.1", "Irish Premier Division", "league-of-ireland", "Football", "soccer"),
    ("soccer", "cyp.1", "Cypriot First Division", "cyprus-first-division", "Football", "soccer"),
    ("soccer", "isr.1", "Israeli Premier League", "israeli-premier-league", "Football", "soccer"),
    ("soccer", "ned.w.1", "Dutch Vrouwen Eredivisie", "vrouwen-eredivisie", "Football", "soccer"),
]
# NOTE: /cricket/<series-slug>/<match-slug>-<id>/ URL space is RESERVED for a
# future cricket feed integration. Do not use /cricket/ for anything else.

STATS = {"scoreboards": 0, "summaries": 0, "failed": 0, "failed_leagues": []}

# League prestige weights for the trending bar (higher = more prominent).
LEAGUE_WEIGHT = {
    "champions-league": 100, "england-premier-league": 95, "la-liga": 90,
    "nfl": 88, "serie-a": 85, "bundesliga": 84, "nba": 82, "ligue-1": 80,
    "europa-league": 78, "mlb": 75, "nhl": 75, "ufc": 72, "mls": 70,
    "eredivisie": 68, "primeira-liga": 66, "championship": 64, "wnba": 60,
    "college-football": 58, "ncaa-basketball": 55, "atp": 50, "wta": 50,
    "liga-mx": 62, "brasileirao": 66, "argentina-primera-division": 60,
    "super-lig": 64, "saudi-pro-league": 63, "j-league": 52, "k-league-1": 50,
    "a-league": 48, "indian-super-league": 45, "uefa-nations-league": 75,
    "fifa-world-cup": 90, "womens-super-league": 55, "nwsl": 52,
    "premiere-ligue": 48, "liga-f": 48, "scottish-premiership": 58,
    "danish-superliga": 50, "eliteserien": 50, "allsvenskan": 50,
    "swiss-super-league": 52, "austrian-bundesliga": 52,
    "belgian-pro-league": 54, "greek-super-league": 50,
    "romanian-liga-1": 46, "chinese-super-league": 44,
    "copa-libertadores": 72, "copa-sudamericana": 62,
    "chile-primera-division": 50, "colombia-primera-a": 52,
    "ligapro-ecuador": 48, "peru-liga-1": 48, "uruguay-primera-division": 50,
    "paraguay-primera-division": 46, "venezuela-primera-division": 46,
    "costa-rica-primera-division": 44, "welsh-premier-league": 42,
    "northern-irish-premiership": 42, "league-of-ireland": 44,
    "cyprus-first-division": 44, "israeli-premier-league": 46,
    "vrouwen-eredivisie": 44,
}
DERBY_BONUS = 40
# Famous rivalry pairs (lowercase substrings matched against team names).
DERBY_PAIRS = [
    ("real madrid", "barcelona"), ("real madrid", "atletico"),
    ("barcelona", "espanyol"), ("sevilla", "betis"),
    ("manchester united", "manchester city"), ("liverpool", "everton"),
    ("arsenal", "tottenham"), ("inter", "milan"), ("roma", "lazio"),
    ("juventus", "torino"), ("bayern", "dortmund"),
    ("paris", "marseille"), ("ajax", "psv"),
    ("cowboys", "eagles"), ("packers", "bears"),
    ("lakers", "clippers"), ("celtics", "lakers"),
]


def log(msg):
    print("[%s] %s" % (datetime.now(timezone.utc).strftime("%H:%M:%S"), msg),
          flush=True)


def fetch_json(url):
    """GET JSON with timeout; returns dict or None (never raises)."""
    try:
        if HAVE_REQUESTS:
            r = requests.get(url, headers={"User-Agent": USER_AGENT},
                             timeout=REQ_TIMEOUT)
            if r.status_code != 200:
                return None
            return r.json()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=REQ_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    finally:
        time.sleep(REQ_DELAY)


def slugify(text):
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "match"


def esc(text):
    return htmllib.escape(str(text if text is not None else ""), quote=True)


def parse_dt(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def today_utc():
    return datetime.now(timezone.utc).date()


def fmt_date(dt):
    if not dt:
        return ""
    return dt.strftime("%a %d %b %Y")


def fmt_time(dt):
    if not dt:
        return ""
    return dt.strftime("%H:%M UTC")

# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------

def parse_competitor(c, logo_sport):
    team = c.get("team") or {}
    logo = team.get("logo")
    if not logo and team.get("id"):
        logo = ("https://a.espncdn.com/i/teamlogos/%s/500/%s.png"
                % (logo_sport, team.get("id")))
    records = c.get("records") or []
    return {
        "id": str(team.get("id") or c.get("id") or ""),
        "name": team.get("displayName") or team.get("name") or "",
        "short": team.get("shortDisplayName") or team.get("abbreviation") or "",
        "abbr": team.get("abbreviation") or "",
        "score": c.get("score"),
        "winner": bool(c.get("winner")),
        "home_away": c.get("homeAway") or "",
        "logo": logo or "",
        "color": team.get("color") or "888888",
        "record": records[0].get("summary") if records else "",
        "form": c.get("form") or "",
    }


def parse_event(league, event):
    """Normalise one scoreboard event. Returns None if unusable."""
    comps = event.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    competitors = comp.get("competitors") or []
    if len(competitors) < 2:
        return None
    home = next((c for c in competitors if c.get("homeAway") == "home"),
                competitors[0])
    away = next((c for c in competitors if c.get("homeAway") == "away"),
                competitors[1] if len(competitors) > 1 else competitors[0])
    status = event.get("status") or {}
    stype = status.get("type") or {}
    state = stype.get("state") or "pre"
    venue = comp.get("venue") or event.get("venue") or {}
    addr = venue.get("address") or {}
    dt = parse_dt(event.get("date") or comp.get("date") or "")
    return {
        "id": str(event.get("id")),
        "league": league,
        "name": event.get("name") or "",
        "short_name": event.get("shortName") or "",
        "date": dt,
        "state": state,  # pre | in | post
        "status_text": stype.get("shortDetail") or stype.get("detail") or "",
        "clock": status.get("displayClock") or "",
        "period": status.get("period") or "",
        "home": parse_competitor(home, league["logo_sport"]),
        "away": parse_competitor(away, league["logo_sport"]),
        "venue": venue.get("fullName") or "",
        "city": addr.get("city") or "",
        "country": addr.get("country") or "",
        "attendance": comp.get("attendance"),
    }


def match_slug(m):
    return "%s-vs-%s-%s" % (slugify(m["home"]["short"] or m["home"]["name"]),
                            slugify(m["away"]["short"] or m["away"]["name"]),
                            m["id"])


def match_path(m):
    return "/football/%s/%s/" % (m["league"]["slug"], match_slug(m))


def team_slug(t):
    return "%s-%s" % (slugify(t.get("short") or t.get("name")), t.get("id"))


def status_label(m):
    if m["state"] == "in":
        bit = m["clock"] or m["status_text"] or "LIVE"
        return "LIVE " + bit
    if m["state"] == "post":
        return m["status_text"] or "FT"
    if m["date"]:
        return fmt_date(m["date"]) + " " + fmt_time(m["date"])
    return "Scheduled"


def score_text(m):
    hs, aws = m["home"].get("score"), m["away"].get("score")
    if hs is None or aws is None:
        return "vs"
    return "%s - %s" % (hs, aws)


def parse_summary(data, m):
    """Extract commentary, stats, lineups, form, h2h, leaders from a summary."""
    out = {"commentary": [], "stats": [], "lineups": [],
           "last_five": [], "h2h": [], "leaders": [], "venue_extra": {}}

    # --- commentary / play-by-play ---
    for ke in data.get("keyEvents") or []:
        text = ke.get("text")
        if not text:
            continue
        clock = ""
        c = ke.get("clock") or {}
        if isinstance(c, dict):
            clock = c.get("displayValue") or ""
        cval = c.get("value") if isinstance(c, dict) else None
        team = ke.get("team") or {}
        out["commentary"].append({
            "clock": clock,
            "clock_sec": cval if isinstance(cval, (int, float)) else None,
            "period": (ke.get("period") or {}).get("number") if isinstance(ke.get("period"), dict) else "",
            "type": ((ke.get("type") or {}).get("text") if isinstance(ke.get("type"), dict) else "") or "",
            "scoring": bool(ke.get("scoringPlay")),
            "team_id": str(team.get("id") or ""),
            "team_name": team.get("displayName") or "",
            "text": text,
        })
    # generic 'commentary' list fallback
    if not out["commentary"]:
        for item in data.get("commentary") or []:
            if isinstance(item, str) and item.strip():
                out["commentary"].append({"clock": "", "period": "",
                                          "type": "", "scoring": False,
                                          "text": item})
            elif isinstance(item, dict):
                t = item.get("text") or item.get("description") or ""
                if t:
                    out["commentary"].append({"clock": "", "period": "",
                                              "type": "", "scoring": False,
                                              "text": t})
    # generic 'details' fallback (some sports)
    if not out["commentary"]:
        for det in data.get("details") or []:
            if isinstance(det, dict):
                t = det.get("text") or det.get("shortText") or ""
                if t:
                    out["commentary"].append({"clock": "", "period": "",
                                              "type": "", "scoring": False,
                                              "text": t})

    # --- team stat comparison ---
    bs = data.get("boxscore") or {}
    bsteams = {}
    for t in bs.get("teams") or []:
        bsteams[t.get("homeAway")] = t
    home_stats = {s.get("name"): s for s in
                  (bsteams.get("home") or {}).get("statistics") or []}
    away_stats = {s.get("name"): s for s in
                  (bsteams.get("away") or {}).get("statistics") or []}
    for name, hs in home_stats.items():
        aws = away_stats.get(name)
        out["stats"].append({
            "label": hs.get("label") or hs.get("name") or name,
            "home": hs.get("displayValue") or "",
            "away": (aws.get("displayValue") if aws else "") or "",
        })

    # --- lineups ---
    for r in data.get("rosters") or []:
        team = r.get("team") or {}
        players = []
        for p in r.get("roster") or []:
            ath = p.get("athlete") or {}
            players.append({
                "name": ath.get("displayName") or ath.get("fullName") or "",
                "jersey": p.get("jersey") or "",
                "position": (p.get("position") or {}).get("abbreviation")
                            if isinstance(p.get("position"), dict) else (p.get("position") or ""),
                "starter": bool(p.get("starter")),
            })
        players = [p for p in players if p["name"]]
        if players:
            out["lineups"].append({
                "team_id": str(team.get("id") or ""),
                "team_name": team.get("displayName") or "",
                "formation": r.get("formation") or "",
                "players": players,
            })

    # --- recent form (last five games per team) ---
    for lf in data.get("lastFiveGames") or []:
        team = lf.get("team") or {}
        games = []
        for ge in lf.get("events") or []:
            gcomps = ge.get("competitions") or []
            if not gcomps:
                continue
            gc = gcomps[0]
            gcs = gc.get("competitors") or []
            if len(gcs) < 2:
                continue
            me = next((c for c in gcs
                       if str((c.get("team") or {}).get("id")) == str(team.get("id"))),
                      gcs[0])
            opp = gcs[1] if gcs[0] is me else gcs[0]
            ms, os_ = me.get("score"), opp.get("score")
            res = ""
            try:
                if ms is not None and os_ is not None:
                    mi, oi = int(ms), int(os_)
                    res = "W" if mi > oi else ("D" if mi == oi else "L")
            except (ValueError, TypeError):
                pass
            games.append({
                "opp": (opp.get("team") or {}).get("abbreviation") or "",
                "score": ("%s-%s" % (ms, os_)) if ms is not None else "",
                "result": res,
                "date": (ge.get("date") or "")[:10],
            })
        if games:
            out["last_five"].append({
                "team_id": str(team.get("id") or ""),
                "team_name": team.get("displayName") or "",
                "games": games[:5],
            })

    # --- head-to-head (season series) ---
    ss = data.get("seasonseries")
    if isinstance(ss, list):
        for item in ss[:10]:
            if not isinstance(item, dict):
                continue
            evs = item.get("events") or [item]
            for ge in evs:
                if not isinstance(ge, dict):
                    continue
                gcomps = ge.get("competitions") or []
                if not gcomps:
                    continue
                gc = gcomps[0]
                gcs = gc.get("competitors") or []
                if len(gcs) < 2:
                    continue
                names = [(c.get("team") or {}).get("abbreviation") or "" for c in gcs]
                scores = [c.get("score") for c in gcs]
                line = "%s %s - %s %s" % (names[0], scores[0], scores[1], names[1])
                out["h2h"].append({
                    "date": (ge.get("date") or "")[:10],
                    "line": line,
                    "t0": str((gcs[0].get("team") or {}).get("id") or ""),
                    "s0": gcs[0].get("score"),
                    "t1": str((gcs[1].get("team") or {}).get("id") or ""),
                    "s1": gcs[1].get("score"),
                })

    # --- key players / stat leaders ---
    for lg in data.get("leaders") or []:
        team = lg.get("team") or {}
        rows = []
        for cat in lg.get("leaders") or []:
            leaders = cat.get("leaders") or []
            if not leaders:
                continue
            top = leaders[0]
            ath = top.get("athlete") or {}
            rows.append({
                "category": cat.get("displayName") or cat.get("shortDisplayName") or "",
                "name": ath.get("displayName") or "",
                "value": top.get("displayValue") or "",
            })
        if rows:
            out["leaders"].append({
                "team_id": str(team.get("id") or ""),
                "team_name": team.get("displayName") or "",
                "rows": rows[:6],
            })

    # --- NFL-style play-by-play from drives (real start/end yard lines) ---
    nfl_plays = []
    drives = data.get("drives") or {}

    def _as_list(x):
        return x if isinstance(x, list) else ([x] if isinstance(x, dict) else [])

    for drv in _as_list(drives.get("previous")) + _as_list(drives.get("current")):
        for p in drv.get("plays") or []:
            if not isinstance(p, dict):
                continue
            st, en = p.get("start") or {}, p.get("end") or {}
            sy, ey = st.get("yardLine"), en.get("yardLine")
            if not isinstance(sy, (int, float)) or not isinstance(ey, (int, float)):
                continue
            clk = p.get("clock") or {}
            per = p.get("period") or {}
            nfl_plays.append({
                "id": str(p.get("id") or ""),
                "q": per.get("number") if isinstance(per, dict) else "",
                "clock": clk.get("displayValue") if isinstance(clk, dict) else "",
                "down": st.get("down"), "dist": st.get("distance"),
                "startYd": sy, "endYd": ey,
                "yte": en.get("yardsToEndzone"),
                "text": p.get("text") or "",
                "scoring": bool(p.get("scoringPlay")),
                "hs": p.get("homeScore"), "as": p.get("awayScore"),
            })
    out["nfl_plays"] = nfl_plays[:400]

    # --- generic scoring plays (for the gamecast panel) ---
    cast = []
    for sp in data.get("scoringPlays") or []:
        if not isinstance(sp, dict):
            continue
        t = sp.get("text") or sp.get("description") or ""
        if not t:
            continue
        clk = sp.get("clock") or {}
        per = sp.get("period") or {}
        cast.append({
            "clock": clk.get("displayValue") if isinstance(clk, dict) else "",
            "q": per.get("number") if isinstance(per, dict) else "",
            "text": t, "scoring": True,
            "hs": sp.get("homeScore"), "as": sp.get("awayScore"),
        })
    out["cast_plays"] = cast

    gi = data.get("gameInfo") or {}
    gv = gi.get("venue") or {}
    if gv:
        out["venue_extra"] = {
            "name": gv.get("fullName") or "",
            "city": (gv.get("address") or {}).get("city") or "",
            "country": (gv.get("address") or {}).get("country") or "",
            "capacity": gv.get("capacity") or "",
        }
    return out


def parse_standings(data):
    """Best-effort standings parse; returns list of groups or []."""
    groups = []
    for child in data.get("children") or data.get("standings") or []:
        entries = child.get("standings", {}).get("entries") if isinstance(
            child.get("standings"), dict) else child.get("entries")
        if not entries:
            # flat shape
            entries = child.get("entries")
        rows = []
        for e in entries or []:
            team = e.get("team") or {}
            stats = {}
            for s in e.get("stats") or []:
                stats[s.get("name")] = s.get("displayValue")
            rows.append({
                "name": team.get("displayName") or team.get("shortDisplayName") or "",
                "abbr": team.get("abbreviation") or "",
                "logo": team.get("logo") or "",
                "stats": stats,
            })
        if rows:
            groups.append({"name": child.get("name") or child.get("abbreviation") or "",
                           "rows": rows})
    # fallback: top-level entries
    if not groups and isinstance(data.get("standings"), list):
        for s in data["standings"]:
            rows = []
            for e in s.get("entries") or []:
                team = e.get("team") or {}
                stats = {x.get("name"): x.get("displayValue") for x in e.get("stats") or []}
                rows.append({"name": team.get("displayName") or "",
                             "abbr": team.get("abbreviation") or "",
                             "logo": team.get("logo") or "", "stats": stats})
            if rows:
                groups.append({"name": s.get("name") or "", "rows": rows})
    return groups

# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------

CSS = """
:root{--bg:#0d1117;--card:#161b22;--line:#262d36;--txt:#e6edf3;--mut:#8b949e;
--acc:#2f81f7;--grn:#3fb950;--red:#f85149;--amber:#d29922}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.5;font-size:16px}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1000px;margin:0 auto;padding:0 14px}
header.top{background:#010409;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10}
.top-in{display:flex;align-items:center;gap:18px;padding:12px 14px;max-width:1000px;margin:0 auto}
.brand{font-size:22px;font-weight:800;color:#fff;letter-spacing:.5px}
.brand b{color:var(--amber)}
nav.main{display:flex;gap:4px;overflow-x:auto;padding:0 14px 10px;max-width:1000px;margin:0 auto}
nav.main a{color:var(--mut);font-size:13px;padding:6px 10px;border-radius:6px;white-space:nowrap}
nav.main a:hover{background:var(--card);color:var(--txt);text-decoration:none}
h1{font-size:24px;margin:22px 0 12px}h2{font-size:19px;margin:24px 0 10px;
border-bottom:1px solid var(--line);padding-bottom:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
.mrow{display:flex;align-items:center;gap:10px;padding:10px 4px;border-bottom:1px solid var(--line)}
.mrow:last-child{border-bottom:0}
.mrow .st{font-size:12px;color:var(--mut);width:86px;flex-shrink:0}
.mrow .st.live{color:var(--red);font-weight:700}
.teams{flex:1;min-width:0}.tn{font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tn img{width:20px;height:20px;vertical-align:-4px;margin-right:6px}
.sc{font-weight:700;font-size:15px;text-align:right;white-space:nowrap}
.sc .w{color:var(--grn)}
table{width:100%;border-collapse:collapse;font-size:14px;margin:8px 0}
th,td{padding:8px 6px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase}
td.num,th.num{text-align:right}.pos{color:var(--mut);width:34px}
.bar-row{display:flex;align-items:center;gap:8px;margin:7px 0;font-size:13px}
.bar-row .v{width:44px;text-align:center;font-weight:700}
.bar{flex:1;height:8px;background:#21262d;border-radius:4px;position:relative;overflow:hidden}
.bar i{position:absolute;top:0;bottom:0;background:var(--acc);border-radius:4px}
.bar-row .lab{width:100%;text-align:center;color:var(--mut);font-size:12px;margin-top:2px}
.stat-block{margin:4px 0 10px}
.cmt{border-left:3px solid var(--line);padding:6px 10px;margin:8px 0;font-size:14px}
.cmt.scoring{border-color:var(--grn)}
.cmt .ck{color:var(--mut);font-size:12px;font-weight:700;margin-right:6px}
.cmt .tp{color:var(--amber);font-size:12px;margin-right:6px}
.lup{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:640px){.lup{grid-template-columns:1fr}}
.lup h3{font-size:15px;margin-bottom:6px}
.pl{display:flex;justify-content:space-between;font-size:13px;padding:4px 0;border-bottom:1px dashed var(--line)}
.pl .js{color:var(--mut);width:30px}.pl.starter .nm{font-weight:600}
.formchip{display:inline-block;min-width:24px;text-align:center;font-size:12px;font-weight:700;
border-radius:4px;padding:2px 4px;margin-right:4px;color:#fff}
.fW{background:var(--grn)}.fD{background:var(--mut)}.fL{background:var(--red)}
.badge{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px;
background:#1f2a37;color:var(--acc);margin-bottom:8px}
.badge.live{background:#3d1113;color:var(--red)}
.meta{color:var(--mut);font-size:13px;margin:6px 0}
footer{border-top:1px solid var(--line);margin-top:36px;padding:20px 14px;color:var(--mut);font-size:13px}
footer .wrap{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.kv{display:flex;gap:8px;font-size:14px;padding:5px 0;border-bottom:1px solid var(--line)}
.kv b{min-width:130px;color:var(--mut);font-weight:600}
.scoreboard-hero{text-align:center;padding:18px 10px}
.scoreboard-hero .big{font-size:40px;font-weight:800;letter-spacing:1px}
.scoreboard-hero .nm{font-size:17px;font-weight:600}
.scoreboard-hero img{width:52px;height:52px}
.vs{display:inline-block;margin:0 14px;color:var(--mut);font-size:15px;vertical-align:middle}
.upd{color:var(--mut);font-size:12px;margin-top:14px}
.trend{margin:14px 0 6px;overflow-x:auto;padding-bottom:8px}
.trend-in{display:flex;gap:10px;min-width:max-content;padding:2px}
.tcard{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:10px 12px;min-width:160px;color:var(--txt);flex-shrink:0}
.tcard:hover{text-decoration:none;border-color:var(--acc)}
.tstat{font-size:11px;font-weight:700;color:var(--mut);display:flex;
align-items:center;gap:6px;margin-bottom:6px}
.tstat.live{color:var(--red)}
.pulse{width:8px;height:8px;border-radius:50%;background:var(--red);
animation:pl 1.6s infinite}
@keyframes pl{0%{box-shadow:0 0 0 0 rgba(248,81,73,.6)}
70%{box-shadow:0 0 0 8px rgba(248,81,73,0)}100%{box-shadow:0 0 0 0 rgba(248,81,73,0)}}
.tline{display:flex;align-items:center;gap:6px;font-size:13px;padding:2px 0}
.tline img{width:18px;height:18px}
.tline .tn2{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tline b{font-size:14px}
.tleague{font-size:10px;color:var(--mut);margin-top:6px;display:block;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fav-star{background:none;border:0;color:var(--mut);font-size:19px;cursor:pointer;
padding:2px 8px;line-height:1;flex-shrink:0}
.fav-star:hover{color:var(--amber)}
.fav-star.on{color:var(--amber)}
h1 .fav-star{font-size:22px;vertical-align:middle}
.sbox{margin-left:auto}
.sbox input{background:var(--card);border:1px solid var(--line);color:var(--txt);
border-radius:8px;padding:7px 12px;font-size:14px;width:190px}
.sbox input::placeholder{color:var(--mut)}
.sres{margin:6px 0}
.sres a{display:block;padding:9px 4px;border-bottom:1px solid var(--line);font-size:15px}
.sres .st2{color:var(--mut);font-size:12px;margin-left:8px}
.sbz-field{position:relative;height:112px;background:#0a5c2f;border-radius:8px;
overflow:hidden;margin:8px 0;border:1px solid #14532d}
.sbz-ez{position:absolute;top:0;bottom:0;width:6%;background:#083d20;display:flex;
align-items:center;justify-content:center;font-weight:800;font-size:13px;color:#fff;z-index:1}
.sbz-yl{position:absolute;top:0;bottom:0;width:1px;background:rgba(255,255,255,.35)}
.sbz-yn{position:absolute;top:5px;transform:translateX(-50%);color:rgba(255,255,255,.8);
font-size:11px;font-weight:700}
.sbz-los{position:absolute;top:0;bottom:0;width:3px;background:#2f81f7;
transform:translateX(-50%);z-index:2}
.sbz-fd{position:absolute;top:0;bottom:0;width:3px;background:#d29922;
transform:translateX(-50%);z-index:2}
.sbz-ball{position:absolute;top:50%;width:15px;height:11px;background:#7a4a21;
border:1px solid #fff;border-radius:6px;transform:translate(-50%,-50%);
transition:left 1.4s ease-in-out;z-index:3}
.sbz-ctrl{display:flex;gap:8px;align-items:center;margin:10px 0;flex-wrap:wrap}
.sbz-ctrl button{background:var(--card);border:1px solid var(--line);color:var(--txt);
border-radius:8px;padding:8px 14px;font-size:14px;cursor:pointer}
.sbz-ctrl button:hover{border-color:var(--acc)}
.sbz-ctrl .cnt{color:var(--mut);font-size:13px}
.sbz-down{font-size:19px;font-weight:800}
.sbz-sub{color:var(--mut);font-size:13px}
.sbz-ptext{font-size:14px;margin-top:8px}
.sbz-chead{display:flex;justify-content:space-between;align-items:center;
font-size:16px;font-weight:700;margin-bottom:6px}
.sbz-chead .ck{color:var(--mut);font-weight:600;font-size:13px}
.cmark{display:inline-block;font-size:10px;font-weight:700;border:1px solid var(--line);
border-radius:4px;padding:0 5px;margin-left:5px;color:var(--amber);background:var(--bg)}
.inn-head{font-size:16px;font-weight:700;margin:14px 0 6px}
"""

SPORTS_NAV = ["Football", "American Football", "Basketball", "Ice Hockey",
              "Baseball", "Tennis", "MMA"]


def jsonld_breadcrumbs(items):
    els = []
    for i, (name, url) in enumerate(items, 1):
        els.append({"@type": "ListItem", "position": i,
                    "name": name, "item": BASE_URL + url})
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": els}


def ga_snippet():
    if not GA_MEASUREMENT_ID:
        return ""
    return ("""
<script async src="https://www.googletagmanager.com/gtag/js?id=%s"></script>
<script>
window.dataLayer=window.dataLayer||[];
function gtag(){dataLayer.push(arguments);}
gtag('js',new Date());
gtag('config','%s');
</script>""" % (GA_MEASUREMENT_ID, GA_MEASUREMENT_ID))


def page(title, desc, path, crumbs, body_html, extra_jsonld=None):
    canon = BASE_URL + path
    scripts = [jsonld_breadcrumbs(crumbs)]
    if extra_jsonld:
        scripts.append(extra_jsonld)
    ld = "\n".join(
        '<script type="application/ld+json">%s</script>' % json.dumps(s)
        for s in scripts)
    nav = "".join('<a href="/leagues/">Leagues</a>' for _ in [0])
    nav += '<a href="/cricket/">Cricket</a>'
    sport_links = "".join(
        '<a href="/%s/">%s</a>' % (slugify(s), esc(s))
        for s in SPORTS_NAV)
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title>
<meta name="description" content="%s">
<link rel="canonical" href="%s">
<meta property="og:type" content="website">
<meta property="og:site_name" content="ScoreBuzz">
<meta property="og:title" content="%s">
<meta property="og:description" content="%s">
<meta property="og:url" content="%s">
%s
<style>%s</style>
%s
</head>
<body>
<header class="top"><div class="top-in">
<a class="brand" href="/"><b>Score</b>Buzz</a>
<form class="sbox" action="/search/" method="get" role="search">
<input name="q" placeholder="Search teams &amp; leagues" aria-label="Search teams and leagues" autocomplete="off">
</form>
</div><nav class="main">%s%s<a href="/favourites/">&#9733; Favourites</a></nav></header>
<main class="wrap">
%s
</main>
<footer><div class="wrap">
<span><b>Score</b>Buzz &mdash; live scores, fixtures, results, lineups and stats.</span>
<span>Data: ESPN. Updated %s UTC.</span>
</div></footer>
<script>
(function(){
function getF(){try{return JSON.parse(localStorage.getItem('sbz_favs')||'{}')}catch(e){return{}}}
function setF(f){try{localStorage.setItem('sbz_favs',JSON.stringify(f))}catch(e){}}
function paint(){var f=getF();document.querySelectorAll('.fav-star').forEach(function(b){
var on=!!f[b.getAttribute('data-fkey')];b.classList.toggle('on',on);
b.innerHTML=on?'&#9733;':'&#9734;';b.setAttribute('aria-pressed',on?'true':'false');});}
document.addEventListener('click',function(e){
var b=e.target.closest?e.target.closest('.fav-star'):null;if(!b)return;
e.preventDefault();e.stopPropagation();
var k=b.getAttribute('data-fkey'),f=getF();
if(f[k]){delete f[k]}else{f[k]={n:b.getAttribute('data-fname')||'',d:b.getAttribute('data-fdate')||''}}
setF(f);paint();
if(typeof sbzFavRender==='function')sbzFavRender();
},true);
document.addEventListener('DOMContentLoaded',paint);paint();
})();
</script>
</body>
</html>""" % (esc(title), esc(desc), canon, esc(title), esc(desc), canon,
               ga_snippet(), CSS, ld, nav, sport_links, body_html,
               datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))


CHANGED_URLS = []  # pages whose HTML actually changed this build (for IndexNow)

def write_file(rel_path, content):
    full = os.path.join(OUT_DIR, rel_path.lstrip("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    try:
        with open(full, "r", encoding="utf-8") as f:
            old = f.read()
    except OSError:
        old = None
    if old != content:
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        CHANGED_URLS.append(BASE_URL + "/" + rel_path.lstrip("/"))
    return "/" + rel_path.lstrip("/")


def team_img(t, size=20):
    if t.get("logo"):
        return ('<img src="%s" alt="%s logo" width="%d" height="%d" loading="lazy">'
                % (esc(t["logo"]), esc(t["short"] or t["name"]), size, size))
    return ""


def match_row(m):
    live = m["state"] == "in"
    st = '<span class="st%s">%s</span>' % (" live" if live else "",
                                           esc(status_label(m)))
    hs, aws = m["home"].get("score"), m["away"].get("score")
    def nm(t, s):
        cls = "w" if t.get("winner") and s is not None else ""
        return ('<div class="tn">%s%s</div>'
                % (team_img(t), esc(t["short"] or t["name"])))
    if hs is None:
        sc = '<div class="sc">%s</div>' % esc(fmt_time(m["date"]))
    else:
        sc = ('<div class="sc"><span class="%s">%s</span><br>'
              '<span class="%s">%s</span></div>'
              % ("w" if m["home"].get("winner") else "", esc(hs),
                 "w" if m["away"].get("winner") else "", esc(aws)))
    fkey = "match:%s" % match_path(m)
    fname = "%s vs %s" % (m["home"]["name"], m["away"]["name"])
    fdate = m["date"].strftime("%Y-%m-%d") if m["date"] else ""
    star = ('<button class="fav-star" data-fkey="%s" data-fname="%s" data-fdate="%s" '
            'aria-label="Save to favourites">&#9734;</button>'
            % (esc(fkey), esc(fname), esc(fdate)))
    return ('<div class="mrow">%s<a href="%s" style="flex:1;display:flex;'
            'align-items:center;gap:10px;min-width:0;color:inherit">'
            '%s<div class="teams">%s%s</div>%s</a>%s</div>'
            % (st, match_path(m), "", nm(m["home"], hs), nm(m["away"], aws),
               sc, star))


def is_derby(m):
    hn, an = m["home"]["name"].lower(), m["away"]["name"].lower()
    for a, b in DERBY_PAIRS:
        if (a in hn and b in an) or (a in an and b in hn):
            return True
    return False


def trend_card(m, live):
    def line(t, s):
        return ('<span class="tline">%s<span class="tn2">%s</span><b>%s</b></span>'
                % (team_img(t, 18), esc(t["abbr"] or t["short"] or t["name"]),
                   esc(s) if s is not None else ""))
    if live:
        stat = ('<span class="tstat live"><span class="pulse"></span>%s</span>'
                % esc(m["clock"] or m["status_text"] or "LIVE"))
    else:
        when = fmt_time(m["date"]) if m["date"] else "Scheduled"
        stat = '<span class="tstat">%s</span>' % esc(when)
    return ('<a class="tcard" href="%s">%s%s%s<span class="tleague">%s%s</span></a>'
            % (match_path(m), stat,
               line(m["home"], m["home"].get("score")),
               line(m["away"], m["away"].get("score")),
               esc(m["league"]["name"]),
               " · Derby" if (not live and is_derby(m)) else ""))


def trending_bar(live_matches, upcoming):
    """SofaScore-style strip: live first, then today's biggest fixtures."""
    cards = []
    for m in sorted(live_matches,
                    key=lambda x: -LEAGUE_WEIGHT.get(x["league"]["slug"], 0))[:6]:
        cards.append(trend_card(m, True))
    scored = []
    for m in upcoming:
        if m["state"] != "pre":
            continue
        w = LEAGUE_WEIGHT.get(m["league"]["slug"], 0)
        if is_derby(m):
            w += DERBY_BONUS
        scored.append((w, m))
    scored.sort(key=lambda x: -x[0])
    for _, m in scored[:max(0, 12 - len(cards))]:
        cards.append(trend_card(m, False))
    if not cards:
        return ""
    return ('<div class="trend" aria-label="Trending matches">'
            '<div class="trend-in">%s</div></div>' % "".join(cards))


def build_leagues_index(leagues):
    by_sport = {}
    for lg in leagues:
        by_sport.setdefault(lg["sport_label"], []).append(lg)
    body = ["<h1>All Leagues</h1>"]
    for sport in SPORTS_NAV:
        lgs = by_sport.get(sport)
        if not lgs:
            continue
        body.append('<h2 id="%s">%s</h2><div class="card">'
                    % (slugify(sport), esc(sport)))
        for lg in lgs:
            body.append('<p><a href="/football/%s/">%s</a> '
                        '<span class="meta">— fixtures, results, standings</span></p>'
                        % (lg["slug"], esc(lg["name"])))
        body.append("</div>")
    return page("All Leagues — Fixtures, Results & Standings | ScoreBuzz",
                "Browse every league on ScoreBuzz: football, NFL, NBA, NHL, MLB, "
                "tennis and MMA fixtures, results and standings.",
                "/leagues/", [("Home", "/"), ("Leagues", "/leagues/")],
                "\n".join(body))


def _group_by_day(matches):
    """Group matches by date -> sorted list of (date, {league_slug: [matches]})."""
    by_day = {}
    for m in matches:
        d = m["date"].date() if m["date"] else None
        by_day.setdefault(d, {}).setdefault(m["league"]["slug"], []).append(m)
    return sorted(by_day.items(), key=lambda kv: (kv[0] is None, kv[0]))


def _day_label(d, today):
    if d is None:
        return "Scheduled"
    if d == today:
        return "Today"
    if d == today + timedelta(days=1):
        return "Tomorrow"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return fmt_date(datetime(d.year, d.month, d.day, tzinfo=timezone.utc))


def build_sport_page(sport_label, sport_slug, sport_leagues, events):
    """Clean category page: /<sport-slug>/ with that sport's leagues + fixtures."""
    today = today_utc()
    live = [m for m in events if m["state"] == "in"]
    upcoming = sorted(
        [m for m in events if m["state"] == "pre" and m["date"]
         and today <= m["date"].date() <= today + timedelta(days=7)],
        key=lambda m: m["date"])
    recent = sorted(
        [m for m in events if m["state"] == "post" and m["date"]
         and today - timedelta(days=3) <= m["date"].date() <= today],
        key=lambda m: m["date"], reverse=True)
    path = "/%s/" % sport_slug
    crumbs = [("Home", "/"), (sport_label, path)]
    body = ["<h1>%s Scores &amp; Fixtures</h1>" % esc(sport_label)]
    if live:
        body.append('<div class="card"><span class="badge live">\u25cf LIVE NOW (%d)</span>'
                    % len(live))
        body += [match_row(m) for m in live]
        body.append("</div>")
    if sport_leagues:
        body.append("<h2>Leagues</h2><div class='card'>")
        for lg in sport_leagues:
            body.append('<p><a href="/football/%s/">%s</a> '
                        '<span class="meta">\u2014 fixtures, results, standings</span></p>'
                        % (lg["slug"], esc(lg["name"])))
        body.append("</div>")
    if upcoming:
        body.append("<h2>Upcoming Fixtures</h2>")
        for d, by_league in _group_by_day(upcoming):
            body.append('<h3 style="font-size:14px;margin:10px 0 4px">%s</h3>'
                        % esc(_day_label(d, today)))
            for slug in sorted(by_league):
                ms = sorted(by_league[slug],
                            key=lambda m: m["date"] or datetime.max.replace(tzinfo=timezone.utc))
                lg = next((l for l in sport_leagues if l["slug"] == slug), None)
                body.append('<div class="card"><h3 style="font-size:15px;margin-bottom:4px">'
                            '<a href="/football/%s/">%s</a></h3>'
                            % (slug, esc(lg["name"] if lg else slug)))
                body += [match_row(m) for m in ms]
                body.append("</div>")
    if recent:
        body.append("<h2>Recent Results</h2>")
        for d, by_league in _group_by_day(recent):
            body.append('<h3 style="font-size:14px;margin:10px 0 4px">%s</h3>'
                        % esc(_day_label(d, today)))
            for slug in sorted(by_league):
                ms = sorted(by_league[slug],
                            key=lambda m: m["date"] or datetime.min.replace(tzinfo=timezone.utc),
                            reverse=True)
                lg = next((l for l in sport_leagues if l["slug"] == slug), None)
                body.append('<div class="card"><h3 style="font-size:15px;margin-bottom:4px">'
                            '<a href="/football/%s/">%s</a></h3>'
                            % (slug, esc(lg["name"] if lg else slug)))
                body += [match_row(m) for m in ms]
                body.append("</div>")
    return page("%s Scores, Fixtures & Results | ScoreBuzz" % sport_label,
                "Live %s scores, upcoming fixtures, results, lineups and stats on ScoreBuzz."
                % sport_label.lower(),
                path, crumbs, "\n".join(body))


_MOTD_LEAGUE_RANK = [
    "uefa.champions", "eng.1", "esp.1", "ita.1", "ger.1", "fra.1",
    "uefa.europa", "conmebol.libertadores", "usa.1", "mex.1", "bra.1",
    "arg.1", "ned.1", "por.1", "nfl", "nba", "nhl", "mlb",
]


def _league_rank(slug):
    try:
        return _MOTD_LEAGUE_RANK.index(slug)
    except ValueError:
        return 999


def _match_of_day(live, upcoming):
    """The single featured match: top live game, else the biggest fixture today."""
    cands = [m for m in (live or []) if m.get("date")]
    if not cands:
        today = today_utc()
        cands = [m for m in (upcoming or [])
                 if m.get("date") and m["date"].date() == today]
    if not cands:
        cands = list(upcoming or [])[:1]
    if not cands:
        return None
    return sorted(cands, key=lambda m: (_league_rank(m["league"]["slug"]),
                                       m["date"] or datetime.max.replace(tzinfo=timezone.utc)))[0]


def _motd_html(m, lg_name):
    """Big featured card: Match of the Day."""
    lg = m["league"]
    live = m["state"] == "in"
    hs, aws = m["home"].get("score"), m["away"].get("score")
    if hs is None:
        mid = '<div class="motd-vs">vs</div><div class="motd-ko">%s</div>' % esc(fmt_time(m["date"]))
    else:
        mid = ('<div class="motd-score">%s<span>-</span>%s</div>'
               % (esc(hs), esc(aws)))
    badge = ('<span class="badge live">\u25cf LIVE</span>' if live
             else '<span class="badge">%s</span>' % esc(_day_label(m["date"].date(), today_utc())
                                                       if m.get("date") else ""))
    return ("""<div class='card motd'><div class='motd-tag'>\u2605 Match of the Day %s</div>
<div class='motd-teams'>
<a href='%s' class='motd-team'>%s<span>%s</span></a>
<div class='motd-mid'>%s</div>
<a href='%s' class='motd-team'>%s<span>%s</span></a>
</div>
<div class='motd-lg'><a href='/football/%s/'>%s</a></div></div>"""
            % (badge, match_path(m), team_img(m["home"], 64), esc(m["home"]["name"]),
               mid, match_path(m), team_img(m["away"], 64), esc(m["away"]["name"]),
               lg["slug"], esc(lg_name.get(lg["slug"], lg["name"]))))


_MOTD_CSS = """
.motd{padding:18px;text-align:center}
.motd-tag{font-size:12px;font-weight:700;color:#b8860b;letter-spacing:.4px;margin-bottom:10px}
.motd-teams{display:flex;align-items:center;justify-content:center;gap:18px}
.motd-team{display:flex;flex-direction:column;align-items:center;gap:8px;color:inherit;
text-decoration:none;font-weight:700;font-size:15px;max-width:38%}
.motd-mid{display:flex;flex-direction:column;align-items:center;gap:4px}
.motd-vs{font-size:13px;color:var(--mut)}
.motd-ko{font-size:14px;font-weight:700}
.motd-score{font-size:30px;font-weight:800}.motd-score span{margin:0 8px;color:var(--mut)}
.motd-lg{margin-top:10px;font-size:12px;color:var(--mut)}
.sport-h{display:flex;align-items:center;gap:8px;margin:14px 0 6px;font-size:15px;font-weight:700}
.sport-h .cnt{font-size:12px;font-weight:600;color:var(--mut);background:var(--bg2);
padding:2px 9px;border-radius:20px}
"""


def _sport_of(m):
    return (m["league"].get("sport") or "football").lower()


_SPORT_LABEL = {"football": "Football", "nfl": "American Football", "nba": "Basketball",
                "nhl": "Ice Hockey", "mlb": "Baseball", "tennis": "Tennis", "mma": "MMA",
                "cricket": "Cricket"}


def build_home(live, upcoming, recent, leagues, trend_html=""):
    lg_name = {l["slug"]: l["name"] for l in leagues}
    today = today_utc()
    body = [trend_html, "<style>%s</style>" % _MOTD_CSS, "<h1>Live Scores &amp; Fixtures</h1>"]

    motd = _match_of_day(live, upcoming)
    if motd:
        body.append(_motd_html(motd, lg_name))

    if live:
        body.append('<div class="card"><span class="badge live">\u25cf LIVE NOW (%d)</span>'
                    % len(live))
        body += [match_row(m) for m in live]
        body.append("</div>")
    else:
        body.append('<div class="card"><span class="badge">No live matches right now</span>'
                    '<p class="meta">Live scores appear here the moment games kick off. '
                    "Browse upcoming fixtures below.</p></div>")

    def by_sport(matches):
        out = {}
        for m in matches:
            out.setdefault(_sport_of(m), []).append(m)
        return out

    if upcoming:
        body.append("<h2>Upcoming Fixtures</h2>")
        for sport in sorted(by_sport(upcoming),
                            key=lambda s: (0 if s == "football" else 1, s)):
            ms = by_sport(upcoming)[sport]
            body.append('<div class="sport-h">%s <span class="cnt">%d %s</span></div>'
                        % (esc(_SPORT_LABEL.get(sport, sport.title())), len(ms),
                           "match" if len(ms) == 1 else "matches"))
            for d, by_league in _group_by_day(ms):
                body.append('<h3 style="font-size:13px;margin:8px 0 4px;color:var(--mut)">%s</h3>'
                            % esc(_day_label(d, today)))
                for slug in sorted(by_league, key=lambda s: lg_name.get(s, s)):
                    lms = sorted(by_league[slug],
                                 key=lambda m: m["date"] or datetime.max.replace(tzinfo=timezone.utc))
                    body.append('<div class="card"><h3 style="font-size:14px;margin-bottom:4px">'
                                '<a href="/football/%s/">%s</a></h3>' % (slug, esc(lg_name.get(slug, slug))))
                    body += [match_row(m) for m in lms]
                    body.append("</div>")
    if recent:
        body.append("<h2>Recent Results</h2>")
        for sport in sorted(by_sport(recent),
                            key=lambda s: (0 if s == "football" else 1, s)):
            ms = by_sport(recent)[sport]
            body.append('<div class="sport-h">%s <span class="cnt">%d %s</span></div>'
                        % (esc(_SPORT_LABEL.get(sport, sport.title())), len(ms),
                           "match" if len(ms) == 1 else "matches"))
            for d, by_league in _group_by_day(ms):
                body.append('<h3 style="font-size:13px;margin:8px 0 4px;color:var(--mut)">%s</h3>'
                            % esc(_day_label(d, today)))
                for slug in sorted(by_league, key=lambda s: lg_name.get(s, s)):
                    lms = sorted(by_league[slug],
                                 key=lambda m: m["date"] or datetime.min.replace(tzinfo=timezone.utc),
                                 reverse=True)
                    body.append('<div class="card"><h3 style="font-size:14px;margin-bottom:4px">'
                                '<a href="/football/%s/">%s</a></h3>' % (slug, esc(lg_name.get(slug, slug))))
                    body += [match_row(m) for m in lms]
                    body.append("</div>")
    body.append("<h2>Leagues</h2><div class='card'>")
    for lg in leagues:
        body.append('<p><a href="/football/%s/">%s</a> '
                    '<span class="meta">\u2014 fixtures, results, standings</span></p>'
                    % (lg["slug"], esc(lg["name"])))
    body.append("</div>")
    return page("Live Football Scores, Fixtures & Results | ScoreBuzz",
                "ScoreBuzz: live scores, upcoming fixtures, results, lineups, "
                "stats and standings across football, NFL, NBA, NHL, MLB, tennis and MMA.",
                "/", [("Home", "/")], "\n".join(body))


def build_league(league, events, standings, trend_html=""):
    upcoming = [m for m in events if m["state"] == "pre"]
    live = [m for m in events if m["state"] == "in"]
    finished = [m for m in events if m["state"] == "post"]
    body = [trend_html,
            "<h1>%s Fixtures, Results &amp; Standings "
            '<button class="fav-star" data-fkey="league:/football/%s/" '
            'data-fname="%s" aria-label="Save to favourites">&#9734;</button></h1>'
            % (esc(league["name"]), league["slug"], esc(league["name"]))]
    if live:
        body.append('<div class="card"><span class="badge live">● LIVE</span>')
        body += [match_row(m) for m in live]
        body.append("</div>")
    if upcoming:
        body.append("<h2>Upcoming Fixtures</h2><div class='card'>")
        body += [match_row(m) for m in upcoming[:40]]
        body.append("</div>")
    if finished:
        body.append("<h2>Results</h2><div class='card'>")
        body += [match_row(m) for m in finished[:40]]
        body.append("</div>")
    if standings:
        for grp in standings:
            body.append("<h2>Standings%s</h2>" %
                        (" — " + esc(grp["name"]) if grp["name"] else ""))
            cols = []
            for r in grp["rows"]:
                for k in r["stats"]:
                    if k not in cols:
                        cols.append(k)
            cols = cols[:6]
            body.append("<div class='card' style='overflow-x:auto'>"
                        "<table><tr><th class='pos'>#</th><th>Team</th>" +
                        "".join("<th class='num'>%s</th>" % esc(pretty_stat(c))
                                for c in cols) + "</tr>")
            for i, r in enumerate(grp["rows"], 1):
                tds = "".join("<td class='num'>%s</td>" % esc(r["stats"].get(c, "-"))
                              for c in cols)
                body.append("<tr><td class='pos'>%d</td><td>%s %s</td>%s</tr>"
                            % (i, team_img({"logo": r["logo"],
                                            "short": r["abbr"], "name": r["name"]}),
                               esc(r["name"]), tds))
            body.append("</table></div>")
    path = "/football/%s/" % league["slug"]
    crumbs = [("Home", "/"), (league["name"], path)]
    return page("%s Fixtures, Results, Live Scores & Standings | ScoreBuzz"
                % league["name"],
                "All %s fixtures, live scores, results and standings on ScoreBuzz."
                % league["name"], path, crumbs, "\n".join(body))


def pretty_stat(name):
    return {"wins": "W", "losses": "L", "ties": "D", "points": "Pts",
            "pointsFor": "PF", "pointsAgainst": "PA",
            "gamesPlayed": "P"}.get(name, name[:4].upper())

# ----------------------------------------------------------------------------
# Match + team pages
# ----------------------------------------------------------------------------

def sports_event_jsonld(m, path):
    hs, aws = m["home"].get("score"), m["away"].get("score")
    ev = {
        "@context": "https://schema.org",
        "@type": "SportsEvent",
        "name": "%s vs %s" % (m["home"]["name"], m["away"]["name"]),
        "sport": m["league"]["sport_label"],
        "startDate": m["date"].isoformat() if m["date"] else "",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "homeTeam": {"@type": "SportsTeam", "name": m["home"]["name"]},
        "awayTeam": {"@type": "SportsTeam", "name": m["away"]["name"]},
        "url": BASE_URL + path,
    }
    if m["state"] == "pre":
        ev["eventStatus"] = "https://schema.org/EventScheduled"
    if m["venue"]:
        loc = {"@type": "SportsActivityLocation", "name": m["venue"]}
        if m["city"]:
            loc["address"] = {"@type": "PostalAddress",
                              "addressLocality": m["city"],
                              "addressCountry": m.get("country") or ""}
        ev["location"] = loc
    return ev


def stat_bars(stats):
    if not stats:
        return ""
    out = ["<h2>Team Stats</h2><div class='card'>"]
    for s in stats[:14]:
        try:
            hv, av = float(s["home"] or 0), float(s["away"] or 0)
        except (ValueError, TypeError):
            hv = av = 0
        tot = hv + av
        hpct = (hv / tot * 100) if tot else 50
        out.append(
            "<div class='stat-block'><div class='bar-row'>"
            "<span class='v'>%s</span>"
            "<div class='bar'><i style='left:0;width:%s%%'></i></div>"
            "<span class='v'>%s</span></div>"
            "<div class='lab'>%s</div></div>"
            % (esc(s["home"] or "-"), round(hpct),
               esc(s["away"] or "-"), esc(s["label"])))
    out.append("</div>")
    return "\n".join(out)


def commentary_html(items):
    if not items:
        return ""
    out = ["<h2>Commentary &amp; Play-by-Play</h2><div class='card'>"]
    for c in items[:200]:
        cls = "cmt scoring" if c.get("scoring") else "cmt"
        out.append('<div class="%s">%s%s<span>%s</span></div>'
                   % (cls,
                      '<span class="ck">%s</span>' % esc(c["clock"]) if c.get("clock") else "",
                      '<span class="tp">%s</span>' % esc(c["type"]) if c.get("type") else "",
                      esc(c["text"])))
    out.append("</div>")
    return "\n".join(out)


def _event_minute(c):
    """Minute number from a commentary item (clock seconds preferred)."""
    sec = c.get("clock_sec")
    if isinstance(sec, (int, float)) and sec >= 0:
        return sec / 60.0
    import re as _re
    m = _re.search(r"(\d+)'(?:\+(\d+)')?", c.get("clock") or "")
    if m:
        return float(m.group(1)) + (float(m.group(2)) if m.group(2) else 0)
    return None


def _event_kind(c):
    """Classify a key event for the timeline: goal | ycard | rcard | sub | None."""
    t = (c.get("type") or "").lower()
    txt = (c.get("text") or "").lower()
    if c.get("scoring") or "goal" in t or ("goal" in txt and "disallowed" not in txt):
        return "goal"
    if "red card" in t or "red card" in txt or "sent off" in txt:
        return "rcard"
    if "yellow card" in t or "yellow card" in txt or "booked" in txt:
        return "ycard"
    if "substitut" in t or "substitut" in txt:
        return "sub"
    return None


_TIMELINE_CSS = """
.tl{position:relative;margin:14px 0 8px;padding:34px 8px 34px}
.tl-axis{position:absolute;left:8px;right:8px;top:50%;height:2px;background:#d7dce3;transform:translateY(-50%)}
.tl-m{position:absolute;top:50%;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;
display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#fff;
cursor:default;box-shadow:0 1px 3px rgba(0,0,0,.25)}
.tl-m.goal{background:#1d9e57}.tl-m.ycard{background:#f2b705;border-radius:4px}
.tl-m.rcard{background:#e5484d;border-radius:4px}.tl-m.sub{background:#2f7fe0}
.tl-m.above{top:calc(50% - 30px)}.tl-m.below{top:calc(50% + 4px)}
.tl-min{position:absolute;top:calc(50% - 8px);transform:translateX(-50%);font-size:10px;color:#8a93a3}
.tl-legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:#5b6472;margin-top:4px}
"""


def timeline_html(items, home_id, away_id, home_short, away_short):
    """SofaScore-style visual event timeline. Only real feed events, never invented."""
    evs = []
    for c in items or []:
        kind = _event_kind(c)
        minute = _event_minute(c)
        if not kind or minute is None or minute < 0 or minute > 130:
            continue
        evs.append((minute, kind, c))
    if not evs:
        return ""
    evs.sort(key=lambda e: e[0])
    max_min = max(90.0, max(e[0] for e in evs))
    icons = {"goal": "⚽", "ycard": "▮", "rcard": "▮", "sub": "⇄"}
    labels = {"goal": "Goal", "ycard": "Yellow card", "rcard": "Red card", "sub": "Substitution"}
    marks = []
    for minute, kind, c in evs:
        pct = min(100.0, minute / max_min * 100.0)
        tid = c.get("team_id") or ""
        side = "above" if tid == str(home_id) else ("below" if tid == str(away_id) else "above")
        team = c.get("team_name") or (home_short if side == "above" else away_short)
        tip = "%s\u2019 %s — %s" % (int(round(minute)), labels[kind], c.get("text") or "")
        marks.append(
            '<div class="tl-m %s %s" style="left:calc(8px + (100%% - 16px) * %.2f / 100)" title="%s">%s</div>'
            % (kind, side, pct, esc(tip), icons[kind]))
        marks.append(
            '<div class="tl-min" style="left:calc(8px + (100%% - 16px) * %.2f / 100)">%d\u2032</div>'
            % (pct, int(round(minute))))
    legend = ('<div class="tl-legend"><span>⚽ Goal</span><span>▮ Yellow / red card</span>'
              '<span>⇄ Substitution</span>'
              '<span>%s \u25b2 &nbsp; %s \u25bc</span></div>'
              % (esc(home_short or "Home"), esc(away_short or "Away")))
    return ("<h2>Match Timeline</h2><div class='card'><style>%s</style>"
            "<div class='tl'><div class='tl-axis'></div>%s</div>%s</div>"
            % (_TIMELINE_CSS, "".join(marks), legend))


_POS_GROUP = {
    "GK": "gk",
    "CB": "df", "LB": "df", "RB": "df", "LWB": "df", "RWB": "df",
    "DF": "df", "SW": "df",
    "CM": "mf", "CDM": "mf", "CAM": "mf", "LM": "mf", "RM": "mf",
    "MF": "mf", "DM": "mf", "AM": "mf",
    "ST": "fw", "CF": "fw", "LW": "fw", "RW": "fw", "FW": "fw",
}

_PITCH_CSS = """
.ptch{position:relative;height:340px;background:#2a7d3f;border-radius:8px;overflow:hidden;
background-image:repeating-linear-gradient(0deg,rgba(255,255,255,.045) 0 34px,transparent 34px 68px)}
.ptch:before{content:"";position:absolute;left:6px;right:6px;top:6px;bottom:6px;
border:2px solid rgba(255,255,255,.7);border-radius:2px}
.ptch:after{content:"";position:absolute;left:50%;top:6px;bottom:6px;width:2px;
background:rgba(255,255,255,.7);transform:translateX(-50%)}
.ptch .cc{position:absolute;left:50%;top:50%;width:70px;height:70px;margin:-35px 0 0 -35px;
border:2px solid rgba(255,255,255,.7);border-radius:50%}
.ptch .bx{position:absolute;left:50%;width:150px;height:52px;transform:translateX(-50%);
border:2px solid rgba(255,255,255,.7)}
.ptch .bx.top{top:6px;border-top:0}.ptch .bx.bot{bottom:6px;border-bottom:0}
.pdot{position:absolute;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;
background:#fff;color:#123;font-size:11px;font-weight:800;display:flex;align-items:center;
justify-content:center;box-shadow:0 1px 4px rgba(0,0,0,.4);cursor:default;z-index:2}
.pdot.aw{background:#123;color:#fff}
.pdot .pn{position:absolute;top:36px;left:50%;transform:translateX(-50%);white-space:nowrap;
font-size:9px;font-weight:600;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.8)}
"""


def _pitch_coords(players, formation, flip):
    """Map starters to (x%, y%) on a vertical pitch. y=0 top (away goal)."""
    import re as _re
    lines = [int(x) for x in _re.findall(r"\d+", formation or "")][:5]
    starters = [pl for pl in players if pl.get("starter")]
    gks = [pl for pl in starters if _POS_GROUP.get((pl.get("position") or "").upper()) == "gk"]
    rest = [pl for pl in starters if pl not in gks]
    order = {"df": 0, "mf": 1, "fw": 2}
    rest.sort(key=lambda pl: order.get(_POS_GROUP.get((pl.get("position") or "").upper(), "mf"), 1))
    if not lines or sum(lines) != len(rest):
        # fallback: 4-4-2-ish split
        n = len(rest)
        lines = [n // 3, n // 3, n - 2 * (n // 3)] if n else []
    groups = {"df": [], "mf": [], "fw": []}
    for pl in rest:
        groups[_POS_GROUP.get((pl.get("position") or "").upper(), "mf")].append(pl)
    placed = []
    li = 0
    for grp_key in ("df", "mf", "fw"):
        grp = groups[grp_key]
        while grp:
            size = lines[li] if li < len(lines) else len(grp)
            chunk, grp = grp[:size], grp[size:]
            placed.append((li, chunk))
            li += 1
    nlines = max(li, 1)
    out = []
    for pl in gks[:1]:
        out.append((pl, 50.0, 96.0 if not flip else 4.0))
    for li, chunk in placed:
        y = 82.0 - (li / max(nlines - 1, 1)) * 64.0 if nlines > 1 else 50.0
        if flip:
            y = 100.0 - y
        for i, pl in enumerate(chunk):
            x = (i + 1) / (len(chunk) + 1) * 100.0
            out.append((pl, x, y))
    return out


def formation_html(lineups, home_id, away_id):
    """Pitch graphic with positioned starters. Football only; needs formation+positions."""
    lus = [lu for lu in (lineups or [])
           if lu.get("formation") and any(pl.get("starter") for pl in lu.get("players", []))]
    if not lus:
        return ""
    parts = ["<style>%s</style>" % _PITCH_CSS, "<h2>Formations</h2>"]
    for lu in lus[:2]:
        flip = str(lu.get("team_id")) == str(away_id)
        dots = []
        for pl, x, y in _pitch_coords(lu["players"], lu["formation"], flip):
            tip = "%s%s — %s" % (pl.get("jersey") + " " if pl.get("jersey") else "",
                                 pl.get("name") or "", pl.get("position") or "")
            dots.append('<div class="pdot%s" style="left:%.1f%%;top:%.1f%%" title="%s">%s'
                        '<span class="pn">%s</span></div>'
                        % (" aw" if flip else "", x, y, esc(tip),
                           esc(str(pl.get("jersey") or "?")),
                           esc((pl.get("name") or "").split()[-1][:12])))
        parts.append("<div class='card'><h3 style='font-size:14px;margin-bottom:6px'>%s "
                     "<span class='meta'>%s</span></h3>"
                     "<div class='ptch'><div class='cc'></div>"
                     "<div class='bx top'></div><div class='bx bot'></div>%s</div></div>"
                     % (esc(lu["team_name"]), esc(lu["formation"]), "".join(dots)))
    return "\n".join(parts)


def lineups_html(lineups, teams_by_id):
    if not lineups:
        return ""
    out = ["<h2>Lineups</h2><div class='lup'>"]
    for lu in lineups:
        t = teams_by_id.get(lu["team_id"], {})
        starters = [p for p in lu["players"] if p["starter"]]
        subs = [p for p in lu["players"] if not p["starter"]]
        head = "<h3>%s%s %s</h3>" % (
            team_img({"logo": t.get("logo"), "short": lu["team_name"],
                      "name": lu["team_name"]}, 22),
            esc(lu["team_name"]),
            '<span class="meta">%s</span>' % esc(lu["formation"]) if lu.get("formation") else "")
        out.append("<div class='card'>%s" % head)
        for p in starters:
            out.append('<div class="pl starter"><span class="js">%s</span>'
                       '<span class="nm">%s</span><span class="meta">%s</span></div>'
                       % (esc(p["jersey"]), esc(p["name"]), esc(p["position"])))
        if subs:
            out.append('<h3 style="margin-top:8px">Substitutes</h3>')
            for p in subs:
                out.append('<div class="pl"><span class="js">%s</span>'
                           '<span class="nm">%s</span><span class="meta">%s</span></div>'
                           % (esc(p["jersey"]), esc(p["name"]), esc(p["position"])))
        out.append("</div>")
    out.append("</div>")
    return "\n".join(out)


def form_html(last_five):
    if not last_five:
        return ""
    out = ["<h2>Recent Form</h2><div class='card'>"]
    for lf in last_five:
        chips = "".join(
            '<span class="formchip f%s" title="%s %s">%s</span>'
            % (g["result"] or "D", esc(g["opp"]), esc(g["score"]), g["result"] or "-")
            for g in lf["games"])
        out.append("<p><b>%s</b><br>%s</p>" % (esc(lf["team_name"]), chips))
    out.append("</div>")
    return "\n".join(out)


def h2h_html(h2h, home_id=None, away_id=None,
             home_short="", away_short=""):
    if not h2h:
        return ""
    bars = ""
    if home_id and away_id:
        w = d = l = 0
        for g in h2h:
            try:
                s0, s1 = int(g["s0"]), int(g["s1"])
            except (TypeError, ValueError):
                continue
            if s0 == s1:
                d += 1
            elif (g["t0"] == str(home_id) and s0 > s1) or \
                 (g["t1"] == str(home_id) and s1 > s0):
                w += 1
            else:
                l += 1
        tot = w + d + l
        if tot:
            pw, pd, pl = w / tot * 100, d / tot * 100, l / tot * 100
            bars = ("<div class='h2hb'><span style='width:%.1f%%' class='hw' "
                    "title='%s wins: %d'></span><span style='width:%.1f%%' class='hd' "
                    "title='Draws: %d'></span><span style='width:%.1f%%' class='hl' "
                    "title='%s wins: %d'></span></div>"
                    "<div class='h2hl'><span>%s %d (%.0f%%)</span>"
                    "<span>Draws %d (%.0f%%)</span><span>%s %d (%.0f%%)</span></div>"
                    "<style>.h2hb{display:flex;height:10px;border-radius:5px;overflow:hidden;"
                    "margin:6px 0 4px}.h2hb .hw{background:#1d9e57}.h2hb .hd{background:#9aa4b2}"
                    ".h2hb .hl{background:#e5484d}.h2hl{display:flex;justify-content:space-between;"
                    "font-size:11px;color:var(--mut);margin-bottom:8px}</style>"
                    % (pw, esc(home_short), w, pd, d, pl, esc(away_short), l,
                       esc(home_short), w, pw, d, pd, esc(away_short), l, pl))
    out = ["<h2>Head-to-Head</h2><div class='card'>%s<table>" % bars]
    for g in h2h:
        out.append("<tr><td class='meta'>%s</td><td>%s</td></tr>"
                   % (esc(g["date"]), esc(g["line"])))
    out.append("</table></div>")
    return "\n".join(out)


def leaders_html(leaders):
    if not leaders:
        return ""
    out = ["<h2>Key Players</h2><div class='card'>"]
    for lg in leaders:
        out.append("<h3 style='font-size:15px'>%s</h3>" % esc(lg["team_name"]))
        for r in lg["rows"]:
            out.append('<div class="kv"><b>%s</b><span>%s — %s</span></div>'
                       % (esc(r["category"]), esc(r["name"]), esc(r["value"])))
    out.append("</div>")
    return "\n".join(out)


def nfl_widget_html(m, plays):
    """Animated 2D field driven by REAL play-by-play yard lines."""
    lg = m["league"]
    hab = m["home"]["abbr"] or "HOME"
    aab = m["away"]["abbr"] or "AWAY"
    data_json = json.dumps(plays).replace("</", "<\\/")
    meta_json = json.dumps({"sport": lg["sport"], "league": lg["code"],
                            "event": m["id"], "state": m["state"],
                            "hab": hab, "aab": aab})
    marks = []
    for yd in range(10, 100, 10):
        label = yd if yd <= 50 else 100 - yd
        marks.append('<span class="sbz-yl" style="left:%d%%"></span>'
                     '<span class="sbz-yn" style="left:%d%%">%d</span>'
                     % (yd, yd, label))
    js = """<script>
var SBZ_NFL_PLAYS=%%PLAYS%%,SBZ_META=%%META%%;
(function(){
var plays=SBZ_NFL_PLAYS,meta=SBZ_META,idx=plays.length-1,timer=null;
var ball=document.getElementById('sbzBall'),los=document.getElementById('sbzLos'),
fd=document.getElementById('sbzFd'),dEl=document.getElementById('sbzDown'),
cEl=document.getElementById('sbzClock'),tEl=document.getElementById('sbzText'),
sEl=document.getElementById('sbzScore'),nEl=document.getElementById('sbzCount'),
autoBtn=document.getElementById('sbzAuto');
function ord(d){return d===1?'1st':d===2?'2nd':d===3?'3rd':d===4?'4th':'';}
function downText(p){return (p.down>0&&p.dist!=null)?ord(p.down)+' & '+p.dist:'Kickoff';}
function fdLine(p){if(!(p.dist>0)||p.startYd==null)return null;var dir=1;
if(p.yte!=null&&p.endYd!=null){dir=(Math.abs(p.endYd+p.yte-100)<Math.abs(p.endYd-p.yte))?1:-1;}
else if(p.endYd<p.startYd)dir=-1;return Math.max(2,Math.min(98,p.startYd+dir*p.dist));}
function show(k,animate){if(!plays.length)return;
idx=Math.max(0,Math.min(plays.length-1,k));var p=plays[idx];
los.style.display='block';los.style.left=p.startYd+'%';
var f=fdLine(p);
if(f==null){fd.style.display='none';}else{fd.style.display='block';fd.style.left=f+'%';}
if(animate){ball.style.transition='none';ball.style.left=p.startYd+'%';void ball.offsetWidth;
ball.style.transition='left 1.4s ease-in-out';}
requestAnimationFrame(function(){ball.style.left=p.endYd+'%';});
dEl.textContent=downText(p);
cEl.textContent=((p.q?('Q'+p.q+' '):'')+(p.clock||'')).trim();
tEl.textContent=p.text||'';
sEl.textContent=(p.hs!=null&&p.as!=null)?(meta.hab+' '+p.hs+' - '+p.as+' '+meta.aab):'';
nEl.textContent='Play '+(idx+1)+' of '+plays.length;}
function stop(){if(timer){clearInterval(timer);timer=null;}autoBtn.textContent='\\u25B6 Auto-play';}
function start(){stop();timer=setInterval(function(){if(idx>=plays.length-1){stop();return;}show(idx+1,true);},2600);
autoBtn.textContent='\\u23F8 Pause';}
document.getElementById('sbzPrev').onclick=function(){stop();show(idx-1,true);};
document.getElementById('sbzNext').onclick=function(){stop();show(idx+1,true);};
autoBtn.onclick=function(){timer?stop():start();};
show(idx,false);
if(meta.state==='in'){
window.sbzUpdate=function(d){var seen={},added=0;
plays.forEach(function(p){seen[p.id]=1;});
try{((d.drives||{}).previous||[]).forEach(function(drv){(drv.plays||[]).forEach(function(p){
var st=p.start||{},en=p.end||{};
if(!p.id||seen[p.id]||typeof st.yardLine!=='number'||typeof en.yardLine!=='number')return;
seen[p.id]=1;added++;
plays.push({id:String(p.id),q:(p.period||{}).number||'',clock:(p.clock||{}).displayValue||'',
down:st.down,dist:st.distance,startYd:st.yardLine,endYd:en.yardLine,yte:en.yardsToEndzone,
text:p.text||'',scoring:!!p.scoringPlay,hs:p.homeScore,as:p.awayScore});});});}catch(e){}
if(added)show(plays.length-1,true);};
(function poll(){var s=document.createElement('script');
s.src='https://site.web.api.espn.com/apis/site/v2/sports/'+meta.sport+'/'+meta.league+
'/summary?event='+meta.event+'&callback=sbzUpdate';
s.onerror=function(){var s2=document.createElement('script');
s2.src='https://site.api.espn.com/apis/site/v2/sports/'+meta.sport+'/'+meta.league+
'/summary?event='+meta.event+'&callback=sbzUpdate';document.head.appendChild(s2);};
document.head.appendChild(s);setTimeout(poll,30000);})();}
})();
</script>""".replace("%%PLAYS%%", data_json).replace("%%META%%", meta_json)
    return ("""<h2>Live Game View</h2><div class="card">
<div class="sbz-field" role="img" aria-label="Animated field showing ball position from real play data">
<div class="sbz-ez" style="left:0">%s</div>
<div class="sbz-ez" style="right:0">%s</div>
%s
<div class="sbz-fd" id="sbzFd" style="display:none"></div>
<div class="sbz-los" id="sbzLos" style="display:none"></div>
<div class="sbz-ball" id="sbzBall" style="left:50%%"></div>
</div>
<div class="sbz-ctrl">
<button id="sbzPrev" type="button">&#8249; Prev</button>
<button id="sbzAuto" type="button">&#9654; Auto-play</button>
<button id="sbzNext" type="button">Next &#8250;</button>
<span class="cnt" id="sbzCount"></span>
</div>
<div class="sbz-down" id="sbzDown"></div>
<div class="sbz-sub" id="sbzClock"></div>
<div class="sbz-ptext" id="sbzText"></div>
<div class="sbz-sub" id="sbzScore"></div>
<p class="meta" style="margin-top:8px">Ball position, down &amp; distance from the official play-by-play.</p>
</div>
%s""" % (esc(hab), esc(aab), "".join(marks), js))


def cast_widget_html(m, plays):
    """Stylized live gamecast panel (no coordinates — feed exposes none)."""
    lg = m["league"]
    hab = m["home"]["abbr"] or m["home"]["short"]
    aab = m["away"]["abbr"] or m["away"]["short"]
    data_json = json.dumps(plays).replace("</", "<\\/")
    meta_json = json.dumps({"sport": lg["sport"], "league": lg["code"],
                            "event": m["id"], "state": m["state"],
                            "hab": hab, "aab": aab})
    js = """<script>
var SBZ_CAST=%%CAST%%,SBZ_META=%%META%%;
(function(){
var plays=SBZ_CAST,meta=SBZ_META;
var box=document.getElementById('sbzCPlays'),
sEl=document.getElementById('sbzCScore'),cEl=document.getElementById('sbzCClock');
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function render(){var h='',n=plays.length,from=Math.max(0,n-25),k,p;
for(k=n-1;k>=from;k--){p=plays[k];
h+='<div class="cmt'+(p.scoring?' scoring':'')+'">'+
(p.clock?'<span class="ck">'+esc(p.clock)+'</span>':'')+'<span>'+esc(p.text)+'</span></div>';}
box.innerHTML=h||'<p class="meta">Plays will appear here once the game starts.</p>';
var l=plays[n-1];
if(l){if(l.hs!=null&&l.as!=null)sEl.textContent=meta.hab+' '+l.hs+' - '+l.as+' '+meta.aab;
cEl.textContent=((l.q?('Q'+l.q+' '):'')+(l.clock||'')).trim();}}
render();
if(meta.state==='in'){
window.sbzCastUpdate=function(d){var seen={},added=0;
plays.forEach(function(p){seen[p.clock+'|'+p.text]=1;});
function push(q,clock,text,scoring,hs,as){var k=clock+'|'+text;
if(!text||seen[k])return;seen[k]=1;added++;
plays.push({q:q,clock:clock,text:text,scoring:!!scoring,hs:hs,as:as});}
try{
((d.drives||{}).previous||[]).forEach(function(drv){(drv.plays||[]).forEach(function(p){
push((p.period||{}).number||'',(p.clock||{}).displayValue||'',p.text||'',p.scoringPlay,p.homeScore,p.awayScore);});});
(d.scoringPlays||[]).forEach(function(p){
push((p.period||{}).number||'',(p.clock||{}).displayValue||'',p.text||p.description||'',true,p.homeScore,p.awayScore);});
(d.keyEvents||[]).forEach(function(p){if(p.text)
push((p.period||{}).number||'',((p.clock||{}).displayValue||''),p.text,!!p.scoringPlay,null,null);});
}catch(e){}
if(added)render();};
(function poll(){var s=document.createElement('script');
s.src='https://site.web.api.espn.com/apis/site/v2/sports/'+meta.sport+'/'+meta.league+
'/summary?event='+meta.event+'&callback=sbzCastUpdate';
s.onerror=function(){var s2=document.createElement('script');
s2.src='https://site.api.espn.com/apis/site/v2/sports/'+meta.sport+'/'+meta.league+
'/summary?event='+meta.event+'&callback=sbzCastUpdate';document.head.appendChild(s2);};
document.head.appendChild(s);setTimeout(poll,30000);})();}
})();
</script>""".replace("%%CAST%%", data_json).replace("%%META%%", meta_json)
    return ("""<h2>Live Gamecast</h2><div class="card">
<div class="sbz-chead"><span id="sbzCScore">%s vs %s</span><span class="ck" id="sbzCClock"></span></div>
<div id="sbzCPlays"></div>
</div>
%s""" % (esc(hab), esc(aab), js))


def build_match(m, summ):
    lg, path = m["league"], match_path(m)
    hs, aws = m["home"].get("score"), m["away"].get("score")
    if m["state"] == "post" and hs is not None:
        title = ("%s %s-%s %s: Result, Stats & Commentary | ScoreBuzz"
                 % (m["home"]["name"], hs, aws, m["away"]["name"]))
        desc = ("Full-time result %s %s-%s %s in the %s. Stats, lineups, "
                "commentary and head-to-head on ScoreBuzz."
                % (m["home"]["name"], hs, aws, m["away"]["name"], lg["name"]))
    elif m["state"] == "in":
        title = ("%s vs %s Live Score, Stats & Commentary | ScoreBuzz"
                 % (m["home"]["name"], m["away"]["name"]))
        desc = ("Live score: %s %s - %s %s (%s). Follow every goal and key "
                "moment on ScoreBuzz."
                % (m["home"]["name"], hs, aws, m["away"]["name"],
                   lg["name"]))
    else:
        when = fmt_date(m["date"]) + (" " + fmt_time(m["date"]) if m["date"] else "")
        title = ("%s vs %s Live Score, H2H, Lineups | ScoreBuzz"
                 % (m["home"]["name"], m["away"]["name"]))
        desc = ("%s vs %s, %s %s. Kick-off time, head-to-head, lineups and "
                "live scores on ScoreBuzz."
                % (m["home"]["name"], m["away"]["name"], lg["name"], when))

    badge = ('<span class="badge live">● %s</span>'
             % esc(status_label(m))) if m["state"] == "in" else \
            ('<span class="badge">%s</span>' % esc(status_label(m)))
    hero = (
        "<div class='card scoreboard-hero'>%s"
        "<div><span class='nm'>%s %s</span><br>"
        "<span class='meta'>%s</span></div>"
        "<div class='big'>%s %s %s</div>"
        "<div><span class='nm'>%s %s</span><br>"
        "<span class='meta'>%s</span></div>"
        "<p class='meta'>%s%s%s</p></div>"
        % (badge,
           team_img(m["home"], 52), esc(m["home"]["name"]),
           esc(m["home"].get("record") or ""),
           esc(hs if hs is not None else ""),
           '<span class="vs">-</span>' if hs is not None else '<span class="vs">vs</span>',
           esc(aws if aws is not None else ""),
           team_img(m["away"], 52), esc(m["away"]["name"]),
           esc(m["away"].get("record") or ""),
           esc(m["venue"]), " — " if m["venue"] and m["city"] else "",
           esc(m["city"])))
    parts = [hero]
    widget_done = False
    if summ:
        if m["league"]["sport"] == "football" and summ.get("nfl_plays"):
            parts.append(nfl_widget_html(m, summ["nfl_plays"]))
            widget_done = True
        else:
            cplays = summ.get("cast_plays") or []
            if not cplays and summ.get("commentary"):
                cplays = [{"clock": c.get("clock") or "", "q": c.get("period") or "",
                           "text": c.get("text") or "", "scoring": c.get("scoring"),
                           "hs": None, "as": None}
                          for c in summ["commentary"]]
            if cplays:
                parts.append(cast_widget_html(m, cplays))
                widget_done = True
    if not widget_done and m["state"] == "in":
        parts.append('<div class="card"><p class="meta">The live game view '
                     'will appear here shortly.</p></div>')
    if summ:
        tl = timeline_html(summ.get("commentary"), m["home"]["id"], m["away"]["id"],
                           m["home"].get("short") or m["home"]["name"],
                           m["away"].get("short") or m["away"]["name"])
        if tl:
            parts.append(tl)
        parts.append(form_html(summ["last_five"]))
        parts.append(h2h_html(summ["h2h"], m["home"]["id"], m["away"]["id"],
                                m["home"].get("short") or m["home"]["name"],
                                m["away"].get("short") or m["away"]["name"]))
        parts.append(stat_bars(summ["stats"]))
        teams_by_id = {m["home"]["id"]: m["home"], m["away"]["id"]: m["away"]}
        parts.append(lineups_html(summ["lineups"], teams_by_id))
        fh = formation_html(summ["lineups"], m["home"]["id"], m["away"]["id"])
        if fh:
            parts.append(fh)
        parts.append(leaders_html(summ["leaders"]))
        parts.append(commentary_html(summ["commentary"]))
    elif m["state"] == "pre":
        parts.append("<div class='card'><p class='meta'>Lineups, stats and "
                     "live commentary will appear here once the match begins.</p></div>")

    lg_path = "/football/%s/" % lg["slug"]
    crumbs = [("Home", "/"), (lg["name"], lg_path),
              ("%s vs %s" % (m["home"]["short"], m["away"]["short"]), path)]
    return page(title, desc, path, crumbs, "\n".join(parts),
                sports_event_jsonld(m, path))


def build_team(team, events):
    finished = sorted([m for m in events if m["state"] == "post"
                       and m["date"]], key=lambda m: m["date"], reverse=True)[:15]
    upcoming = sorted([m for m in events if m["state"] in ("pre", "in")
                       and m["date"]], key=lambda m: m["date"])[:15]
    body = ["<h1>%s %s "
            '<button class="fav-star" data-fkey="team:/team/%s/" data-fname="%s" '
            'aria-label="Save to favourites">&#9734;</button></h1>'
            % (team_img(team, 34), esc(team["name"]), esc(team_slug(team)),
               esc(team["name"]))]
    if finished:
        body.append("<h2>Recent Results</h2><div class='card'>")
        body += [match_row(m) for m in finished]
        body.append("</div>")
    if upcoming:
        body.append("<h2>Upcoming Fixtures</h2><div class='card'>")
        body += [match_row(m) for m in upcoming]
        body.append("</div>")
    path = "/team/%s/" % team_slug(team)
    crumbs = [("Home", "/"), (team["name"], path)]
    return page("%s Fixtures, Results & Live Scores | ScoreBuzz" % team["name"],
                "All %s fixtures, results and live scores on ScoreBuzz."
                % team["name"], path, crumbs, "\n".join(body))

# ----------------------------------------------------------------------------
# Favourites + search pages (client-side, zero backend)
# ----------------------------------------------------------------------------

def build_favourites():
    body = ["""<h1>&#9733; My Favourites</h1>
<div class="card"><p class="meta">Tap the &#9734; on any match, team or league to save it here.
Saved on this device only — no account needed.</p></div>
<div id="favOut"><div class="card"><p class="meta">Loading…</p></div></div>
<script>
function sbzFavRender(){
  var f={};try{f=JSON.parse(localStorage.getItem('sbz_favs')||'{}')}catch(e){}
  var groups={match:[],team:[],league:[]},keys=Object.keys(f);
  keys.forEach(function(k){
    var i=k.indexOf(':'),t=k.slice(0,i),u=k.slice(i+1);
    if(t==='cmatch')t='match';
    if(groups[t])groups[t].push({u:u,n:f[k].n||u,d:f[k].d||''});
  });
  var titles={match:'Matches',team:'Teams',league:'Leagues'},h='';
  ['match','team','league'].forEach(function(t){
    var g=groups[t];if(!g.length)return;
    g.sort(function(a,b){return (b.d||'').localeCompare(a.d||'')});
    h+='<h2>'+titles[t]+'</h2><div class="card sres">';
    g.forEach(function(x){
      h+='<a href="'+x.u.replace(/"/g,'')+'">'+escH(x.n)+
         (x.d?'<span class="st2">'+escH(x.d)+'</span>':'')+'</a>';
    });
    h+='</div>';
  });
  if(!h)h='<div class="card"><p class="meta">Nothing saved yet. Tap the &#9734; '
        +'on any match, team or league and it will appear here.</p></div>';
  document.getElementById('favOut').innerHTML=h;
}
function escH(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
document.addEventListener('DOMContentLoaded',sbzFavRender);sbzFavRender();
</script>"""]
    return page("My Favourites | ScoreBuzz",
                "Your saved matches, teams and leagues on ScoreBuzz. No account needed.",
                "/favourites/",
                [("Home", "/"), ("Favourites", "/favourites/")],
                "\n".join(body))


def build_search_page():
    body = ["""<h1>Search</h1>
<div class="card">
<form id="sbzSForm" action="/search/" method="get" role="search" style="display:flex;gap:8px">
<input id="sbzSQ" name="q" placeholder="Team or league…" aria-label="Search"
 style="flex:1;background:var(--bg);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:10px 12px;font-size:16px" autocomplete="off">
<button style="background:var(--acc);border:0;color:#fff;border-radius:8px;padding:10px 18px;font-size:15px;cursor:pointer">Search</button>
</form></div>
<div id="sbzSOut"></div>
<script>
var SBZ_IDX=null;
function escH(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function sbzRun(q){
  var out=document.getElementById('sbzSOut');
  q=(q||'').trim().toLowerCase();
  if(!SBZ_IDX){out.innerHTML='<div class="card"><p class="meta">Loading index…</p></div>';return;}
  if(q.length<2){out.innerHTML='<div class="card"><p class="meta">Type at least 2 characters.</p></div>';return;}
  var teams=SBZ_IDX.teams.filter(function(t){return t.n.toLowerCase().indexOf(q)>=0}).slice(0,20);
  var leagues=SBZ_IDX.leagues.filter(function(t){return t.n.toLowerCase().indexOf(q)>=0}).slice(0,10);
  var h='';
  if(leagues.length){h+='<h2>Leagues</h2><div class="card sres">'+leagues.map(function(x){
    return '<a href="'+x.u+'">'+escH(x.n)+'<span class="st2">'+escH(x.s||'')+'</span></a>';}).join('')+'</div>';}
  if(teams.length){h+='<h2>Teams</h2><div class="card sres">'+teams.map(function(x){
    return '<a href="'+x.u+'">'+escH(x.n)+'</a>';}).join('')+'</div>';}
  if(!h)h='<div class="card"><p class="meta">No results for &ldquo;'+escH(q)+'&rdquo;.</p></div>';
  out.innerHTML=h;
}
fetch('/search.json').then(function(r){return r.json()}).then(function(j){
  SBZ_IDX=j;
  var q=new URLSearchParams(location.search).get('q')||'';
  document.getElementById('sbzSQ').value=q;
  if(q)sbzRun(q);
}).catch(function(){document.getElementById('sbzSOut').innerHTML=
  '<div class="card"><p class="meta">Search index failed to load.</p></div>';});
document.getElementById('sbzSQ').addEventListener('input',function(){sbzRun(this.value);});
document.getElementById('sbzSForm').addEventListener('submit',function(e){
  e.preventDefault();sbzRun(document.getElementById('sbzSQ').value);});
</script>"""]
    return page("Search Teams & Leagues | ScoreBuzz",
                "Search ScoreBuzz for teams and leagues — fixtures, results and live scores.",
                "/search/",
                [("Home", "/"), ("Search", "/search/")],
                "\n".join(body))


# ----------------------------------------------------------------------------
# Cricket pages (CricAPI — budget-capped, degrades gracefully)
# ----------------------------------------------------------------------------

def cricket_url(cm):
    return "/cricket/%s/%s/" % (cm["series_slug"], cm["slug"])


def cricket_match_row(cm):
    url = cricket_url(cm)
    if cm["state"] == "live":
        st = '<span class="st live">● LIVE</span>'
    elif cm["state"] == "upcoming":
        dt = (fmt_date(cm["date"]) + " " + fmt_time(cm["date"])
              if cm["date"] else "Scheduled")
        st = '<span class="st">%s</span>' % esc(dt)
    else:
        st = '<span class="st">%s</span>' % esc(cm["status"] or "Result")

    def logo(tname):
        img = (cm.get("team_imgs") or {}).get((tname or "").lower())
        if img:
            return ('<img src="%s" alt="%s" width="20" height="20" loading="lazy">'
                    % (esc(img), esc(tname)))
        return ""

    teams_html = ('<div class="tn">%s%s</div><div class="tn">%s%s</div>'
                  % (logo(cm["team1"]), esc(cm["team1"] or "?"),
                     logo(cm["team2"]), esc(cm["team2"] or "?")))
    sc = ('<div class="sc" style="font-weight:400;font-size:13px">%s</div>'
          % esc(cm["score_text"])) if cm["score_text"] else \
        '<div class="sc meta">%s</div>' % esc(cm["match_type"] or "")
    star = ('<button class="fav-star" data-fkey="cmatch:%s" data-fname="%s" '
            'data-fdate="%s" aria-label="Save to favourites">&#9734;</button>'
            % (esc(url), esc(cm["name"]),
               esc(cm["date"].strftime("%Y-%m-%d") if cm["date"] else "")))
    return ('<div class="mrow">%s<a href="%s" style="flex:1;display:flex;'
            'align-items:center;gap:10px;min-width:0;color:inherit">'
            '<div class="teams">%s</div>%s</a>%s</div>'
            % (st, url, teams_html, sc, star))


def build_cricket_index(matches, series):
    live = [m for m in matches if m["state"] == "live"]
    upcoming = [m for m in matches if m["state"] == "upcoming"][:30]
    recent = [m for m in matches if m["state"] == "recent"][:30]
    body = ["<h1>Cricket Live Scores &amp; Fixtures</h1>"]
    if live:
        body.append('<div class="card"><span class="badge live">● LIVE NOW</span>')
        body += [cricket_match_row(m) for m in live]
        body.append("</div>")
    if upcoming:
        body.append("<h2>Upcoming Matches</h2><div class='card'>")
        body += [cricket_match_row(m) for m in upcoming]
        body.append("</div>")
    if recent:
        body.append("<h2>Recent Results</h2><div class='card'>")
        body += [cricket_match_row(m) for m in recent]
        body.append("</div>")
    if not (live or upcoming or recent):
        body.append('<div class="card"><p class="meta">No cricket data available '
                    'right now — fixtures will appear here automatically.</p></div>')
    if series:
        body.append("<h2>Series</h2><div class='card'>")
        for s in series:
            body.append('<p><a href="/cricket/%s/">%s</a></p>'
                        % (s["slug"], esc(s["name"])))
        body.append("</div>")
    return page("Cricket Live Scores, Fixtures & Results | ScoreBuzz",
                "Live cricket scores, full scorecards, fixtures and results on ScoreBuzz.",
                "/cricket/", [("Home", "/"), ("Cricket", "/cricket/")],
                "\n".join(body))


def build_cricket_series(s, matches):
    body = ["<h1>%s — Fixtures &amp; Results</h1>" % esc(s["name"])]
    live = [m for m in matches if m["state"] == "live"]
    other = [m for m in matches if m["state"] != "live"]
    if live:
        body.append('<div class="card"><span class="badge live">● LIVE</span>')
        body += [cricket_match_row(m) for m in live]
        body.append("</div>")
    if other:
        body.append("<div class='card'>")
        body += [cricket_match_row(m) for m in other[:40]]
        body.append("</div>")
    path = "/cricket/%s/" % s["slug"]
    return page("%s Fixtures, Results & Live Scores | ScoreBuzz" % s["name"],
                "All %s cricket fixtures, live scores and results on ScoreBuzz." % s["name"],
                path,
                [("Home", "/"), ("Cricket", "/cricket/"), (s["name"], path)],
                "\n".join(body))


def _cricket_batting_table(players):
    if not players:
        return ""
    rows = ["<div style='overflow-x:auto'><table><tr><th>Batter</th>"
            "<th class='num'>R</th><th class='num'>B</th><th class='num'>4s</th>"
            "<th class='num'>6s</th><th class='num'>SR</th></tr>"]
    for p in players:
        marks = "".join('<span class="cmark">%s</span>' % x
                        for x in (["C"] if p.get("captain") else []) +
                                 (["†"] if p.get("keeper") else []))
        rows.append("<tr><td>%s%s<br><span class='meta'>%s</span></td>"
                    "<td class='num'><b>%s</b></td><td class='num'>%s</td>"
                    "<td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td></tr>"
                    % (esc(p["name"]), marks, esc(p.get("dismissal") or "not out"),
                       esc(p.get("r") if p.get("r") is not None else "-"),
                       esc(p.get("b") if p.get("b") is not None else "-"),
                       esc(p.get("fours") if p.get("fours") is not None else "-"),
                       esc(p.get("sixes") if p.get("sixes") is not None else "-"),
                       esc(p.get("sr") if p.get("sr") is not None else "-")))
    rows.append("</table></div>")
    return "\n".join(rows)


def _cricket_bowling_table(players):
    if not players:
        return ""
    rows = ["<div style='overflow-x:auto'><table><tr><th>Bowler</th>"
            "<th class='num'>O</th><th class='num'>M</th><th class='num'>R</th>"
            "<th class='num'>W</th><th class='num'>Econ</th></tr>"]
    for p in players:
        rows.append("<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s</td>"
                    "<td class='num'>%s</td><td class='num'><b>%s</b></td>"
                    "<td class='num'>%s</td></tr>"
                    % (esc(p["name"]),
                       esc(p.get("o") if p.get("o") is not None else "-"),
                       esc(p.get("m") if p.get("m") is not None else "-"),
                       esc(p.get("r") if p.get("r") is not None else "-"),
                       esc(p.get("w") if p.get("w") is not None else "-"),
                       esc(p.get("econ") if p.get("econ") is not None else "-")))
    rows.append("</table></div>")
    return "\n".join(rows)


def _ball_chip(b):
    """Short visual label for one ball: 0 1 2 3 4 6 W wd nb. Defensive: data varies."""
    if isinstance(b, dict):
        keys = {str(k).lower(): v for k, v in b.items()}
        txt = str(b.get("text") or b.get("commentary") or "").lower()
        wicket = (keys.get("wicket") or keys.get("iswicket") or keys.get("dismissal")
                  or keys.get("out") or ("wicket" in txt and "no" not in txt.split("wicket")[0][-12:]))
        runs = keys.get("runs", keys.get("r", keys.get("score")))
        try:
            runs = int(str(runs).strip().split()[0])
        except Exception:
            runs = None
        extra = (keys.get("wide") or keys.get("wideball") or keys.get("noball")
                 or keys.get("no_ball") or keys.get("extra") or keys.get("extras")
                 or "wide" in txt or "no ball" in txt)
        if wicket:
            return ("W", "wk")
        if runs == 6:
            return ("6", "six")
        if runs == 4:
            return ("4", "four")
        if extra:
            return ((str(runs) if isinstance(runs, int) else "") + "•", "ex")
        if isinstance(runs, int):
            return (str(runs), "r%d" % runs if runs <= 3 else "r")
        return ("•", "")
    s = str(b or "").strip()
    if not s:
        return ("•", "")
    sl = s.lower()
    if "wicket" in sl or " out " in (" " + sl + " "):
        return ("W", "wk")
    import re as _re
    m = _re.search(r"(\d+)\s*runs?", sl)
    if m:
        r = int(m.group(1))
        if r == 6:
            return ("6", "six")
        if r == 4:
            return ("4", "four")
        return (str(r), "")
    if "wide" in sl or "no ball" in sl:
        return ("•", "ex")
    return ("•", "")


def _ball_over(b, idx):
    if isinstance(b, dict):
        for k in ("over", "o", "overnumber", "over_number", "overno"):
            v = b.get(k)
            try:
                return int(float(str(v).split(".")[0]))
            except Exception:
                pass
    return idx // 6


_OVERS_CSS = """
.ovr{margin:8px 0}.ovr-h{font-size:12px;color:#5b6472;margin-bottom:4px;font-weight:600}
.ovr-balls{display:flex;flex-wrap:wrap;gap:5px}
.bl{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;
font-size:12px;font-weight:700;background:#eef1f6;color:#333}
.bl.six{background:#1d9e57;color:#fff}.bl.four{background:#2f7fe0;color:#fff}
.bl.wk{background:#e5484d;color:#fff}.bl.ex{background:#f2b705;color:#333}
"""


def overs_html(balls):
    """Over-by-over visual: each over as a row of ball chips."""
    if not balls:
        return ""
    overs = {}
    order = []
    for i, b in enumerate(balls):
        o = _ball_over(b, i)
        if o not in overs:
            overs[o] = []
            order.append(o)
        overs[o].append(b)
    rows = ["<style>%s</style>" % _OVERS_CSS]
    for o in sorted(order)[:40]:
        chips = []
        for b in overs[o][:12]:
            label, cls = _ball_chip(b)
            tip = ""
            if isinstance(b, dict):
                tip = esc(str(b.get("text") or b.get("commentary") or "")[:120])
            chips.append('<span class="bl %s" title="%s">%s</span>'
                         % (cls, tip, esc(label)))
        rows.append('<div class="ovr"><div class="ovr-h">Over %s</div>'
                    '<div class="ovr-balls">%s</div></div>' % (o + 1, "".join(chips)))
    return "<h2>Over-by-Over</h2><div class='card'>%s</div>" % "".join(rows)


def build_cricket_match(cm):
    path = cricket_url(cm)
    state = cm["state"]
    if state == "live":
        title = "%s Live Score & Scorecard | ScoreBuzz" % cm["name"]
        desc = "Live cricket score: %s. %s Full ball-by-ball scorecard on ScoreBuzz." % (
            cm["name"], cm["score_text"])
    elif state == "recent":
        title = "%s: Full Scorecard & Result | ScoreBuzz" % cm["name"]
        desc = "Full scorecard and result: %s. %s Batting, bowling, fall of wickets on ScoreBuzz." % (
            cm["name"], cm["score_text"])
    else:
        title = "%s: Schedule, Venue & Scorecard | ScoreBuzz" % cm["name"]
        desc = "%s — %s at %s. Live scorecard on ScoreBuzz when play begins." % (
            cm["name"],
            (cm["date"].strftime("%a %d %b %Y %H:%M UTC") if cm["date"] else ""),
            cm["venue"] or "TBC")

    def logo(tname, size=44):
        img = (cm.get("team_imgs") or {}).get((tname or "").lower())
        if img:
            return '<img src="%s" alt="%s" width="%d" height="%d" loading="lazy">' % (
                esc(img), esc(tname), size, size)
        return ""

    badge = ('<span class="badge live">● LIVE</span>' if state == "live"
             else '<span class="badge">%s</span>' % esc(cm["status"] or state.title()))
    hero = ("""<div class='card scoreboard-hero'>%s
<div class='nm'>%s %s</div><div class='big' style='font-size:22px'>%s</div>
<div class='nm'>%s %s</div>
<p class='meta'>%s%s%s &middot; %s</p></div>"""
            % (badge, logo(cm["team1"]), esc(cm["team1"]),
               esc(cm["score_text"] or "vs"),
               logo(cm["team2"]), esc(cm["team2"]),
               esc(cm["venue"]), " — " if cm["venue"] and cm["date"] else "",
               esc(cm["date"].strftime("%a %d %b %Y %H:%M UTC") if cm["date"] else ""),
               esc(cm["series_name"])))
    parts = [hero]
    sc = cm.get("scorecard") or {}
    if sc.get("toss") or sc.get("umpires") or cm.get("venue"):
        info = []
        if sc.get("toss"):
            info.append("<b>Toss:</b> " + esc(sc["toss"]))
        if cm.get("venue"):
            info.append("<b>Venue:</b> " + esc(cm["venue"]))
        if sc.get("umpires"):
            info.append("<b>Umpires:</b> " + esc(sc["umpires"]))
        parts.append("<div class='card'><p class='meta'>%s</p></div>"
                     % " &nbsp;·&nbsp; ".join(info))
    for inn in sc.get("innings") or []:
        head = inn.get("name") or "Innings"
        if inn.get("total") is not None:
            head += " — %s/%s" % (inn["total"], inn.get("wickets") if inn.get("wickets") is not None else 0)
            if inn.get("overs"):
                head += " (%s ov)" % inn["overs"]
        parts.append('<div class="inn-head">%s</div>' % esc(head))
        if inn.get("batting"):
            parts.append("<h2>Batting</h2><div class='card'>%s</div>"
                         % _cricket_batting_table(inn["batting"]))
        if inn.get("bowling"):
            parts.append("<h2>Bowling</h2><div class='card'>%s</div>"
                         % _cricket_bowling_table(inn["bowling"]))
        if inn.get("fow"):
            parts.append("<h2>Fall of Wickets</h2><div class='card'><p class='meta'>%s</p></div>"
                         % esc("; ".join(inn["fow"])))
    balls = sc.get("balls") or []
    ov = overs_html(balls)
    if ov:
        parts.append(ov)
    if state == "live" and not (sc.get("innings")):
        parts.append('<div class="card"><p class="meta">Live scorecard is loading — '
                     'refreshes automatically.</p></div>')

    ev_jsonld = {
        "@context": "https://schema.org", "@type": "SportsEvent",
        "name": cm["name"], "sport": "Cricket",
        "startDate": cm["date"].isoformat() if cm["date"] else "",
        "homeTeam": {"@type": "SportsTeam", "name": cm["team1"]},
        "awayTeam": {"@type": "SportsTeam", "name": cm["team2"]},
        "url": "https://scorebuzz.net" + path,
    }
    if state == "upcoming":
        ev_jsonld["eventStatus"] = "https://schema.org/EventScheduled"
    if cm.get("venue"):
        ev_jsonld["location"] = {"@type": "SportsActivityLocation", "name": cm["venue"]}
    crumbs = [("Home", "/"), ("Cricket", "/cricket/"),
              (cm["series_name"], "/cricket/%s/" % cm["series_slug"]),
              (cm["name"][:60], path)]
    return page(title, desc, path, crumbs, "\n".join(parts), ev_jsonld)


# ----------------------------------------------------------------------------
# Sitemap / robots / main
# ----------------------------------------------------------------------------

def build_sitemap(urls):
    today = today_utc().isoformat()
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in sorted(set(urls)):
        out.append("  <url><loc>%s%s</loc><lastmod>%s</lastmod>"
                   "<changefreq>hourly</changefreq></url>"
                   % (BASE_URL, u, today))
    out.append("</urlset>")
    return "\n".join(out)


def build_sitemapindex(filenames):
    today = today_utc().isoformat()
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for fn in filenames:
        out.append("  <sitemap><loc>%s/%s</loc><lastmod>%s</lastmod></sitemap>"
                   % (BASE_URL, fn, today))
    out.append("</sitemapindex>")
    return "\n".join(out)


SITEMAP_MATCH_CHUNK = 40000  # max match URLs per sitemap-matches-N.xml


def main():
    start = time.time()
    log("ScoreBuzz generator starting (%d leagues)" % len(LEAGUES))
    leagues = [{"sport": s, "code": c, "name": n, "slug": sl,
                "sport_label": spl, "logo_sport": ls}
               for s, c, n, sl, spl, ls in LEAGUES]

    all_matches, league_events = [], {}
    teams = {}  # team_id -> {info..., "events": [matches]}

    # 1) scoreboards
    for lg in leagues:
        url = ("https://site.api.espn.com/apis/site/v2/sports/%s/%s/scoreboard"
               % (lg["sport"], lg["code"]))
        data = fetch_json(url)
        STATS["scoreboards"] += 1
        if not data or not data.get("events"):
            STATS["failed"] += 1
            STATS["failed_leagues"].append(lg["slug"])
            log("FAIL scoreboard: %s" % lg["slug"])
            continue
        events = []
        for ev in data["events"]:
            m = parse_event(lg, ev)
            if not m:
                continue
            events.append(m)
            all_matches.append(m)
            for t in (m["home"], m["away"]):
                if t["id"]:
                    rec = teams.setdefault(t["id"], dict(t))
                    rec.setdefault("events", []).append(m)
        league_events[lg["slug"]] = sorted(
            events, key=lambda m: m["date"] or datetime.max.replace(tzinfo=timezone.utc))
        log("scoreboard %s: %d events" % (lg["slug"], len(events)))

    # 2) summaries (live + within window), bounded per league
    now = datetime.now(timezone.utc)
    lo, hi = now - timedelta(days=SUMMARY_WINDOW_DAYS), now + timedelta(days=SUMMARY_WINDOW_DAYS)
    summaries = {}
    for lg in leagues:
        done = 0
        for m in league_events.get(lg["slug"], []):
            if done >= MAX_SUMMARIES_PER_LEAGUE:
                break
            in_window = m["date"] and lo <= m["date"] <= hi
            if not (m["state"] == "in" or in_window):
                continue
            url = ("https://site.api.espn.com/apis/site/v2/sports/%s/%s/summary?event=%s"
                   % (lg["sport"], lg["code"], m["id"]))
            data = fetch_json(url)
            if data:
                summaries[m["id"]] = parse_summary(data, m)
                STATS["summaries"] += 1
                done += 1
            else:
                STATS["failed"] += 1
        log("summaries %s: %d" % (lg["slug"], done))

    # 2b) permanent match archive — ended matches keep their URLs forever
    import archive as archive_mod
    ARCHIVE_DIR = os.path.join(HERE, "match_archive")
    arc = archive_mod.load(ARCHIVE_DIR)
    n_arc_before = sum(len(v) for v in arc.values())
    archive_mod.merge(arc, all_matches, summaries)
    kept, dropped = archive_mod.prune(arc)
    log("archive: %d -> %d matches (%d dropped as older than %d days)"
        % (n_arc_before, kept, dropped, archive_mod.ARCHIVE_KEEP_DAYS))

    # 3) standings (best effort)
    standings = {}
    for lg in leagues:
        url = ("https://site.api.espn.com/apis/site/v2/sports/%s/%s/standings"
               % (lg["sport"], lg["code"]))
        data = fetch_json(url)
        grp = parse_standings(data) if data else []
        standings[lg["slug"]] = grp
        log("standings %s: %s" % (lg["slug"], "ok (%d groups)" % len(grp) if grp else "n/a"))

    # 4) cricket (budget-capped module; never breaks the build) — fetched
    # before rendering so pages + search index can use it
    cricket_data = {"matches": [], "series": [], "hits": 0, "errors": []}
    try:
        import cricket as cricket_mod
        cricket_data = cricket_mod.fetch_cricket()
    except Exception as e:
        cricket_data["errors"].append(str(e))
        log("cricket module failed gracefully: %s" % e)
    cmatches = cricket_data.get("matches") or []
    cseries = cricket_data.get("series") or []
    log("cricket: %d matches, %d series, %d API hits today%s"
        % (len(cmatches), len(cseries), cricket_data.get("hits", 0),
           (" errors: " + "; ".join(cricket_data.get("errors", [])))
           if cricket_data.get("errors") else ""))

    # 5) render — from the permanent archive, so ended matches keep their
    # URLs, stay in the sitemap and keep earning search traffic
    urls = []
    match_urls = []  # match pages go to sitemap-matches-N.xml files
    today = today_utc()

    arch_summaries = {}
    arch_all = []
    arch_league_events = {}
    for lg_slug, mid, rec in archive_mod.all_records(arc):
        m, s = rec["m"], rec["s"]
        arch_all.append(m)
        arch_summaries[m["id"]] = s
        arch_league_events.setdefault(lg_slug, []).append(m)
    for lg_slug in arch_league_events:
        arch_league_events[lg_slug].sort(
            key=lambda m: m["date"] or datetime.max.replace(tzinfo=timezone.utc),
            reverse=True)
    # team history from the archive (full results, not just this week)
    teams = {}
    for m in arch_all:
        for t in (m["home"], m["away"]):
            if t["id"]:
                trec = teams.setdefault(t["id"], dict(t))
                trec.setdefault("events", []).append(m)

    live = sorted([m for m in arch_all if m["state"] == "in"],
                  key=lambda m: m["date"] or datetime.max.replace(tzinfo=timezone.utc))
    todays = sorted([m for m in arch_all
                     if m["date"] and m["date"].date() == today and m["state"] != "post"],
                    key=lambda m: m["date"])
    upcoming = sorted(
        [m for m in arch_all
         if m["state"] == "pre" and m["date"] and today <= m["date"].date() <= today + timedelta(days=7)],
        key=lambda m: m["date"])
    recent = sorted(
        [m for m in arch_all
         if m["state"] == "post" and m["date"] and today - timedelta(days=3) <= m["date"].date() <= today],
        key=lambda m: m["date"], reverse=True)
    trend_html = trending_bar(live, todays)
    urls.append(write_file("index.html", build_home(live, upcoming, recent, leagues,
                                                    trend_html)))
    urls.append(write_file("leagues/index.html", build_leagues_index(leagues)))
    stale_hub = os.path.join(OUT_DIR, "football", "index.html")
    if os.path.exists(stale_hub):
        os.remove(stale_hub)  # old leagues-hub location; sport pages now live at /<sport>/
    # clean sport category pages: /football/, /american-football/, ...
    for sport in SPORTS_NAV:
        sslug = slugify(sport)
        s_leagues = [lg for lg in leagues if lg["sport_label"] == sport]
        s_events = [m for m in arch_all if m["league"]["sport_label"] == sport]
        if not s_leagues and not s_events:
            continue
        urls.append(write_file("%s/index.html" % sslug,
                               build_sport_page(sport, sslug, s_leagues, s_events)))
    urls.append(write_file("favourites/index.html", build_favourites()))
    urls.append(write_file("search/index.html", build_search_page()))

    # client-side search index: teams + leagues (zero backend)
    search_index = {
        "leagues": ([{"n": lg["name"], "u": "/football/%s/" % lg["slug"],
                      "s": lg["sport_label"]} for lg in leagues] +
                    [{"n": s["name"], "u": "/cricket/%s/" % s["slug"],
                      "s": "Cricket"} for s in (cricket_data.get("series") or [])]),
        "teams": [{"n": t["name"], "u": "/team/%s/" % team_slug(t)}
                  for _, t in sorted(teams.items(),
                                     key=lambda kv: kv[1].get("name") or "")
                  if t.get("name")],
    }
    with open(os.path.join(OUT_DIR, "search.json"), "w",
              encoding="utf-8") as f:
        json.dump(search_index, f, separators=(",", ":"))

    for lg in leagues:
        evs = arch_league_events.get(lg["slug"], [])
        if not evs and not standings.get(lg["slug"]):
            continue
        urls.append(write_file("football/%s/index.html" % lg["slug"],
                               build_league(lg, evs, standings.get(lg["slug"]),
                                            trend_html)))

    n_match = 0
    n_skipped = 0
    for m in arch_all:
        lg_slug = m["league"]["slug"]
        rec = arc[lg_slug][str(m["id"])]
        h = "%s|v%d" % (rec["hash"], archive_mod.TEMPLATE_VERSION)
        rel = "football/%s/%s/index.html" % (lg_slug, match_slug(m))
        full = os.path.join(OUT_DIR, rel)
        page_url = "/" + rel
        if rec.get("rendered_hash") == h and os.path.exists(full):
            urls.append(page_url)  # unchanged — keep the sitemap entry
            match_urls.append(page_url)
            n_skipped += 1
            continue
        urls.append(write_file(rel, build_match(m, arch_summaries.get(m["id"]))))
        match_urls.append(page_url)
        rec["rendered_hash"] = h
        n_match += 1

    n_team = 0
    for tid, t in teams.items():
        if not t.get("name") or not t.get("events"):
            continue
        rel = "team/%s/index.html" % team_slug(t)
        urls.append(write_file(rel, build_team(t, t["events"])))
        n_team += 1

    # 6) cricket pages (data already fetched in step 4)
    urls.append(write_file("cricket/index.html",
                           build_cricket_index(cmatches, cseries)))
    series_by_slug = {}
    for s in cseries:
        series_by_slug[s["slug"]] = s
        sm = [m for m in cmatches if m["series_slug"] == s["slug"]]
        urls.append(write_file("cricket/%s/index.html" % s["slug"],
                               build_cricket_series(s, sm)))
    for cm in cmatches:
        rel = "cricket/%s/%s/index.html" % (cm["series_slug"], cm["slug"])
        page_url = "/" + rel
        urls.append(write_file(rel, build_cricket_match(cm)))
        match_urls.append(page_url)

    # sitemap index: main file + chunked match files (40k URLs each)
    match_url_set = set(match_urls)
    main_urls = [u for u in urls if u not in match_url_set]
    sitemap_files = []
    write_file("sitemap-main.xml", build_sitemap(main_urls))
    sitemap_files.append("sitemap-main.xml")
    for i in range(0, len(match_urls), SITEMAP_MATCH_CHUNK):
        fn = "sitemap-matches-%d.xml" % (i // SITEMAP_MATCH_CHUNK + 1)
        write_file(fn, build_sitemap(match_urls[i:i + SITEMAP_MATCH_CHUNK]))
        sitemap_files.append(fn)
    write_file("sitemap.xml", build_sitemapindex(sitemap_files))

    # IndexNow queue: changed page URLs for the post-deploy ping (cap 10k)
    try:
        with open(os.path.join(HERE, "indexnow-queue.json"), "w", encoding="utf-8") as f:
            json.dump(CHANGED_URLS[:10000], f)
        log("indexnow: %d changed URLs queued" % min(len(CHANGED_URLS), 10000))
    except Exception as e:
        log("indexnow queue write failed: %s" % e)
    log("sitemap: %d main urls, %d match urls in %d match files"
        % (len(main_urls), len(match_urls), len(sitemap_files) - 1))
    write_file("robots.txt",
               "User-agent: *\nAllow: /\n\nSitemap: %s/sitemap.xml\n" % BASE_URL)

    archive_mod.save(ARCHIVE_DIR, arc)

    el = time.time() - start
    log("DONE in %.0fs — leagues ok: %d/%d, matches: %d rendered (%d skipped), "
        "teams: %d, summaries: %d, cricket: %d matches, urls: %d"
        % (el, len(leagues) - len(STATS["failed_leagues"]), len(leagues),
           n_match, n_skipped, n_team, STATS["summaries"], len(cmatches), len(urls)))
    if STATS["failed_leagues"]:
        log("failed leagues: %s" % ", ".join(STATS["failed_leagues"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
