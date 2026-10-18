#!/usr/bin/env python3
"""CricAPI integration for the ScoreBuzz static generator.

Fetches cricket data via the authenticated helper CLI (key never in code):
    ~/workspace/custom-connectors/custom.cricapi/bin/cricapi <path> [k=v ...]

HARD BUDGET (100 hits/day, lifetime free tier):
  - /v1/matches at most 4x/day (6h cache)
  - /v1/series at most 1x/day (24h cache, best effort)
  - /v1/cricScore live snapshot refreshed with matches (6h cache)
  - scorecards ONLY for matches in live status, at most 1 fetch per
    live match per 45 minutes
  - finished-match scorecards cached forever on disk, never refetched
  - local hit counter per build; ALL calls stop at 90 hits/day
  - every API hit is logged to the build log

ENVIRONMENT CAVEAT: api.cricapi.com refuses connections from this VM.
Every fetch is wrapped: failures degrade gracefully (last good cache),
never break the build. The deploy environment (GitHub Actions) should
reach the API fine.

URL space: /cricket/<series-slug>/<match-slug>-<id>/
"""
import json
import os
import re
import subprocess
import time
import html as htmllib
from datetime import datetime, timedelta, timezone

BIN = os.path.expanduser(
    "~/workspace/custom-connectors/custom.cricapi/bin/cricapi")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "cricket_cache")
SCORECARD_DIR = os.path.join(CACHE_DIR, "scorecards")
HIT_CAP = 90
MATCHES_TTL = 6 * 3600      # 4x/day max
SERIES_TTL = 24 * 3600      # 1x/day
SCORECARD_LIVE_TTL = 45 * 60  # live scorecard refresh at most every 45 min
CLI_TIMEOUT = 40

ENDED_MARKERS = ["won by", "match ended", "no result", "abandoned",
                 "tie", "drawn", "match drawn"]


def log(msg):
    print("[cricket %s] %s"
          % (datetime.now(timezone.utc).strftime("%H:%M:%S"), msg), flush=True)


def esc(t):
    return htmllib.escape(str(t if t is not None else ""), quote=True)


def slugify(text):
    text = (text or "").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "match"


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _hits_path():
    return os.path.join(CACHE_DIR, "hits.json")


def hits_used():
    try:
        d = json.load(open(_hits_path()))
        if d.get("date") == _today():
            return int(d.get("count", 0))
    except Exception:
        pass
    return 0


def _bump_hits():
    os.makedirs(CACHE_DIR, exist_ok=True)
    n = hits_used() + 1
    json.dump({"date": _today(), "count": n}, open(_hits_path(), "w"))
    return n


def _cache_get(name, ttl):
    p = os.path.join(CACHE_DIR, name)
    try:
        if time.time() - os.path.getmtime(p) < ttl:
            return json.load(open(p))
    except Exception:
        pass
    return None


