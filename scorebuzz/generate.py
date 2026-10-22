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
import shutil
import base64
import urllib.parse
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


def sport_slug_of(label):
    """URL segment for a sport category: Football->football,
    American Football->american-football, Basketball->basketball, ..."""
    return slugify(label or "Football")


def league_path(lg):
    """Category-correct league URL, e.g. /basketball/nba/ (dict form)."""
    return "/%s/%s/" % (sport_slug_of(lg.get("sport_label")), lg["slug"])


def league_path_for(label, slug):
    return "/%s/%s/" % (sport_slug_of(label), slug)


_SLUG_LABEL = {r[3]: r[4] for r in LEAGUES}


def match_path(m):
    return "/%s/%s/%s/" % (sport_slug_of(m["league"].get("sport_label")),
                           m["league"]["slug"], match_slug(m))


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
:root{--bg:#edf0f4;--card:#ffffff;--line:#dde3ea;--txt:#15222f;--mut:#5d6f82;
--acc:#1447c8;--grn:#178a4c;--red:#d92d20;--amber:#b8860b;--bg2:#eef2f7}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;line-height:1.5;font-size:16px}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1040px;margin:0 auto;padding:0 14px}
.wrap-wide{max-width:1360px}
header.top{background:#0e2a1d;background:linear-gradient(90deg,#0b3524,#0e5136);border-bottom:3px solid var(--grn);position:sticky;top:0;z-index:10}
.top-in{display:flex;align-items:center;gap:18px;padding:12px 14px;max-width:1360px;margin:0 auto}
.brand{font-size:24px;font-weight:800;color:#fff;letter-spacing:.5px}
.brand b{color:#7ee2a8}
.brand:hover{text-decoration:none}
nav.main{display:flex;gap:6px;overflow-x:auto;padding:0 14px 12px;max-width:1360px;margin:0 auto}
nav.main a{color:#d7e3dc;font-size:13px;font-weight:600;padding:6px 13px;border-radius:999px;white-space:nowrap;background:rgba(255,255,255,.09)}
nav.main a:hover{background:rgba(255,255,255,.22);color:#fff;text-decoration:none}
h1{font-size:25px;margin:20px 0 12px}h2{font-size:19px;margin:24px 0 10px;
border-bottom:2px solid var(--line);padding-bottom:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:10px 0;box-shadow:0 1px 2px rgba(21,34,47,.05)}
.mrow{display:flex;align-items:center;gap:10px;padding:10px 6px;border-bottom:1px solid var(--line);border-radius:6px}
.tlg{display:inline-flex;align-items:center;justify-content:center;border-radius:50%;background:#e4eae5;color:#0e5136;font-weight:700;flex:none;vertical-align:middle}
.mrow:hover{background:#f4f8fd}
.mrow:last-child{border-bottom:0}
.mrow .st{font-size:12px;color:var(--mut);width:86px;flex-shrink:0;font-weight:600}
.mrow .st.live{color:var(--red);font-weight:800}
.teams{flex:1;min-width:0}.tn{font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.mcomp{font-size:10.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px;margin-top:2px}
.tn img{width:20px;height:20px;vertical-align:-4px;margin-right:6px}
.sc{font-weight:700;font-size:15px;text-align:right;white-space:nowrap}
.sc .w{color:var(--grn)}
table{width:100%;border-collapse:collapse;font-size:14px;margin:8px 0}
th,td{padding:8px 6px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase}
td.num,th.num{text-align:right}.pos{color:var(--mut);width:34px}
.bar-row{display:flex;align-items:center;gap:8px;margin:7px 0;font-size:13px}
.bar-row .v{width:44px;text-align:center;font-weight:700}
.bar{flex:1;height:8px;background:#e3e9f0;border-radius:4px;position:relative;overflow:hidden}
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
.fW{background:var(--grn)}.fD{background:#8494a7}.fL{background:var(--red)}
.badge{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px;
background:#e8effc;color:var(--acc);margin-bottom:8px}
.badge.live{background:#fdeceb;color:var(--red)}
.meta{color:var(--mut);font-size:13px;margin:6px 0}
footer{border-top:1px solid var(--line);margin-top:36px;padding:20px 14px;color:var(--mut);font-size:13px;background:#fff}
footer .wrap{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.kv{display:flex;gap:8px;font-size:14px;padding:5px 0;border-bottom:1px solid var(--line)}
.kv b{min-width:130px;color:var(--mut);font-weight:600}
.scoreboard-hero{text-align:center;padding:16px 12px}
.hero-comp{font-size:14px;font-weight:700}
.hero-comp a{color:inherit}
.hero-meta{color:var(--mut);font-size:12.5px;margin-top:3px}
.hero-row{display:flex;align-items:center;justify-content:center;gap:6px;margin-top:14px}
.hero-team{flex:1;display:flex;align-items:center;gap:10px;min-width:0;color:inherit}
.hero-team:hover{text-decoration:none}
.ht-home{justify-content:flex-end;text-align:right}
.ht-away{justify-content:flex-start;text-align:left}
.hero-team .nm{font-size:17px;font-weight:700}
.hero-mid{flex:none;min-width:112px}
.scoreboard-hero .big{font-size:34px;font-weight:800;letter-spacing:.5px;white-space:nowrap}
.hero-status{margin-top:3px}
.cd-cap{font-size:10px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;color:var(--mut);margin-bottom:6px}
.cd{display:flex;align-items:flex-start;justify-content:center;gap:6px}
.cd-u{background:linear-gradient(160deg,#148352 0%,#0b3524 80%);color:#fff;border:1px solid rgba(255,255,255,.16);border-radius:12px;min-width:56px;padding:8px 5px 6px;text-align:center;box-shadow:0 8px 18px rgba(8,40,24,.35),inset 0 1px 0 rgba(255,255,255,.22),inset 0 -8px 14px rgba(0,0,0,.25)}
.cd-n{display:block;font-size:25px;font-weight:800;line-height:1;font-variant-numeric:tabular-nums;text-shadow:0 2px 6px rgba(0,0,0,.35)}
.cd-l{display:block;font-size:9px;letter-spacing:2px;color:#a9dfc0;margin-top:4px;font-weight:700}
.cd-sep{font-size:22px;font-weight:800;color:#0e7a44;padding-top:7px;animation:cdblink 1s steps(1) infinite}
.fl{position:relative;height:30px;perspective:280px}
.fl-h{position:absolute;left:0;width:100%;height:15px;overflow:hidden}
.fl-h i,.fl-f i{font-style:normal;display:block;height:30px;line-height:30px;font-size:25px;font-weight:800;text-align:center;text-shadow:0 2px 6px rgba(0,0,0,.35)}
.fl-t{top:0}
.fl-b{top:15px}
.fl-b i{transform:translateY(-15px)}
.fl::after{content:'';position:absolute;top:15px;left:2px;right:2px;height:1px;background:rgba(0,0,0,.4);z-index:3}
.fl-leaf{position:absolute;top:0;left:0;width:100%;height:15px;transform-origin:bottom;z-index:2}
.fl-f{position:absolute;left:0;top:0;width:100%;height:15px;overflow:hidden;background:linear-gradient(180deg,#177a4e,#116540);border-radius:6px 6px 0 0;backface-visibility:hidden;-webkit-backface-visibility:hidden}
.fl.go .fl-leaf{animation:flfold .48s ease-in forwards}
@keyframes flfold{from{transform:rotateX(0deg)}to{transform:rotateX(-90deg)}}
@keyframes cdblink{0%,55%{opacity:1}56%,100%{opacity:.2}}
.wbtn{display:inline-flex;align-items:center;gap:7px;margin-top:10px;background:var(--acc);color:#fff;font-weight:700;font-size:13.5px;padding:8px 20px;border-radius:999px;box-shadow:0 2px 8px rgba(20,71,200,.3)}
.wbtn:hover{text-decoration:none;filter:brightness(1.1)}
.wbtn.live{background:var(--red);box-shadow:0 2px 8px rgba(217,45,32,.35)}
.wbtn .pulse{background:#fff}
.rwbtn{flex:none;font-size:11.5px;font-weight:800;padding:5px 12px;border-radius:999px;background:#e8effc;color:var(--acc);white-space:nowrap}
.rwbtn.live{background:#fdeceb;color:var(--red)}
.cdrow{display:flex;align-items:flex-start;justify-content:flex-end;gap:2px}
.fu{background:linear-gradient(160deg,#148352,#0b3524);color:#fff;border-radius:7px;padding:3px 3px 2px;box-shadow:0 2px 6px rgba(8,40,24,.3)}
.fu .fl{width:27px;height:20px;perspective:160px}
.fu .fl-h{height:10px}
.fu .fl-b{top:10px}
.fu .fl-b i{transform:translateY(-10px)}
.fu .fl-h i,.fu .fl-f i{height:20px;line-height:20px;font-size:15px;text-shadow:none}
.fu .fl-leaf,.fu .fl-f{height:10px}
.fu .fl-f{border-radius:4px 4px 0 0}
.fu .fl::after{top:10px;left:1px;right:1px}
.cdrow .cd-sep{font-size:13px;padding-top:3px;animation:cdblink 1s steps(1) infinite}
@media(max-width:560px){.cd-u{min-width:47px}.cd-n{font-size:20px}
.fl{height:26px}.fl-h,.fl-leaf,.fl-f{height:13px}
.fl-h i,.fl-f i{height:26px;line-height:26px;font-size:20px}
.fl-b{top:13px}.fl-b i{transform:translateY(-13px)}.fl::after{top:13px}}
.scorers{display:flex;gap:8px;margin-top:14px;border-top:1px solid var(--line);padding-top:10px;font-size:13px}
.sc-side{flex:1;line-height:1.6;min-width:0}
.sc-home{text-align:right}
.sc-away{text-align:left}
.sc-ball{flex:none;color:var(--mut)}
@media(max-width:560px){.hero-team .nm{font-size:13.5px}.hero-mid{min-width:86px}.scoreboard-hero .big{font-size:25px}}
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
@keyframes pl{0%{box-shadow:0 0 0 0 rgba(217,45,32,.5)}
70%{box-shadow:0 0 0 8px rgba(217,45,32,0)}100%{box-shadow:0 0 0 0 rgba(217,45,32,0)}}
.tline{display:flex;align-items:center;gap:6px;font-size:13px;padding:2px 0}
.tline img{width:18px;height:18px}
.tline .tn2{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tline b{font-size:14px}
.tleague{font-size:10px;color:var(--mut);margin-top:6px;display:block;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fav-star{background:none;border:0;color:#a7b4c2;font-size:19px;cursor:pointer;
padding:2px 8px;line-height:1;flex-shrink:0}
.fav-star:hover{color:var(--amber)}
.fav-star.on{color:var(--amber)}
h1 .fav-star{font-size:22px;vertical-align:middle}
.sbox{margin-left:auto}
.sbox input{background:#fff;border:1px solid rgba(255,255,255,.4);color:var(--txt);
border-radius:999px;padding:8px 14px;font-size:14px;width:210px}
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
border-radius:4px;padding:0 5px;margin-left:5px;color:var(--amber);background:#fff8ea}
.inn-head{font-size:16px;font-weight:700;margin:14px 0 6px}
/* ---- three-column layout (list pages) ---- */
.layout{display:grid;grid-template-columns:238px minmax(0,1fr) 294px;gap:16px;align-items:start;padding-top:4px}
.content{min-width:0}
.side,.rail{position:sticky;top:116px}
.sidebox{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:0 0 14px;box-shadow:0 1px 2px rgba(21,34,47,.05)}
.sidebox h3{font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--mut);margin:2px 4px 8px}
.sidebox h4{font-size:12px;color:var(--mut);margin:10px 4px 4px;font-weight:700}
.sidebox a{display:flex;justify-content:space-between;gap:8px;padding:6px 8px;border-radius:8px;color:var(--txt);font-size:14px}
.sidebox a:hover{background:#eef4ff;text-decoration:none}
.sidebox a .cnt{color:var(--mut);font-size:12px}
.sidebox.scroll{max-height:560px;overflow-y:auto}
.mini{display:flex;gap:8px;align-items:center;padding:7px 4px;border-bottom:1px solid var(--line);font-size:13px;color:inherit}
.mini:last-child{border-bottom:0}
.mini:hover{text-decoration:none;background:#f4f8fd}
.mini .mt{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mini .ms{font-weight:700;white-space:nowrap}
.mini .ml{color:var(--red);font-weight:800;font-size:11px;white-space:nowrap}
.mini .ml.mut{color:var(--mut)}
@media(max-width:1100px){.rail{display:none}.layout{grid-template-columns:224px minmax(0,1fr)}}
@media(max-width:760px){.layout{grid-template-columns:minmax(0,1fr)}.side{position:static}.side .sidebox.scroll{display:none}}
/* ---- league group blocks + date strip ---- */
.lgroup{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:12px 0;overflow:hidden;box-shadow:0 1px 2px rgba(21,34,47,.05)}
.lg-head{display:flex;align-items:center;gap:8px;padding:10px 14px;background:#eef4ec;border-bottom:1px solid var(--line);font-weight:700;font-size:15px}
.lg-head a{color:var(--txt)}
.lg-head .lg-cnt{margin-left:auto;font-size:12px;color:var(--mut);font-weight:600}
.lgroup .mrow{padding-left:14px;padding-right:10px}
.datestrip{display:flex;gap:8px;margin:12px 0 2px;flex-wrap:wrap}
.datestrip a{padding:7px 15px;border:1px solid var(--line);border-radius:999px;background:var(--card);color:var(--txt);font-size:13px;font-weight:600}
.datestrip a:hover{border-color:var(--grn);text-decoration:none}
.datestrip a.live{color:var(--red);border-color:#f0b8b3}
.datestrip a.on{background:var(--grn);color:#fff;border-color:var(--grn)}
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


def page(title, desc, path, crumbs, body_html, extra_jsonld=None, layout=None):
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
    if layout:
        main_html = ('<main class="wrap wrap-wide"><div class="layout">'
                     '<aside class="side">%s</aside>'
                     '<div class="content">%s</div>'
                     '<aside class="rail">%s</aside></div></main>'
                     % (layout[0], body_html, layout[1]))
    else:
        main_html = '<main class="wrap">\n%s\n</main>' % body_html
    return ("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/svg+xml" href="/favicon.svg"><link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
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
%s
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
<script>
(function(){
var rows=[].slice.call(document.querySelectorAll('[data-eid]'));
if(!rows.length)return;
function pc(s){if(!s)return null;s=String(s);var a=s.match(/(\d+):(\d+)/);if(a)return parseInt(a[1],10)*60+parseInt(a[2],10);var b=s.match(/(\d+)\s*'/);if(b)return parseInt(b[1],10)*60;return null;}
rows.forEach(function(r){r._clk=pc(r.getAttribute('data-clock'));r._at=Date.now();r._state=r.getAttribute('data-state');});
var live0=false;setTimeout(function(){live0=true;},4000);
setInterval(function(){if(!live0)return;var now=Date.now();rows.forEach(function(r){
if(r._state!=='in'||r._clk==null||r.getAttribute('data-tick')!=='1')return;
var adv=Math.floor((now-r._at)/1000);if(adv>75)adv=75;
var mm=Math.floor((r._clk+adv)/60);var el=r.querySelector('.js-st');
if(el)el.textContent='LIVE '+mm+String.fromCharCode(39);
});},1000);
function upd(r,e){
var st=(e.status&&e.status.type)||{};var comp=(e.competitions||[])[0]||{};var cs=comp.competitors||[];
if(r.getAttribute('data-cricket')==='1'){
var parts=[];cs.forEach(function(c){var s=c.score;if(s!=null&&String(s)!==''){parts.push((((c.team||{}).displayName)||'')+' '+s);}});
var cel=r.querySelector('.js-cs');if(cel&&parts.length)cel.textContent=parts.join('   ');
var el2=r.querySelector('.js-st');var summ=(e.status||{}).summary||'';
if(st.state==='post'){r._state='post';r.setAttribute('data-state','post');if(el2){el2.textContent=summ||'Result';el2.classList.remove('live');}}
else if(st.state==='in'){r._state='in';if(el2){el2.textContent='\u25cf LIVE';el2.classList.add('live');}}
return;
}
var H=null,A=null;cs.forEach(function(c){if(c.homeAway==='home')H=c;if(c.homeAway==='away')A=c;});
var he=r.querySelector('.js-hs'),ae=r.querySelector('.js-as');
if(H&&he&&H.score!=null)he.textContent=H.score;
if(A&&ae&&A.score!=null)ae.textContent=A.score;
var big=r.querySelector('.js-big');
if(big&&H&&A&&H.score!=null&&A.score!=null)big.textContent=H.score+' - '+A.score;
var cd=r.querySelector('.js-cd');var wb=r.querySelector('.js-wbtn');
if(cd&&(st.state==='in'||st.state==='post')){cd.setAttribute('data-done','1');
if(big){cd.style.display='none';big.style.display='';}
else if(H&&A&&H.score!=null&&A.score!=null){cd.textContent=H.score+' - '+A.score;cd.classList.remove('cdrow');cd.style.display='';}
else{cd.style.display='none';}}
if(st.state==='in'){if(wb){wb.innerHTML='\u25cf LIVE';wb.classList.add('live');}}
else if(st.state==='post'){if(wb)wb.style.display='none';}
var el=r.querySelector('.js-st');var det=st.detail||'';
if(st.state==='post'){r._state='post';r.setAttribute('data-state','post');
if(el){el.textContent=st.shortDetail||det||'FT';el.classList.remove('live');}}
else if(st.state==='in'){r._state='in';
if(/half/i.test(det)){r._clk=null;if(el)el.textContent='HT';}
else{var c=pc(e.status.displayClock);if(c!=null){r._clk=c;r._at=Date.now();}
if(el)el.textContent='LIVE '+(e.status.displayClock||st.shortDetail||'');}
if(el)el.classList.add('live');}
}
function refresh(){
var by={};var any=false;
rows.forEach(function(r){if(r._state==='in'||r._state==='pre'){var k=r.getAttribute('data-espn');if(k){(by[k]=by[k]||[]).push(r);any=true;}}});
if(!any)return;
Object.keys(by).forEach(function(k){
fetch('/api/'+k+'/scoreboard').then(function(x){return x.json();}).then(function(d){
var evs={};(d.events||[]).forEach(function(e){evs[String(e.id)]=e;});
by[k].forEach(function(r){var e=evs[r.getAttribute('data-eid')];if(e)upd(r,e);});
}).catch(function(){}).then(function(){live0=true;});
});
}
refresh();setInterval(refresh,15000);
document.addEventListener('visibilitychange',function(){if(!document.hidden)refresh();});
function flipTo(fl,nv,inst){if(!fl)return;var t=fl.querySelector('.fl-t i'),b=fl.querySelector('.fl-b i'),f=fl.querySelector('.fl-f i');if(inst){t.textContent=nv;b.textContent=nv;f.textContent=nv;fl.classList.remove('go');fl._busy=0;return;}var cur=f.textContent;if(cur===nv)return;
if(fl._busy){t.textContent=nv;b.textContent=nv;f.textContent=nv;return;}
fl._busy=1;t.textContent=nv;fl.classList.remove('go');void fl.offsetWidth;fl.classList.add('go');
setTimeout(function(){b.textContent=nv;},240);
setTimeout(function(){f.textContent=nv;fl.classList.remove('go');fl._busy=0;},500);}
var cds=document.querySelectorAll('.js-cd');
if(cds.length){var cdInit=true;var tickCd=function(){var now=Date.now();cds.forEach(function(cd){if(cd.getAttribute('data-done')==='1')return;var t=Date.parse(cd.getAttribute('data-kickoff'));if(isNaN(t))return;var s=Math.max(0,Math.floor((t-now)/1000));var h=Math.floor(s/3600),mi=Math.floor((s-h*3600)/60),se=s-h*3600-mi*60;var p=function(n){return (n<10?'0':'')+n;};var fh=cd.querySelector('[data-u="h"]');if(fh){flipTo(fh,p(h),cdInit);flipTo(cd.querySelector('[data-u="m"]'),p(mi),cdInit);flipTo(cd.querySelector('[data-u="s"]'),p(se),cdInit);}else{var eh=cd.querySelector('.js-cd-h');if(eh){var em=cd.querySelector('.js-cd-m'),es=cd.querySelector('.js-cd-s');eh.textContent=p(h);if(em)em.textContent=p(mi);if(es)es.textContent=p(se);}else{var tt=cd.querySelector('.js-cd-t');if(tt)tt.textContent=p(h)+':'+p(mi)+':'+p(se);else cd.textContent=p(h)+':'+p(mi)+':'+p(se);}}});};tickCd();cdInit=false;setInterval(tickCd,1000);}
var strip=document.querySelector('.datestrip');
if(strip){
function applyF(f,day,isToday,a){
strip.querySelectorAll('a').forEach(function(x){x.classList.toggle('on',x===a);});
var lc=document.getElementById('live');if(lc)lc.style.display=(f==='all'||f==='live'||(f==='day'&&isToday))?'':'none';
var nl=document.getElementById('nolive');if(nl)nl.style.display=(f==='all'||f==='live')?'':'none';
var md=document.getElementById('motd');if(md)md.style.display=(f==='all'||(f==='day'&&isToday))?'':'none';
document.querySelectorAll('.fhide').forEach(function(el){el.style.display=(f==='all')?'':'none';});
document.querySelectorAll('.dayblock').forEach(function(b){b.style.display=(f==='all'||(f==='day'&&b.getAttribute('data-day')===day))?'':'none';});
document.querySelectorAll('.sportblock').forEach(function(s){if(f==='all'){s.style.display='';return;}var any=false;s.querySelectorAll('.dayblock').forEach(function(b){if(b.style.display!=='none')any=true;});s.style.display=any?'':'none';});
document.querySelectorAll('.fsec').forEach(function(s){if(f==='all'){s.style.display='';return;}if(f==='live'){s.style.display='none';return;}var any=false;s.querySelectorAll('.dayblock').forEach(function(b){if(b.style.display!=='none')any=true;});s.style.display=any?'':'none';});
}
strip.querySelectorAll('a').forEach(function(a){a.addEventListener('click',function(ev){ev.preventDefault();applyF(a.getAttribute('data-f'),a.getAttribute('data-day'),a.getAttribute('data-today')==='1',a);});});
}
})();
</script>
</body>
</html>""" % (esc(title), esc(desc), canon, esc(title), esc(desc), canon,
               ga_snippet(), CSS, ld, nav, sport_links, "@@MAIN@@",
               datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))).replace(
        "@@MAIN@@", main_html)


CHANGED_URLS = []  # pages whose HTML actually changed this build (for IndexNow)

FAVICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0b3f2a"/><stop offset="1" stop-color="#178a4c"/></linearGradient></defs><rect width="64" height="64" rx="14" fill="url(#g)"/><text x="32" y="42" font-family="Arial,Helvetica,sans-serif" font-size="27" font-weight="700" fill="#ffffff" text-anchor="middle">SB</text></svg>'
FAVICON32_B64 = "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"
FAVICON180_B64 = "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"


IMG_FN_JS = '\nexport async function onRequest(context) {\n  const url = new URL(context.request.url);\n  const u = url.searchParams.get("u") || "";\n  let target;\n  try { target = new URL(u); } catch (e) { return new Response("bad url", { status: 400 }); }\n  if (["a.espncdn.com", "g.cricapi.com"].indexOf(target.hostname) < 0) {\n    return new Response("forbidden", { status: 403 });\n  }\n  const cache = caches.default;\n  const key = new Request(target.toString(), { method: "GET" });\n  let resp = await cache.match(key);\n  if (resp) return resp;\n  const up = await fetch(target.toString(), {\n    headers: { "User-Agent": "Mozilla/5.0", "Accept": "image/*" },\n  });\n  if (!up.ok) return new Response("upstream error", { status: 502 });\n  resp = new Response(up.body, {\n    headers: {\n      "Content-Type": up.headers.get("Content-Type") || "image/png",\n      "Cache-Control": "public, max-age=86400, s-maxage=604800",\n      "Access-Control-Allow-Origin": "*",\n    },\n  });\n  context.waitUntil(cache.put(key, resp.clone()));\n  return resp;\n}\n'
API_FN_JS = '\nexport async function onRequest(context) {\n  const url = new URL(context.request.url);\n  const path = url.pathname.replace(/^\\/api\\//, "");\n  if (!/^[a-z0-9./_-]+$/i.test(path)) return new Response("bad path", { status: 400 });\n  const target = "https://site.api.espn.com/apis/site/v2/sports/" + path + url.search;\n  const cache = caches.default;\n  const key = new Request(target, { method: "GET" });\n  let resp = await cache.match(key);\n  if (resp) return resp;\n  const up = await fetch(target, { headers: { "User-Agent": "Mozilla/5.0" } });\n  if (!up.ok) {\n    return new Response(JSON.stringify({ error: "upstream " + up.status }), {\n      status: 502, headers: { "Content-Type": "application/json" },\n    });\n  }\n  const body = await up.text();\n  resp = new Response(body, {\n    headers: {\n      "Content-Type": "application/json",\n      "Cache-Control": "public, s-maxage=10, max-age=0",\n      "Access-Control-Allow-Origin": "*",\n    },\n  });\n  context.waitUntil(cache.put(key, resp.clone()));\n  return resp;\n}\n'


def write_binary(rel_path, data):
    full = os.path.join(OUT_DIR, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)


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


def _initials(name):
    words = [w for w in re.split(r"[^A-Za-z]+", name or "") if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def proxy_img(u):
    """Route third-party images through our own domain (/img edge proxy):
    some visitors' networks/ad-blockers block ESPN's CDN outright, which
    left crest slots empty. Same-domain images always load."""
    if u and ("a.espncdn.com" in u or "g.cricapi.com" in u):
        return "/img?u=" + urllib.parse.quote(u, safe="")
    return u or ""


def team_img(t, size=20):
    if t.get("logo"):
        return ('<img src="%s" alt="%s logo" width="%d" height="%d" loading="lazy">'
                % (esc(proxy_img(t["logo"])), esc(t["short"] or t["name"]), size, size))
    return ('<span class="tlg" style="width:%dpx;height:%dpx;font-size:%dpx">%s</span>'
            % (size, size, max(8, int(size * 0.4)),
               esc(_initials(t.get("name") or t.get("short") or "?"))))


def _live_attrs(m):
    """data-* hooks for the client-side live updater (see LIVE_JS in page())."""
    lg = m.get("league") or {}
    return (' data-eid="%s" data-espn="%s/%s" data-state="%s" '
            'data-clock="%s" data-tick="%s"'
            % (esc(m.get("id") or ""), esc(lg.get("sport") or ""),
               esc(lg.get("code") or ""), esc(m.get("state") or ""),
               esc(m.get("clock") or ""),
               "1" if lg.get("sport") == "soccer" else "0"))


def _flap_mini(val, unit):
    return ("<div class='fu'><div class='fl' data-u='%s'>"
            "<div class='fl-h fl-t'><i>%02d</i></div>"
            "<div class='fl-h fl-b'><i>%02d</i></div>"
            "<div class='fl-leaf'><div class='fl-f'><i>%02d</i></div></div>"
            "</div></div>" % (unit, val, val, val))


def match_row(m, comp=None):
    live = m["state"] == "in"
    st = '<span class="st js-st%s">%s</span>' % (" live" if live else "",
                                                 esc(status_label(m)))
    hs, aws = m["home"].get("score"), m["away"].get("score")
    def nm(t, s):
        cls = "w" if t.get("winner") and s is not None else ""
        return ('<div class="tn">%s%s</div>'
                % (team_img(t), esc(t["short"] or t["name"])))
    if m["state"] == "pre" and m["date"]:
        _rem = max(0, int((m["date"] - datetime.now(timezone.utc)).total_seconds()))
        _hh, _rem = divmod(_rem, 3600)
        _mi, _ss = divmod(_rem, 60)
        sc = ('<div class="sc cdrow js-cd" data-kickoff="%s">'
              % m["date"].strftime("%Y-%m-%dT%H:%M:%SZ"))
        sc += _flap_mini(_hh, "h") + '<span class="cd-sep">:</span>'
        sc += _flap_mini(_mi, "m") + '<span class="cd-sep">:</span>'
        sc += _flap_mini(_ss, "s") + '</div>'
    elif hs is None:
        sc = '<div class="sc">%s</div>' % esc(fmt_time(m["date"]))
    else:
        sc = ('<div class="sc"><span class="js-hs %s">%s</span><br>'
              '<span class="js-as %s">%s</span></div>'
              % ("w" if m["home"].get("winner") else "", esc(hs),
                 "w" if m["away"].get("winner") else "", esc(aws)))
    fkey = "match:%s" % match_path(m)
    fname = "%s vs %s" % (m["home"]["name"], m["away"]["name"])
    fdate = m["date"].strftime("%Y-%m-%d") if m["date"] else ""
    star = ('<button class="fav-star" data-fkey="%s" data-fname="%s" data-fdate="%s" '
            'aria-label="Save to favourites">&#9734;</button>'
            % (esc(fkey), esc(fname), esc(fdate)))
    comp_html = '<div class="mcomp">%s</div>' % esc(comp) if comp else ""
    if m["state"] == "pre":
        pill = '<span class="rwbtn js-wbtn">&#9654; Watch</span>'
    elif m["state"] == "in":
        pill = '<span class="rwbtn live js-wbtn">&#9679; LIVE</span>'
    else:
        pill = ""
    return ('<div class="mrow"%s>%s<a href="%s" style="flex:1;display:flex;'
            'align-items:center;gap:10px;min-width:0;color:inherit">'
            '%s<div class="teams">%s%s%s</div>%s%s</a>%s</div>'
            % (_live_attrs(m), st, match_path(m), "", nm(m["home"], hs),
               nm(m["away"], aws), comp_html, sc, pill, star))


def is_derby(m):
    hn, an = m["home"]["name"].lower(), m["away"]["name"].lower()
    for a, b in DERBY_PAIRS:
        if (a in hn and b in an) or (a in an and b in hn):
            return True
    return False


def trend_card(m, live):
    def line(t, s, cls):
        return ('<span class="tline">%s<span class="tn2">%s</span>'
                '<b class="%s">%s</b></span>'
                % (team_img(t, 18), esc(t["abbr"] or t["short"] or t["name"]),
                   cls, esc(s) if s is not None else ""))
    if live:
        stat = ('<span class="tstat live js-st"><span class="pulse"></span>%s</span>'
                % esc(m["clock"] or m["status_text"] or "LIVE"))
    else:
        when = fmt_time(m["date"]) if m["date"] else "Scheduled"
        stat = '<span class="tstat js-st">%s</span>' % esc(when)
    return ('<a class="tcard" href="%s"%s>%s%s%s<span class="tleague">%s%s</span></a>'
            % (match_path(m), _live_attrs(m), stat,
               line(m["home"], m["home"].get("score"), "js-hs"),
               line(m["away"], m["away"].get("score"), "js-as"),
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
            body.append('<p><a href="%s">%s</a> '
                        '<span class="meta">— fixtures, results, standings</span></p>'
                        % (league_path(lg), esc(lg["name"])))
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



def _sidebar_html():
    """Left rail: sports quick links + every competition, grouped by sport.
    Built from the LEAGUES config tuples (sport, code, name, slug, label, key).
    All links use the existing paths — nothing new is introduced."""
    out = ['<div class="sidebox"><h3>Sports</h3><a href="/">All Sports '
           '<span class="cnt">live</span></a>']
    out.append('<a href="/cricket/">Cricket</a>')
    for s in SPORTS_NAV:
        out.append('<a href="/%s/">%s</a>' % (slugify(s), esc(s)))
    out.append('<a href="/leagues/">All Leagues</a>')
    out.append('<a href="/favourites/">&#9733; Favourites</a></div>')
    groups, order = {}, []
    for row in LEAGUES:
        label = row[4]
        if label not in groups:
            groups[label] = []
            order.append(label)
        groups[label].append((row[3], row[2]))
    out.append('<div class="sidebox scroll"><h3>Competitions</h3>')
    for label in order:
        out.append('<h4>%s</h4>' % esc(label))
        for slug, name in groups[label]:
            out.append('<a href="%s">%s</a>' % (league_path_for(label, slug), esc(name)))
    out.append('</div>')
    return "".join(out)


def _mini_row(m):
    hs, aws = m["home"].get("score"), m["away"].get("score")
    sc = ("%s - %s" % (esc(hs), esc(aws))) if hs is not None else esc(fmt_time(m["date"]))
    if m["state"] == "in":
        tag = '<span class="ml">LIVE</span>'
    else:
        tag = '<span class="ml mut">%s</span>' % esc(status_label(m))
    return ('<a class="mini" href="%s">%s<span class="mt">%s %s v %s %s</span>'
            '<span class="ms">%s</span></a>'
            % (match_path(m), tag,
               team_img(m["home"], 16),
               esc(m["home"]["short"] or m["home"]["name"]),
               team_img(m["away"], 16),
               esc(m["away"]["short"] or m["away"]["name"]), sc))


def _rail_html(live, upcoming=None):
    """Right rail: live-now mini list + popular leagues + about box."""
    out = ['<div class="sidebox"><h3>&#9679; Live Now</h3>']
    if live:
        out += [_mini_row(m) for m in live[:8]]
    else:
        out.append('<p class="meta">No live matches right now.</p>')
    out.append('</div>')
    tops = sorted(LEAGUES, key=lambda r: _league_rank(r[3]))[:7]
    out.append('<div class="sidebox"><h3>Popular Leagues</h3>')
    for row in tops:
        out.append('<a href="%s">%s</a>' % (league_path_for(row[4], row[3]), esc(row[2])))
    out.append('</div>')
    out.append('<div class="sidebox"><h3>ScoreBuzz</h3>'
               '<p class="meta">Live scores, fixtures, results, lineups and '
               'stats across %d competitions. No betting, just the game.</p></div>'
               % len(LEAGUES))
    return "".join(out)


def _lgroup(slug, name, rows_html, cnt_text=""):
    return ('<section class="lgroup"><div class="lg-head">'
            '<a href="%s">%s</a><span class="lg-cnt">%s</span></div>'
            '%s</section>' % (league_path_for(_SLUG_LABEL.get(slug, "Football"), slug),
                              esc(name), esc(cnt_text), rows_html))


def _datestrip_html():
    t = today_utc()
    chips = ['<a href="#" data-f="all" class="on">All</a>',
             '<a class="live" href="#" data-f="live">&#9679; Live</a>']
    for delta, label in ((-1, "Yesterday"), (0, "Today"), (1, "Tomorrow")):
        d = t + timedelta(days=delta)
        chips.append('<a href="#" data-f="day" data-day="%s"%s>%s</a>'
                     % (d.isoformat(),
                        ' data-today="1"' if delta == 0 else "", label))
    return '<div class="datestrip">%s</div>' % "".join(chips)


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
        body.append('<div class="card" id="live"><span class="badge live">\u25cf LIVE NOW (%d)</span>'
                    % len(live))
        body += [match_row(m) for m in live]
        body.append("</div>")
    else:
        body.append('<div class="card" id="nolive"><span class="badge">No live matches right now</span>'
                    '<p class="meta">Live scores appear here the moment games kick off.</p></div>')
    if sport_leagues:
        body.append('<div class="fhide"><h2>Leagues</h2><div class="card">')
        for lg in sport_leagues:
            body.append('<p><a href="%s">%s</a> '
                        '<span class="meta">\u2014 fixtures, results, standings</span></p>'
                        % (league_path(lg), esc(lg["name"])))
        body.append("</div></div>")
    if upcoming:
        body.append('<section class="fsec"><h2>Upcoming Fixtures</h2>')
        lgmap = {l["slug"]: l["name"] for l in sport_leagues}
        for d, by_league in _group_by_day(upcoming):
            day_ms = sorted(
                (mm for ms in by_league.values() for mm in ms),
                key=lambda m: m["date"] or datetime.max.replace(tzinfo=timezone.utc))
            body.append('<div class="dayblock" data-day="%s">'
                        '<h3 style="font-size:14px;margin:10px 0 4px">%s</h3>'
                        % (d.isoformat() if d else "tbc", esc(_day_label(d, today))))
            body.append('<div class="card">')
            body += [match_row(m, lgmap.get(m["league"]["slug"], ""))
                     for m in day_ms]
            body.append("</div>")
            body.append("</div>")
        body.append("</section>")
    if recent:
        body.append('<section class="fsec"><h2>Recent Results</h2>')
        lgmap = {l["slug"]: l["name"] for l in sport_leagues}
        day_groups = _group_by_day(recent)
        ordered_days = [g for g in reversed(day_groups) if g[0] is not None] + \
                       [g for g in day_groups if g[0] is None]
        for d, by_league in ordered_days:
            day_ms = sorted(
                (mm for ms in by_league.values() for mm in ms),
                key=lambda m: m["date"] or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True)
            body.append('<div class="dayblock" data-day="%s">'
                        '<h3 style="font-size:14px;margin:10px 0 4px">%s</h3>'
                        % (d.isoformat() if d else "tbc", esc(_day_label(d, today))))
            body.append('<div class="card">')
            body += [match_row(m, lgmap.get(m["league"]["slug"], ""))
                     for m in day_ms]
            body.append("</div>")
            body.append("</div>")
        body.append("</section>")
    body.insert(1, _datestrip_html())
    return page("%s Scores, Fixtures & Results | ScoreBuzz" % sport_label,
                "Live %s scores, upcoming fixtures, results, lineups and stats on ScoreBuzz."
                % sport_label.lower(),
                path, crumbs, "\n".join(body),
                layout=(_sidebar_html(), _rail_html(live, upcoming)))


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
    return ("""<div class='card motd' id='motd'><a class='motd-cover' href='%s' aria-label='Open match page'></a><div class='motd-tag'>\u2605 Match of the Day %s</div>
<div class='motd-teams'>
<a href='%s' class='motd-team'>%s<span>%s</span></a>
<div class='motd-mid'>%s</div>
<a href='%s' class='motd-team'>%s<span>%s</span></a>
</div>
<div class='motd-lg'><a href='%s'>%s</a></div></div>"""
            % (match_path(m), badge, match_path(m), team_img(m["home"], 64), esc(m["home"]["name"]),
               mid, match_path(m), team_img(m["away"], 64), esc(m["away"]["name"]),
               league_path(lg), esc(lg_name.get(lg["slug"], lg["name"]))))


_MOTD_CSS = """
.motd{padding:18px;text-align:center;position:relative;transition:box-shadow .15s,border-color .15s}
.motd:hover{border-color:var(--grn);box-shadow:0 3px 16px rgba(14,81,54,.20)}
.motd-cover{position:absolute;inset:0;z-index:1;border-radius:inherit;cursor:pointer}
.motd-lg{position:relative;z-index:2}
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
    body = [trend_html, "<style>%s</style>" % _MOTD_CSS, "<h1>Live Scores &amp; Fixtures</h1>",
            _datestrip_html()]

    motd = _match_of_day(live, upcoming)
    if motd:
        body.append(_motd_html(motd, lg_name))

    if live:
        body.append('<div class="card" id="live"><span class="badge live">\u25cf LIVE NOW (%d)</span>'
                    % len(live))
        body += [match_row(m) for m in live]
        body.append("</div>")
    else:
        body.append('<div class="card" id="nolive"><span class="badge">No live matches right now</span>'
                    '<p class="meta">Live scores appear here the moment games kick off. '
                    "Browse upcoming fixtures below.</p></div>")

    def by_sport(matches):
        out = {}
        for m in matches:
            out.setdefault(_sport_of(m), []).append(m)
        return out

    if upcoming:
        body.append('<section class="fsec"><h2>Upcoming Fixtures</h2>')
        up_sorted = sorted(
            upcoming,
            key=lambda m: m["date"] or datetime.max.replace(tzinfo=timezone.utc))
        for d, by_league in _group_by_day(up_sorted):
            day_ms = sorted(
                (mm for ms in by_league.values() for mm in ms),
                key=lambda m: m["date"] or datetime.max.replace(tzinfo=timezone.utc))
            body.append('<div class="dayblock" data-day="%s">'
                        '<h3 style="font-size:13px;margin:8px 0 4px;color:var(--mut)">%s</h3>'
                        % (d.isoformat() if d else "tbc", esc(_day_label(d, today))))
            body.append('<div class="card">')
            body += [match_row(m, lg_name.get(m["league"]["slug"], ""))
                     for m in day_ms]
            body.append("</div>")
            body.append("</div>")
        body.append("</section>")
    if recent:
        body.append('<section class="fsec"><h2>Recent Results</h2>')
        rec_sorted = sorted(
            recent,
            key=lambda m: m["date"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True)
        day_groups = _group_by_day(rec_sorted)
        ordered_days = [g for g in reversed(day_groups) if g[0] is not None] + \
                       [g for g in day_groups if g[0] is None]
        for d, by_league in ordered_days:
            day_ms = sorted(
                (mm for ms in by_league.values() for mm in ms),
                key=lambda m: m["date"] or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True)
            body.append('<div class="dayblock" data-day="%s">'
                        '<h3 style="font-size:13px;margin:8px 0 4px;color:var(--mut)">%s</h3>'
                        % (d.isoformat() if d else "tbc", esc(_day_label(d, today))))
            body.append('<div class="card">')
            body += [match_row(m, lg_name.get(m["league"]["slug"], ""))
                     for m in day_ms]
            body.append("</div>")
            body.append("</div>")
        body.append("</section>")
    body.append('<div class="fhide"><h2>Leagues</h2><div class="card">')
    for lg in leagues:
        body.append('<p><a href="%s">%s</a> '
                    '<span class="meta">\u2014 fixtures, results, standings</span></p>'
                    % (league_path(lg), esc(lg["name"])))
    body.append("</div></div>")
    return page("Live Football Scores, Fixtures & Results | ScoreBuzz",
                "ScoreBuzz: live scores, upcoming fixtures, results, lineups, "
                "stats and standings across football, NFL, NBA, NHL, MLB, tennis and MMA.",
                "/", [("Home", "/")], "\n".join(body),
                layout=(_sidebar_html(), _rail_html(live, upcoming)))


def build_league(league, events, standings, trend_html=""):
    upcoming = sorted([m for m in events if m["state"] == "pre"],
                      key=lambda m: m["date"] or datetime.max.replace(tzinfo=timezone.utc))
    live = [m for m in events if m["state"] == "in"]
    finished = [m for m in events if m["state"] == "post"]
    body = [trend_html,
            "<h1>%s Fixtures, Results &amp; Standings "
            '<button class="fav-star" data-fkey="league:%s" '
            'data-fname="%s" aria-label="Save to favourites">&#9734;</button></h1>'
            % (esc(league["name"]), league_path(league), esc(league["name"]))]
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
    path = league_path(league)
    crumbs = [("Home", "/"), (league["name"], path)]
    return page("%s Fixtures, Results, Live Scores & Standings | ScoreBuzz"
                % league["name"],
                "All %s fixtures, live scores, results and standings on ScoreBuzz."
                % league["name"], path, crumbs, "\n".join(body),
                layout=(_sidebar_html(), _rail_html(live, upcoming)))


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
(function poll(){fetch('/api/'+meta.sport+'/'+meta.league+'/summary?event='+meta.event)
.then(function(x){return x.json();}).then(function(d){window.sbzUpdate(d);}).catch(function(){});
setTimeout(poll,15000);})();}
})();
</script>""".replace("%%PLAYS%%", data_json).replace("%%META%%", meta_json)
    return ("""<h2 id="sbzWatch">Live Game View</h2><div class="card">
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
if(meta.state==='in'||meta.state==='pre'){
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
(d.commentary||[]).forEach(function(c){if(c&&c.text){var t=c.time||{};
push('',(t.displayValue||c.clock||''),c.text,false,null,null);}});
}catch(e){}
if(added)render();};
(function poll(){fetch('/api/'+meta.sport+'/'+meta.league+'/summary?event='+meta.event)
.then(function(x){return x.json();}).then(function(d){window.sbzCastUpdate(d);}).catch(function(){});
setTimeout(poll,15000);})();}
})();
</script>""".replace("%%CAST%%", data_json).replace("%%META%%", meta_json)
    return ("""<h2 id="sbzWatch">Live Gamecast</h2><div class="card">
<div class="sbz-chead"><span id="sbzCScore">%s vs %s</span><span class="ck" id="sbzCClock"></span></div>
<div id="sbzCPlays"></div>
</div>
%s""" % (esc(hab), esc(aab), js))


def _surname(name):
    parts = (name or "").split()
    return parts[-1] if parts else (name or "")


def _scorer_name(txt, team_name):
    low = (txt or "").lower()
    i = low.find("own goal by ")
    if i >= 0:
        rest = txt[i + len("own goal by "):]
        return _surname(re.split(r"[,.(]", rest)[0].strip())
    # ESPN goal text: "Goal! Home 1, Away 0. <Scorer> (<Team>) <shot>..."
    # (the parenthesised team is often a short form, e.g. "(Viking)" for
    # "Viking FK", so match the name pattern, not the exact team name)
    mm = re.search(r"\.\s+([A-Z][\wÀ-ÿ'’.-]+(?: [A-Z][\wÀ-ÿ'’.-]+){0,2}) \(",
                   txt or "")
    if mm:
        return _surname(mm.group(1))
    if team_name:
        mm = re.search(r"([A-Z][\wÀ-ÿ'’.-]+(?: [A-Z][\wÀ-ÿ'’.-]+){0,2}) \("""
                       + re.escape(team_name) + r"\)", txt or "")
        if mm:
            return _surname(mm.group(1))
    return ""


def _scorers_html(m, summ):
    """FotMob-style goalscorer lines under the hero score (soccer only).
    Built only from real key events in the summary — never invented."""
    if not summ or (m["league"].get("sport") != "soccer"):
        return ""
    home_id, away_id = str(m["home"].get("id")), str(m["away"].get("id"))
    per = {"home": {}, "away": {}}
    order = {"home": [], "away": []}
    for c in summ.get("commentary") or []:
        if _event_kind(c) != "goal":
            continue
        minute = _event_minute(c)
        if minute is None:
            continue
        tid = c.get("team_id") or ""
        side = "home" if tid == home_id else ("away" if tid == away_id else None)
        if not side:
            continue
        name = _scorer_name(c.get("text") or "", c.get("team_name") or "")
        if not name:
            continue
        txt = (c.get("text") or "").lower()
        tag = ""
        if "own goal" in txt:
            tag = " (OG)"
        elif "penalt" in (c.get("type") or "").lower() or "penalty" in txt:
            tag = " (Pen)"
        mtxt = (c.get("clock") or "").strip() or ("%d'" % int(round(minute)))
        if name not in per[side]:
            per[side][name] = []
            order[side].append(name)
        per[side][name].append((minute, mtxt + tag))
    if not order["home"] and not order["away"]:
        return ""
    def fmt(side):
        lines = []
        for name in order[side]:
            mins = ", ".join(t for _, t in sorted(per[side][name]))
            lines.append("%s <b>%s</b>" % (esc(name), esc(mins)))
        return "<br>".join(lines)
    return ("<div class='scorers'><div class='sc-side sc-home'>%s</div>"
            "<div class='sc-ball'>\u26bd</div>"
            "<div class='sc-side sc-away'>%s</div></div>"
            % (fmt("home"), fmt("away")))


# SportScore live tracker embeds, keyed by ESPN event id -> SportScore fixture
# slug. Free-tier terms: the "Powered by SportScore" attribution must stay
# visible on pages that carry the tracker.
TRACKER_SPOTS = {
    "401892156": "perth-glory-vs-brisbane-roar",  # Brisbane Roar v Perth Glory, A-League, 2026-10-23
}


def tracker_widget_html(m):
    slug = TRACKER_SPOTS.get(str(m.get("id")))
    if not slug:
        return ""
    return ("<h2>Live Match Tracker</h2><div class=\"card\" style=\"padding:8px\">"
            "<iframe src=\"https://sportscore.com/embed/tracker/football/" + slug + "/?theme=light\" "
            "style=\"width:100%;height:430px;border:0;border-radius:8px;display:block\" "
            "loading=\"lazy\" title=\"Live match tracker\"></iframe>"
            "<div style=\"text-align:right;font-size:12px;color:#5b6b7a;padding-top:6px\">"
            "Powered by <a href=\"https://sportscore.com/\" rel=\"dofollow\">SportScore</a>"
            "</div></div>")


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

    badge = ('<span class="badge live js-st">● %s</span>'
             % esc(status_label(m))) if m["state"] == "in" else \
            ('<span class="badge js-st">%s</span>' % esc(status_label(m)))
    when = ""
    if m["date"]:
        when = "%s, %s" % (fmt_date(m["date"]), fmt_time(m["date"]))
    venue_line = ""
    if m["venue"]:
        venue_line = m["venue"] + (" \u2014 " + m["city"] if m["city"] else "")
    elif m["city"]:
        venue_line = m["city"]
    meta_line = " \u00b7 ".join(b for b in (when, venue_line) if b)
    big_html = ('<div class=\'big js-big\'>%s %s %s</div>'
                % (esc(hs if hs is not None else ""),
                   '<span class="vs">-</span>' if hs is not None else '<span class="vs">vs</span>',
                   esc(aws if aws is not None else "")))
    action = ""
    mid_inner = big_html
    if m["state"] == "pre":
        action = "<a class='wbtn js-wbtn' href='#sbzWatch'>&#9654; Watch</a>"
        if m["date"]:
            rem = max(0, int((m["date"] - datetime.now(timezone.utc)).total_seconds()))
            hh, rem = divmod(rem, 3600)
            mi, ss = divmod(rem, 60)
            def _fl(val, unit, label):
                return ("<div class='cd-u'><div class='fl' data-u='%s'>"
                        "<div class='fl-h fl-t'><i>%02d</i></div>"
                        "<div class='fl-h fl-b'><i>%02d</i></div>"
                        "<div class='fl-leaf'><div class='fl-f'><i>%02d</i></div></div>"
                        "</div><span class='cd-l'>%s</span></div>"
                        % (unit, val, val, val, label))
            mid_inner = (
                "<div class='cd-cap'>Starts in</div>"
                "<div class='cd js-cd' data-kickoff='%s'>"
                % m["date"].strftime("%Y-%m-%dT%H:%M:%SZ"))
            mid_inner += _fl(hh, "h", "HRS")
            mid_inner += "<span class='cd-sep'>:</span>"
            mid_inner += _fl(mi, "m", "MIN")
            mid_inner += "<span class='cd-sep'>:</span>"
            mid_inner += _fl(ss, "s", "SEC")
            mid_inner += ("</div>"
                          "<div class='big js-big' style='display:none'></div>")
    elif m["state"] == "in":
        action = ("<a class='wbtn live js-wbtn' href='#sbzWatch'>"
                  "<span class='pulse'></span>LIVE</a>")
    hero = (
        "<div class='card scoreboard-hero'%s>"
        "<div class='hero-comp'><a href='%s'>%s</a></div>"
        "<div class='hero-meta'>%s</div>"
        "<div class='hero-row'>"
        "<a class='hero-team ht-home' href='/team/%s/'><span class='nm'>%s</span>%s</a>"
        "<div class='hero-mid'>%s"
        "<div class='hero-status'>%s</div>%s</div>"
        "<a class='hero-team ht-away' href='/team/%s/'>%s<span class='nm'>%s</span></a>"
        "</div>%s</div>"
        % (_live_attrs(m),
           league_path(lg), esc(lg["name"]),
           esc(meta_line),
           esc(team_slug(m["home"])), esc(m["home"]["name"]), team_img(m["home"], 46),
           mid_inner,
           badge, action,
           esc(team_slug(m["away"])), team_img(m["away"], 46), esc(m["away"]["name"]),
           _scorers_html(m, summ)))
    parts = [hero]
    tw = tracker_widget_html(m)
    if tw:
        parts.append(tw)
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
    if not widget_done and m["state"] in ("in", "pre"):
        parts.append(cast_widget_html(m, []))
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

    lg_path = league_path(lg)
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


def _cricket_hooks(cm):
    """data-* hooks for the browser live updater, when this match has an
    ESPN identity (mapped series): the browser polls ESPN's series
    scoreboard directly and rewrites the score in place."""
    eid = cm.get("espn_event_id")
    sid = cm.get("espn_series_id")
    if not eid or not sid:
        return ""
    state = {"live": "in", "upcoming": "pre", "recent": "post"}.get(cm["state"], "pre")
    return (' data-eid="%s" data-espn="cricket/%s" data-state="%s" data-cricket="1"'
            % (esc(eid), esc(sid), state))


def cricket_match_row(cm):
    url = cricket_url(cm)
    if cm["state"] == "live":
        st = '<span class="st js-st live">● LIVE</span>'
    elif cm["state"] == "upcoming":
        dt = (fmt_date(cm["date"]) + " " + fmt_time(cm["date"])
              if cm["date"] else "Scheduled")
        st = '<span class="st js-st">%s</span>' % esc(dt)
    else:
        st = '<span class="st js-st">%s</span>' % esc(cm["status"] or "Result")

    def logo(tname):
        img = (cm.get("team_imgs") or {}).get((tname or "").lower())
        if img:
            return ('<img src="%s" alt="%s" width="20" height="20" loading="lazy">'
                    % (esc(proxy_img(img)), esc(tname)))
        return ('<span class="tlg" style="width:20px;height:20px;font-size:9px">%s</span>'
                % esc(_initials(tname)))

    teams_html = ('<div class="tn">%s%s</div><div class="tn">%s%s</div>'
                  % (logo(cm["team1"]), esc(cm["team1"] or "?"),
                     logo(cm["team2"]), esc(cm["team2"] or "?")))
    sc = ('<div class="sc js-cs" style="font-weight:400;font-size:13px">%s</div>'
          % esc(cm["score_text"])) if cm["score_text"] else \
        '<div class="sc js-cs meta">%s</div>' % esc(cm["match_type"] or "")
    star = ('<button class="fav-star" data-fkey="cmatch:%s" data-fname="%s" '
            'data-fdate="%s" aria-label="Save to favourites">&#9734;</button>'
            % (esc(url), esc(cm["name"]),
               esc(cm["date"].strftime("%Y-%m-%d") if cm["date"] else "")))
    return ('<div class="mrow"%s>%s<a href="%s" style="flex:1;display:flex;'
            'align-items:center;gap:10px;min-width:0;color:inherit">'
            '<div class="teams">%s</div>%s</a>%s</div>'
            % (_cricket_hooks(cm), st, url, teams_html, sc, star))


def build_cricket_index(matches, series):
    live = [m for m in matches if m["state"] == "live"]
    upcoming = [m for m in matches if m["state"] == "upcoming"][:30]
    recent = [m for m in matches if m["state"] == "recent"][:30]
    body = ["<h1>Cricket Live Scores &amp; Fixtures</h1>",
            '<p class="meta"><a href="/cricket/schedule/">Full schedule '
            '&amp; fixtures &rarr;</a></p>']
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


def build_cricket_series(s, matches, store=None):
    body = ["<h1>%s — Fixtures &amp; Results</h1>" % esc(s["name"])]
    live = [m for m in matches if m["state"] == "live"]
    other = [m for m in matches if m["state"] != "live"]
    if live:
        body.append('<div class="card"><span class="badge live">● LIVE</span>')
        body += [cricket_match_row(m) for m in live]
        body.append("</div>")
    if store:
        entries = [e for e in store.values() if e.get("series") == s["slug"]]
        pt = _points_table_html(entries)
        if pt:
            body.append(pt)
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


def _num(x):
    try:
        return float(x)
    except Exception:
        return None


def _overs_to_balls(o):
    """'15.3' overs -> 93 balls; '15' -> 90; None -> None."""
    if o is None:
        return None
    s = str(o).strip()
    if not s:
        return None
    try:
        if "." in s:
            whole, part = s.split(".", 1)
            return int(whole) * 6 + int(part[:1])
        return int(float(s)) * 6
    except Exception:
        return None


def _alloc_balls(mtype):
    """Total balls in a limited-overs innings, from the format name."""
    t = (mtype or "").upper()
    if "T10" in t:
        return 60
    if "T20" in t:
        return 120
    if "ODI" in t or "ONE DAY" in t or "LIST A" in t:
        return 300
    return None


def _cricket_live_panel(cm):
    """Cricbuzz-style live panel: CRR/RRR, pair at the crease, this over."""
    sc = cm.get("scorecard") or {}
    inns = sc.get("innings") or []
    if not inns:
        return ""
    cur = inns[-1]
    total = _num(cur.get("total"))
    balls_bowled = _overs_to_balls(cur.get("overs"))
    rows = []
    if total is not None and balls_bowled:
        rows.append("<b>CRR</b> %.2f" % (total / (balls_bowled / 6.0)))
    alloc = _alloc_balls(cm.get("match_type"))
    if len(inns) >= 2 and alloc and total is not None and balls_bowled is not None:
        first_total = _num(inns[0].get("total"))
        if first_total is not None:
            need = first_total + 1 - total
            left = alloc - balls_bowled
            if need > 0 and left > 0:
                rows.append("<b>RRR</b> %.2f" % (need / (left / 6.0)))
                rows.append("<b>Target %d</b> — need %d off %d balls"
                            % (int(first_total + 1), int(need), left))
    pair = [p for p in (cur.get("batting") or [])
            if (p.get("dismissal") or "").strip().lower()
            in ("", "not out", "batting")]
    pair_html = ""
    if pair:
        cells = []
        for p in pair[-2:]:
            nm = (p.get("name") or "").rstrip()
            star = "*" if nm.endswith("*") else ""
            nm = nm.rstrip("*").strip()
            cells.append("%s%s %s (%s)" % (
                esc(nm), star,
                esc(p.get("r") if p.get("r") is not None else 0),
                esc(p.get("b") if p.get("b") is not None else 0)))
        pair_html = ("<p class='meta'><b>At the crease:</b> %s</p>"
                     % " &nbsp;·&nbsp; ".join(cells))
    over_html = ""
    balls = sc.get("balls") or []
    if balls:
        groups, order = {}, []
        for i, b in enumerate(balls):
            o = _ball_over(b, i)
            if o not in groups:
                groups[o] = []
                order.append(o)
            groups[o].append(b)
        if order:
            o = max(order)
            chips = []
            for b in groups[o][:12]:
                label, cls = _ball_chip(b)
                chips.append('<span class="bl %s">%s</span>' % (cls, esc(label)))
            over_html = ("<p class='meta'><b>This over:</b></p>"
                         "<div class='ovr-balls'>%s</div>" % "".join(chips))
    if not (rows or pair_html or over_html):
        return ""
    return ("<div class='card'><span class='badge live'>● LIVE</span>"
            "<p class='meta'>%s</p>%s%s</div>"
            % (" &nbsp;·&nbsp; ".join(rows), pair_html, over_html))


def commentary_html(balls):
    """Ball-by-ball text feed, newest first. Empty when feed has no text."""
    items = []
    for i, b in enumerate(balls or []):
        if not isinstance(b, dict):
            continue
        txt = str(b.get("text") or b.get("commentary") or "").strip()
        if txt:
            items.append((_ball_over(b, i), txt))
    if not items:
        return ""
    lis = "".join("<li><b>Over %s</b> — %s</li>" % (o + 1, esc(t))
                  for o, t in reversed(items[-60:]))
    return "<h2>Ball-by-Ball</h2><div class='card'><ul class='comm'>%s</ul></div>" % lis


def _points_table_html(entries):
    """League standings computed from tracked results (results store).
    Win = 2 pts, tie/no-result/draw = 1. NRR shown only when every
    counted match has complete innings data."""
    if not entries:
        return ""
    tbl = {}

    def T(n):
        return tbl.setdefault(n, {"p": 0, "w": 0, "l": 0, "nr": 0, "pts": 0,
                                    "rf": 0.0, "fb": 0, "ra": 0.0, "ab": 0,
                                    "nm": 0})
    for e in entries:
        t1, t2 = (e.get("t1") or "").strip(), (e.get("t2") or "").strip()
        if not t1 or not t2:
            continue
        a, b = T(t1), T(t2)
        a["p"] += 1
        b["p"] += 1
        status = e.get("status") or ""
        st = status.lower()
        winner = None
        if " won by" in st:
            wname = status.split(" won by")[0].strip().lower()
            if wname and wname in t1.lower():
                winner = "a"
            elif wname and wname in t2.lower():
                winner = "b"
        if winner == "a":
            a["w"] += 1
            a["pts"] += 2
            b["l"] += 1
        elif winner == "b":
            b["w"] += 1
            b["pts"] += 2
            a["l"] += 1
        else:
            a["nr"] += 1
            b["nr"] += 1
            a["pts"] += 1
            b["pts"] += 1
        alloc = _alloc_balls(e.get("type"))
        inns = e.get("innings") or []
        if alloc and len(inns) >= 2:
            per, ok = {}, True
            for inn in inns[:2]:
                nm = (inn.get("name") or "").lower()
                tot = _num(inn.get("total"))
                fb = _overs_to_balls(inn.get("overs"))
                wk = _num(inn.get("wickets"))
                if tot is None or fb is None:
                    ok = False
                    break
                if wk is not None and wk >= 10:
                    fb = alloc  # all out = full allocation for NRR
                if t1.lower() in nm:
                    per["a"] = (tot, fb)
                elif t2.lower() in nm:
                    per["b"] = (tot, fb)
            if ok and "a" in per and "b" in per:
                a["rf"] += per["a"][0]
                a["fb"] += per["a"][1]
                a["ra"] += per["b"][0]
                a["ab"] += per["b"][1]
                b["rf"] += per["b"][0]
                b["fb"] += per["b"][1]
                b["ra"] += per["a"][0]
                b["ab"] += per["a"][1]
                a["nm"] += 1
                b["nm"] += 1
    if not tbl:
        return ""
    show_nrr = all(v["p"] and v["nm"] == v["p"] for v in tbl.values())

    def nrr(v):
        if not v["fb"] or not v["ab"]:
            return 0.0
        return v["rf"] / (v["fb"] / 6.0) - v["ra"] / (v["ab"] / 6.0)

    rows = sorted(tbl.items(),
                  key=lambda kv: (-kv[1]["pts"],
                                  -nrr(kv[1]) if show_nrr else 0.0, kv[0]))
    out = ["<h2>Points Table</h2><div class='card'>"
           "<div style='overflow-x:auto'><table><tr><th>#</th><th>Team</th>"
           "<th class='num'>P</th><th class='num'>W</th><th class='num'>L</th>"
           "<th class='num'>T/NR</th><th class='num'>Pts</th>"]
    if show_nrr:
        out.append("<th class='num'>NRR</th>")
    out.append("</tr>")
    for i, (name, v) in enumerate(rows, 1):
        out.append("<tr><td>%d</td><td>%s</td><td class='num'>%d</td>"
                   "<td class='num'>%d</td><td class='num'>%d</td>"
                   "<td class='num'>%d</td><td class='num'><b>%d</b></td>%s</tr>"
                   % (i, esc(name), v["p"], v["w"], v["l"], v["nr"], v["pts"],
                      "<td class='num'>%+.3f</td>" % nrr(v) if show_nrr else ""))
    out.append("</table></div><p class='meta'>Standings from results "
               "tracked on ScoreBuzz.</p></div>")
    return "".join(out)


_TABS_STYLE = ("<style>.tabbar{display:flex;gap:6px;margin:14px 0 6px;flex-wrap:wrap}"
               ".tabbtn{border:1px solid #d5dae3;background:#fff;border-radius:999px;"
               "padding:6px 14px;font-size:13px;font-weight:600;cursor:pointer;color:#333}"
               ".tabbtn.on{background:#1447c8;color:#fff;border-color:#1447c8}"
               ".comm{margin:0;padding-left:18px}.comm li{margin:5px 0;font-size:14px}</style>")


def _tabs_html(sections):
    """(id, label, html) sections behind a tab bar. Without JS every
    section stays visible, stacked — the tabs only enhance."""
    btns, secs = [], []
    for i, (tid, label, html) in enumerate(sections):
        btns.append('<button class="tabbtn%s" data-tab="%s">%s</button>'
                    % (" on" if i == 0 else "", tid, esc(label)))
        secs.append('<div class="tabsec" id="tab-%s">%s</div>' % (tid, html))
    js = ("<script>(function(){var bs=document.querySelectorAll('.tabbtn');"
          "function act(b){bs.forEach(function(x){x.classList.toggle('on',x===b)});"
          "document.querySelectorAll('.tabsec').forEach(function(s){"
          "s.style.display=s.id==='tab-'+b.getAttribute('data-tab')?'':'none';});}"
          "bs.forEach(function(b){b.addEventListener('click',function(){act(b)});});"
          "if(bs.length)act(bs[0]);})();</script>")
    return (_TABS_STYLE + '<div class="tabbar">' + "".join(btns) + "</div>"
            + "".join(secs) + js)


def build_cricket_schedule(matches):
    up = [m for m in matches if m["state"] == "upcoming"]
    up.sort(key=lambda m: m["date"] or datetime.max.replace(tzinfo=timezone.utc))
    body = ["<h1>Cricket Schedule &amp; Fixtures</h1>"]
    if not up:
        body.append('<div class="card"><p class="meta">No upcoming fixtures '
                    'right now — new fixtures appear here automatically.</p></div>')
    cur = None
    for m in up[:80]:
        d = fmt_date(m["date"]) if m["date"] else "Date TBC"
        if d != cur:
            if cur is not None:
                body.append("</div>")
            body.append("<h2>%s</h2><div class='card'>" % esc(d))
            cur = d
        body.append(cricket_match_row(m))
    if cur is not None:
        body.append("</div>")
    return page("Cricket Schedule & Fixtures | ScoreBuzz",
                "Upcoming cricket fixtures across all series, with venues "
                "and start times, on ScoreBuzz.",
                "/cricket/schedule/",
                [("Home", "/"), ("Cricket", "/cricket/"),
                 ("Schedule", "/cricket/schedule/")],
                "\n".join(body))


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
                esc(proxy_img(img)), esc(tname), size, size)
        return ('<span class="tlg" style="width:%dpx;height:%dpx;font-size:%dpx">%s</span>'
                % (size, size, max(10, int(size * 0.4)), esc(_initials(tname))))

    badge = ('<span class="badge js-st live">● LIVE</span>' if state == "live"
             else '<span class="badge js-st">%s</span>' % esc(cm["status"] or state.title()))
    hero = ("""<div class='card scoreboard-hero'%s>%s
<div class='nm'>%s %s</div><div class='big js-cs' style='font-size:22px'>%s</div>
<div class='nm'>%s %s</div>
<p class='meta'>%s%s%s &middot; %s</p></div>"""
            % (_cricket_hooks(cm), badge, logo(cm["team1"]), esc(cm["team1"]),
               esc(cm["score_text"] or "vs"),
               logo(cm["team2"]), esc(cm["team2"]),
               esc(cm["venue"]), " — " if cm["venue"] and cm["date"] else "",
               esc(cm["date"].strftime("%a %d %b %Y %H:%M UTC") if cm["date"] else ""),
               esc(cm["series_name"])))
    parts = [hero]
    sc = cm.get("scorecard") or {}
    if state == "live":
        panel = _cricket_live_panel(cm)
        if panel:
            parts.append(panel)

    # --- Scorecard tab ---
    sc_parts = []
    for inn in sc.get("innings") or []:
        head = inn.get("name") or "Innings"
        if inn.get("total") is not None:
            head += " — %s/%s" % (inn["total"], inn.get("wickets") if inn.get("wickets") is not None else 0)
            if inn.get("overs"):
                head += " (%s ov)" % inn["overs"]
        sc_parts.append('<div class="inn-head">%s</div>' % esc(head))
        if inn.get("batting"):
            sc_parts.append("<h2>Batting</h2><div class='card'>%s</div>"
                            % _cricket_batting_table(inn["batting"]))
            if inn.get("extras"):
                sc_parts.append("<p class='meta'><b>Extras:</b> %s</p>"
                                % esc(inn["extras"]))
        if inn.get("bowling"):
            sc_parts.append("<h2>Bowling</h2><div class='card'>%s</div>"
                            % _cricket_bowling_table(inn["bowling"]))
        if inn.get("fow"):
            sc_parts.append("<h2>Fall of Wickets</h2><div class='card'><p class='meta'>%s</p></div>"
                            % esc("; ".join(inn["fow"])))
    if not sc_parts:
        if state == "live":
            note = "Live scorecard is loading — refreshes automatically."
        elif state == "upcoming":
            note = "The scorecard will appear here once play begins."
        else:
            note = "Scorecard not available for this match."
        sc_parts.append('<div class="card"><p class="meta">%s</p></div>' % note)

    # --- Balls tab ---
    balls = sc.get("balls") or []
    ball_parts = []
    ov = overs_html(balls)
    if ov:
        ball_parts.append(ov)
    comm = commentary_html(balls)
    if comm:
        ball_parts.append(comm)
    if not ball_parts:
        ball_parts.append('<div class="card"><p class="meta">Ball-by-ball data '
                          'is not available for this match.</p></div>')

    # --- Info tab ---
    info = []
    if sc.get("toss"):
        info.append("<b>Toss:</b> " + esc(sc["toss"]))
    if cm.get("venue"):
        info.append("<b>Venue:</b> " + esc(cm["venue"]))
    if sc.get("umpires"):
        info.append("<b>Umpires:</b> " + esc(sc["umpires"]))
    if cm.get("date"):
        info.append("<b>Date:</b> " + esc(cm["date"].strftime("%a %d %b %Y %H:%M UTC")))
    if cm.get("match_type"):
        info.append("<b>Format:</b> " + esc(cm["match_type"]))
    info.append("<b>Series:</b> <a href='/cricket/%s/'>%s</a>"
                % (cm["series_slug"], esc(cm["series_name"])))
    info_html = ("<div class='card'><p class='meta'>%s</p></div>"
                 % " &nbsp;·&nbsp; ".join(info))
    squads = sc.get("squads") or {}
    if squads:
        sq = ["<h2>Squads</h2><div class='card'>"]
        for team, names in squads.items():
            sq.append("<p class='meta'><b>%s:</b> %s</p>"
                      % (esc(team), esc(", ".join(names))))
        sq.append("</div>")
        info_html += "".join(sq)

    parts.append(_tabs_html([("sc", "Scorecard", "".join(sc_parts)),
                             ("balls", "Balls", "".join(ball_parts)),
                             ("info", "Info", info_html)]))

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


def reap_zombies(arc, leagues_by_slug, now):
    """Heal archive records stuck in state 'in' long after kickoff.

    A record can miss its finishing scoreboard update (window edges,
    feed gaps, suspended games). Verify each stale-live record once per
    build against the summary endpoint, whose header carries the true
    status, and flip it to its final state. Capped per build; records
    that are genuinely still in play are left alone and re-checked on
    the next build.
    """
    import archive as _arc_mod
    checked = flipped = 0
    for lg_slug, matches in arc.items():
        lg = leagues_by_slug.get(lg_slug)
        if not lg:
            continue
        for mid, rec in matches.items():
            if checked >= 40:
                return flipped
            m = rec.get("m") or {}
            if m.get("state") != "in":
                continue
            d = m.get("date")
            if not isinstance(d, datetime) or now - d < timedelta(hours=5):
                continue
            url = ("https://site.api.espn.com/apis/site/v2/sports/%s/%s/"
                   "summary?event=%s" % (lg["sport"], lg["code"], mid))
            data = fetch_json(url)
            checked += 1
            if not data:
                continue
            comps = (data.get("header") or {}).get("competitions") or []
            if not comps:
                continue
            comp = comps[0]
            st = (comp.get("status") or {}).get("type") or {}
            if st.get("state") != "post":
                continue
            m["state"] = "post"
            m["status_text"] = (st.get("shortDetail") or st.get("detail")
                                or "FT")
            m["clock"] = ""
            by_side = {}
            for c in comp.get("competitors") or []:
                if c.get("homeAway"):
                    by_side[c["homeAway"]] = c
            for side, key in (("home", "home"), ("away", "away")):
                c = by_side.get(side)
                tgt = m.get(key)
                if c is not None and isinstance(tgt, dict):
                    if c.get("score") is not None:
                        tgt["score"] = c.get("score")
                    tgt["winner"] = bool(c.get("winner"))
            rec["m"] = m
            rec["hash"] = _arc_mod.record_hash(m, rec.get("s"))
            flipped += 1
    if flipped:
        log("zombie reaper: flipped %d stuck-live matches to FT (checked %d)"
            % (flipped, checked))
    return flipped


SITEMAP_MATCH_CHUNK = 40000  # max match URLs per sitemap-matches-N.xml


def main():
    start = time.time()
    log("ScoreBuzz generator starting (%d leagues)" % len(LEAGUES))
    leagues = [{"sport": s, "code": c, "name": n, "slug": sl,
                "sport_label": spl, "logo_sport": ls}
               for s, c, n, sl, spl, ls in LEAGUES]

    all_matches, league_events = [], {}
    teams = {}  # team_id -> {info..., "events": [matches]}

    # 1) scoreboards — the default board PLUS the last two UTC days.
    # ESPN's default board is "today" in US Eastern: a match that leaves
    # that board while still 'in' never receives its final state and the
    # archive keeps it LIVE forever. Re-fetching yesterday and the day
    # before guarantees every started match is seen again after it ends.
    # (The zombie reaper in step 2b is the final backstop.)
    sb_now = datetime.now(timezone.utc)
    sb_past_days = [(sb_now - timedelta(days=n)).strftime("%Y%m%d")
                    for n in (1, 2)]
    for lg in leagues:
        base = ("https://site.api.espn.com/apis/site/v2/sports/%s/%s/scoreboard"
                % (lg["sport"], lg["code"]))
        raw_events = []
        data = fetch_json(base)
        STATS["scoreboards"] += 1
        if data and data.get("events"):
            raw_events.extend(data["events"])
        for _day in sb_past_days:
            ddata = fetch_json(base + "?dates=" + _day)
            STATS["scoreboards"] += 1
            if ddata and ddata.get("events"):
                raw_events.extend(ddata["events"])
        if not raw_events:
            STATS["failed"] += 1
            STATS["failed_leagues"].append(lg["slug"])
            log("FAIL scoreboard: %s" % lg["slug"])
            continue
        events = []
        _seen_ev = set()
        for ev in raw_events:
            if str(ev.get("id")) in _seen_ev:
                continue
            _seen_ev.add(str(ev.get("id")))
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
    reap_zombies(arc, {lg["slug"]: lg for lg in leagues},
                 datetime.now(timezone.utc))
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
        "leagues": ([{"n": lg["name"], "u": league_path(lg),
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
        sp = sport_slug_of(lg["sport_label"])
        if sp != "football":
            legacy = os.path.join(OUT_DIR, "football", lg["slug"])
            if os.path.isdir(legacy):
                shutil.rmtree(legacy, ignore_errors=True)
        urls.append(write_file("%s/%s/index.html" % (sp, lg["slug"]),
                               build_league(lg, evs, standings.get(lg["slug"]),
                                            trend_html)))

    n_match = 0
    n_skipped = 0
    for m in arch_all:
        lg_slug = m["league"]["slug"]
        rec = arc[lg_slug][str(m["id"])]
        h = "%s|v%d" % (rec["hash"], archive_mod.TEMPLATE_VERSION)
        rel = "%s/%s/%s/index.html" % (
            sport_slug_of(m["league"].get("sport_label")), lg_slug, match_slug(m))
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
    urls.append(write_file("cricket/schedule/index.html",
                           build_cricket_schedule(cmatches)))
    cstore = cricket_data.get("results_store") or {}
    series_by_slug = {}
    for s in cseries:
        series_by_slug[s["slug"]] = s
        sm = [m for m in cmatches if m["series_slug"] == s["slug"]]
        urls.append(write_file("cricket/%s/index.html" % s["slug"],
                               build_cricket_series(s, sm, cstore)))
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
    redir = []
    for lg in leagues:
        sp = sport_slug_of(lg["sport_label"])
        if sp != "football":
            redir.append("/football/%s /%s/%s/ 301" % (lg["slug"], sp, lg["slug"]))
            redir.append("/football/%s/* /%s/%s/:splat 301"
                         % (lg["slug"], sp, lg["slug"]))
    write_file("_redirects", "\n".join(redir) + "\n")

    # Pages Functions must live at the project root (repo root in CI),
    # NOT inside the static output dir: wrangler pages deploy reads
    # <cwd>/functions. Writing them into dist/ left the routes dead
    # (SPA fallback served the homepage for /img and /api).
    fn_root = os.path.normpath(os.path.join(HERE, "..", "functions"))
    os.makedirs(os.path.join(fn_root, "api"), exist_ok=True)
    with open(os.path.join(fn_root, "img.js"), "w", encoding="utf-8") as f:
        f.write(IMG_FN_JS)
    with open(os.path.join(fn_root, "api", "[[path]].js"), "w", encoding="utf-8") as f:
        f.write(API_FN_JS)
    shutil.rmtree(os.path.join(OUT_DIR, "functions"), ignore_errors=True)
    write_file("favicon.svg", FAVICON_SVG)
    write_binary("favicon-32.png", base64.b64decode(FAVICON32_B64))
    write_binary("apple-touch-icon.png", base64.b64decode(FAVICON180_B64))
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