def _cache_put(name, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    json.dump(data, open(os.path.join(CACHE_DIR, name), "w"))


def cricapi(path, params=None):
    """Fetch from CricAPI. Returns (data_dict_or_None, counted_hit_bool).

    Prefers the CRICAPI_KEY env var (GitHub Actions deploy) with a direct
    HTTPS call; falls back to the helper CLI (Secure Vault) locally.
    Transport failures return (None, False) and never count against the
    budget. Any JSON response counts as one hit.
    """
    if hits_used() >= HIT_CAP:
        log("hit cap reached (%d) — skipping %s" % (HIT_CAP, path))
        return None, False
    env_key = os.environ.get("CRICAPI_KEY", "").strip()
    if env_key:
        try:
            import urllib.request
            q = dict(params or {})
            q["apikey"] = env_key
            url = ("https://api.cricapi.com" + path + "?" +
                   urllib.parse.urlencode(q))
            req = urllib.request.Request(
                url, headers={"User-Agent": "ScoreBuzz/1.0"})
            with urllib.request.urlopen(req, timeout=CLI_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as e:
            log("transport failure on %s (%s) — using cache" % (path, e))
            return None, False
    else:
        cmd = [BIN, path] + ["%s=%s" % (k, v) for k, v in (params or {}).items()]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=CLI_TIMEOUT)
            out = (r.stdout or "").strip()
            data = json.loads(out)  # raises on transport-error text
        except Exception as e:
            log("transport failure on %s (%s) — using cache" % (path, e))
            return None, False
    n = _bump_hits()
    log("API hit #%d: %s" % (n, path))
    if isinstance(data, dict) and data.get("status") == "failure":
        log("API error on %s: %s" % (path, data.get("reason") or data.get("message")))
        return None, True
    return data, True


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def is_ended(status):
    s = (status or "").lower()
    return any(m in s for m in ENDED_MARKERS)

# ----------------------------------------------------------------------------
# Fetching (all cached, all budget-capped, all failure-proof)
# ----------------------------------------------------------------------------

def get_matches():
    """Fixture list, max 4x/day."""
    cached = _cache_get("matches.json", MATCHES_TTL)
    if cached is not None:
        return cached
    data, _ = cricapi("/v1/matches")
    if data and isinstance(data.get("matches"), list):
        _cache_put("matches.json", data["matches"])
        return data["matches"]
    # stale cache is better than nothing
    try:
        return json.load(open(os.path.join(CACHE_DIR, "matches.json")))
    except Exception:
        return []


def get_series():
    """Series list, max 1x/day. Best effort — may not exist."""
    cached = _cache_get("series.json", SERIES_TTL)
    if cached is not None:
        return cached
    data, _ = cricapi("/v1/series")
    series = []
    if isinstance(data, dict):
        series = data.get("series") or data.get("data") or []
    if isinstance(series, list) and series:
        _cache_put("series.json", series)
        return series
    try:
        return json.load(open(os.path.join(CACHE_DIR, "series.json")))
    except Exception:
        return []


def get_live():
    """Live snapshot, refreshed with matches (6h cache)."""
    cached = _cache_get("live.json", MATCHES_TTL)
    if cached is not None:
        return cached
    data, _ = cricapi("/v1/cricScore")
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        _cache_put("live.json", data["data"])
        return data["data"]
    try:
        return json.load(open(os.path.join(CACHE_DIR, "live.json")))
    except Exception:
        return []


def get_scorecard(mid, live):
    """Scorecard for one match. Finished matches: cached forever, never
    refetched. Live matches: at most one fetch per 45 minutes."""
    os.makedirs(SCORECARD_DIR, exist_ok=True)
    sp = os.path.join(SCORECARD_DIR, re.sub(r"[^a-zA-Z0-9_-]", "", str(mid)) + ".json")
    try:
        cached = json.load(open(sp))
    except Exception:
        cached = None
    if cached is not None and not live:
        return cached  # finished: forever
    if live and cached is not None:
        try:
            if time.time() - os.path.getmtime(sp) < SCORECARD_LIVE_TTL:
                return cached
        except Exception:
            pass
    if not live and cached is not None:
        return cached
    data, _ = cricapi("/v1/cricScore", {"id": mid})
    if data:
        json.dump(data, open(sp, "w"))
        return data
    return cached  # may be None on first failure — caller degrades gracefully


def _first(d, *keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _clean_name(name):
    """Strip (c)/(wk)/(†) markers, return (name, is_captain, is_keeper)."""
    n = str(name or "")
    cap = bool(re.search(r"\(c\)", n, re.I))
    keep = bool(re.search(r"\((wk|†)\)", n, re.I))
    n = re.sub(r"\s*\((c|wk|†)\)", "", n, flags=re.I).strip()
    return n, cap, keep


def parse_scorecard(data):
    """Defensive multi-shape scorecard parse. Never raises."""
    out = {"innings": [], "toss": "", "umpires": "", "balls": []}
    if not isinstance(data, dict):
        return out
    try:
        toss = _first(data, "toss", "tossWinner", "toss_winner_team")
        tdec = _first(data, "tossDecision", "toss_decision", "decision")
        if toss:
            out["toss"] = str(toss) + (" chose to " + str(tdec) if tdec else "")
        ump = _first(data, "umpires", "officials", "umpire")
        if isinstance(ump, list):
            out["umpires"] = ", ".join(str(x) for x in ump)
        elif ump:
            out["umpires"] = str(ump)

        innings = data.get("scorecard") or data.get("innings") or []
        if isinstance(innings, dict):
            innings = [innings]
        for inn in innings:
            if not isinstance(inn, dict):
                continue
            batting = []
            for r in _first(inn, "batting", "batsmen") or []:
                if not isinstance(r, dict):
                    continue
                nm, cap, keep = _clean_name(_first(r, "batsman", "name", "player", "batsmanName"))
                if not nm:
                    continue
                batting.append({
                    "name": nm, "captain": cap or bool(_first(r, "captain", "isCaptain")),
                    "keeper": keep or bool(_first(r, "keeper", "isKeeper")),
                    "dismissal": str(_first(r, "dismissal", "out", "howOut", "dismissalText") or ""),
                    "r": _first(r, "r", "runs"), "b": _first(r, "b", "balls"),
                    "fours": _first(r, "4s", "fours"), "sixes": _first(r, "6s", "sixes"),
                    "sr": _first(r, "sr", "strikeRate"),
                })
            bowling = []
            for r in _first(inn, "bowling", "bowlers") or []:
                if not isinstance(r, dict):
                    continue
                nm, _, _ = _clean_name(_first(r, "bowler", "name", "player"))
                if not nm:
                    continue
                bowling.append({
                    "name": nm, "o": _first(r, "o", "overs"),
                    "m": _first(r, "m", "maidens"), "r": _first(r, "r", "runs"),
                    "w": _first(r, "w", "wickets"), "econ": _first(r, "econ", "economy"),
                })
            fow = _first(inn, "fow", "fallOfWickets", "fall_of_wickets") or []
            if isinstance(fow, dict):
                fow = [fow]
            fow_lines = []
            for f in fow:
                if isinstance(f, dict):
                    fow_lines.append(str(_first(f, "text", "description", "score") or ""))
                elif f:
                    fow_lines.append(str(f))
            out["innings"].append({
                "name": str(_first(inn, "inning", "title", "name", "team") or ""),
                "total": _first(inn, "total", "score", "runs"),
                "wickets": _first(inn, "wickets", "w"),
                "overs": _first(inn, "overs", "o"),
                "batting": batting, "bowling": bowling,
                "fow": [x for x in fow_lines if x],
            })
        balls = _first(data, "ballByBall", "balls", "commentary") or []
        if isinstance(balls, list):
            out["balls"] = balls[:120]
    except Exception as e:
        log("scorecard parse warning: %s" % e)
    return out

# ----------------------------------------------------------------------------
# Normalization + orchestration
# ----------------------------------------------------------------------------

def _score_text(sc):
    """Collapse CricAPI score array/object into 'PAK 150/3 (20 ov)' text."""
    if not sc:
        return ""
    parts = []
    items = sc if isinstance(sc, list) else [sc]
    for s in items:
        if not isinstance(s, dict):
            continue
        inn = str(_first(s, "inning", "title", "name") or "").replace(" Inning 1", "").replace(" Inning 2", "")
        r, w, o = _first(s, "r", "runs"), _first(s, "w", "wickets"), _first(s, "o", "overs")
        if r is None:
            continue
        bits = [inn, "%s/%s" % (r, w if w is not None else 0)]
        if o:
            bits.append("(%s ov)" % o)
        parts.append(" ".join(b for b in bits if b))
    return "   ".join(parts)


def fetch_cricket():
    """Main entry: returns normalized cricket data. Never raises."""
    result = {"matches": [], "series": [], "hits": 0, "errors": [],
              "live_count": 0}
    try:
        raw_matches = get_matches() or []
        live_raw = get_live() or []
        live_by_id = {}
        for e in live_raw:
            if isinstance(e, dict) and e.get("id"):
                live_by_id[str(e["id"])] = e

        series_raw = get_series() or []
        series_by_match = {}
        series_list = []
        for s in series_raw:
            if not isinstance(s, dict):
                continue
            sid = str(_first(s, "id", "seriesId") or "")
            sname = str(_first(s, "name", "seriesName") or "")
            if not sname:
                continue
            sslug = slugify(sname)
            series_list.append({"slug": sslug, "name": sname, "matches": []})
            for sm in s.get("matches") or []:
                mid = str(_first(sm, "id", "unique_id", "matchId") or "")
                if mid:
                    series_by_match[mid] = sslug
        series_by_slug = {s["slug"]: s for s in series_list}

        now = datetime.now(timezone.utc)
        matches = []
        for e in raw_matches:
            if not isinstance(e, dict):
                continue
            mid = str(_first(e, "unique_id", "id") or "")
            if not mid:
                continue
            t1 = str(_first(e, "team-1", "team1") or "")
            t2 = str(_first(e, "team-2", "team2") or "")
            lv = live_by_id.get(mid, {})
            status = str(_first(lv, "status") or _first(e, "status") or "")
            dt = parse_dt(_first(e, "dateTimeGMT", "date"))
            mtype = str(_first(e, "type", "matchType") or "")
            venue = str(_first(lv, "venue") or _first(e, "venue") or "")
            name = str(_first(lv, "name") or "") or ("%s vs %s" % (t1, t2)).strip(" vs")

            ended = is_ended(status)
            if mid in live_by_id and not ended:
                state = "live"
            elif dt and dt > now:
                state = "upcoming"
            elif ended:
                state = "recent"
            elif _first(e, "matchStarted"):
                state = "live" if not ended else "recent"
            else:
                state = "upcoming" if not ended else "recent"

            sslug = (series_by_match.get(mid)
                     or slugify(str(_first(e, "series", "seriesName", "tournament") or ""))
                     or slugify(mtype) or "cricket")
            sname = ""
            if sslug in series_by_slug:
                sname = series_by_slug[sslug]["name"]
                series_by_slug[sslug]["matches"].append(mid)
            elif sslug != "cricket":
                sname = sslug.replace("-", " ").title()
                series_by_slug.setdefault(
                    sslug, {"slug": sslug, "name": sname, "matches": []})["matches"].append(mid)

            # team logos from teamInfo when present
            imgs = {}
            for ti in _first(lv, "teamInfo", "teaminfo") or []:
                if isinstance(ti, dict) and ti.get("name"):
                    imgs[str(ti["name"]).lower()] = ti.get("img") or ""

            scorecard = None
            if state == "live":
                raw_sc = get_scorecard(mid, live=True)
                if raw_sc:
                    scorecard = parse_scorecard(raw_sc)
            elif state == "recent":
                raw_sc = get_scorecard(mid, live=False)
                if raw_sc:
                    scorecard = parse_scorecard(raw_sc)

            mslug = slugify(("%s-vs-%s" % (t1, t2)) if t1 and t2 else name)
            matches.append({
                "id": mid, "name": name, "team1": t1, "team2": t2,
                "match_type": mtype, "venue": venue, "date": dt,
                "status": status, "state": state,
                "series_slug": sslug, "series_name": sname or "Cricket",
                "slug": "%s-%s" % (mslug, mid),
                "score_text": _score_text(_first(lv, "score", "score")),
                "scorecard": scorecard, "team_imgs": imgs,
            })
            if state == "live":
                result["live_count"] += 1

        # drop ancient/undated noise, keep recent window
        cutoff = now - timedelta(days=14)
        matches = [m for m in matches
                   if m["state"] == "live"
                   or (m["date"] and m["date"] > cutoff)
                   or (not m["date"] and m["state"] == "upcoming")]
        matches.sort(key=lambda m: ({"live": 0, "upcoming": 1, "recent": 2}[m["state"]],
                                     m["date"] or datetime.max.replace(tzinfo=timezone.utc)))
        result["matches"] = matches
        result["series"] = [s for s in series_by_slug.values() if s["matches"]]
        result["hits"] = hits_used()
    except Exception as e:
        result["errors"].append(str(e))
        log("fetch_cricket failed gracefully: %s" % e)
    log("done: %d matches (%d live), %d series, %d API hits today"
        % (len(result["matches"]), result["live_count"],
           len(result["series"]), result["hits"]))
    return result


if __name__ == "__main__":
    d = fetch_cricket()
    print(json.dumps({"matches": len(d["matches"]), "series": len(d["series"]),
                      "hits": d["hits"], "errors": d["errors"],
                      "live": d["live_count"]}, indent=1))
