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
LIVE_TTL = 30 * 60          # live snapshot: fresh enough to catch starts
SCORECARD_LIVE_TTL = 45 * 60  # live scorecard refresh at most every 45 min
RUN_SCORECARD_CAP = 25      # max scorecard API fetches in one build
_run_sc_fetches = [0]
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
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # CricAPI sometimes omits the offset — its times are GMT
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
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
    if isinstance(data, dict):
        lst = data.get("matches") or data.get("data")
        if isinstance(lst, list):
            # The endpoint pages its catalogue (info.totalRows): the
            # first page alone can be one tournament's fixtures while
            # today's matches sit on a later page. Follow a few pages.
            info = data.get("info") or {}
            try:
                total = int(info.get("totalRows") or info.get("total")
                            or len(lst))
            except Exception:
                total = len(lst)
            seen = set()
            for e in lst:
                if isinstance(e, dict):
                    seen.add(str(_first(e, "unique_id", "id") or ""))
            pages = 0
            while len(lst) < total and pages < 2 and hits_used() < HIT_CAP:
                more, _ = cricapi("/v1/matches", {"offset": len(lst)})
                ml = None
                if isinstance(more, dict):
                    ml = more.get("matches") or more.get("data")
                if not isinstance(ml, list) or not ml:
                    break
                added = 0
                for e in ml:
                    if isinstance(e, dict):
                        k = str(_first(e, "unique_id", "id") or "")
                        if k and k not in seen:
                            seen.add(k)
                            lst.append(e)
                            added += 1
                pages += 1
                if not added:
                    break  # endpoint ignored offset — avoid duplicates
            if pages:
                log("matches paginated: %d rows of %d reported"
                    % (len(lst), total))
            _cache_put("matches.json", lst)
            return lst
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
    """Live snapshot (30-min cache — this is the live-games source)."""
    cached = _cache_get("live.json", LIVE_TTL)
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
    if _run_sc_fetches[0] >= RUN_SCORECARD_CAP:
        return cached  # per-build seatbelt; the daily cap is the real one
    _run_sc_fetches[0] += 1
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
            ext = _first(inn, "extras", "extra")
            if isinstance(ext, dict):
                ext = ", ".join("%s %s" % (k, v) for k, v in ext.items())
            out["innings"].append({
                "name": str(_first(inn, "inning", "title", "name", "team") or ""),
                "total": _first(inn, "total", "score", "runs"),
                "wickets": _first(inn, "wickets", "w"),
                "overs": _first(inn, "overs", "o"),
                "extras": str(ext or ""),
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

def _flat_score(lv, e):
    """cricScore rows carry ready-made score strings (t1s/t2s) instead
    of a structured score array."""
    a = str(_first(lv, "t1s") or _first(e, "t1s") or "")
    b = str(_first(lv, "t2s") or _first(e, "t2s") or "")
    return "   ".join(x for x in (a, b) if x)


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


def _update_results_store(matches):
    """Persist every finished match ever seen (keyed by match id) so
    series points tables can cover a whole tournament, not just the
    14-day display window. Lives in CACHE_DIR, which the deploy
    workflow caches between runs. Never raises."""
    p = os.path.join(CACHE_DIR, "series_results.json")
    try:
        store = json.load(open(p))
        if not isinstance(store, dict):
            store = {}
    except Exception:
        store = {}
    try:
        for m in matches:
            if m.get("state") != "recent":
                continue
            inns = []
            for inn in (m.get("scorecard") or {}).get("innings") or []:
                inns.append({"name": inn.get("name") or "",
                             "total": inn.get("total"),
                             "wickets": inn.get("wickets"),
                             "overs": inn.get("overs")})
            store[m["id"]] = {
                "series": m["series_slug"], "t1": m["team1"],
                "t2": m["team2"], "status": m["status"],
                "type": m["match_type"],
                "date": m["date"].isoformat() if m.get("date") else "",
                "innings": inns,
            }
        os.makedirs(CACHE_DIR, exist_ok=True)
        json.dump(store, open(p, "w"))
    except Exception as e:
        log("results store update failed: %s" % e)
    return store


def fetch_cricket():
    """Main entry: returns normalized cricket data. Never raises."""
    result = {"matches": [], "series": [], "hits": 0, "errors": [],
              "live_count": 0, "results_store": {}}
    try:
        raw_matches = get_matches() or []
        live_raw = get_live() or []
        live_by_id = {}
        for e in live_raw:
            if isinstance(e, dict) and e.get("id"):
                live_by_id[str(e["id"])] = e
        # A match that is live right now may not appear in the fixtures
        # list at all (different series, later page). Build records from
        # the live snapshot itself so a running game can never be missed.
        known_ids = set()
        for e in raw_matches:
            if isinstance(e, dict):
                kid = str(_first(e, "unique_id", "id") or "")
                if kid:
                    known_ids.add(kid)
        live_only = 0
        _live_only_entries = []
        for e in live_raw:
            if isinstance(e, dict):
                lid = str(e.get("id") or "")
                if lid and lid not in known_ids:
                    _live_only_entries.append(e)
                    known_ids.add(lid)
                    live_only += 1
        if _live_only_entries:
            raw_matches = _live_only_entries + raw_matches
        log("sources: %d fixture rows, %d live rows (%d live-only)"
            % (len(raw_matches) - live_only, len(live_raw), live_only))

        series_raw = get_series() or []
        series_by_match = {}
        series_by_id = {}
        series_list = []
        for s in series_raw:
            if not isinstance(s, dict):
                continue
            sid = str(_first(s, "id", "seriesId") or "")
            sname = str(_first(s, "name", "seriesName") or "")
            if not sname:
                continue
            sslug = slugify(sname)
            if sid:
                series_by_id[sid] = sslug
            series_list.append({"slug": sslug, "name": sname, "matches": []})
            sm_list = s.get("matches")
            if isinstance(sm_list, list):  # v1 returns a COUNT here, not a list
                for sm in sm_list:
                    if isinstance(sm, dict):
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
            t1 = str(_first(e, "team-1", "team1", "t1") or "")
            t2 = str(_first(e, "team-2", "team2", "t2") or "")
            teams_arr = e.get("teams")
            if isinstance(teams_arr, list) and len(teams_arr) >= 2:
                t1 = t1 or str(teams_arr[0] or "")
                t2 = t2 or str(teams_arr[1] or "")
            lv = live_by_id.get(mid, {})
            if not t1:
                t1 = str(_first(lv, "t1") or "")
            if not t2:
                t2 = str(_first(lv, "t2") or "")
            status = str(_first(lv, "status") or _first(e, "status") or "")
            dt = parse_dt(_first(e, "dateTimeGMT", "date")
                          or _first(lv, "dateTimeGMT", "date"))
            mtype = str(_first(e, "matchType", "type")
                        or _first(lv, "matchType", "type") or "")
            venue = str(_first(lv, "venue") or _first(e, "venue") or "")
            name = (str(_first(lv, "name") or _first(e, "name") or "")
                    or ("%s vs %s" % (t1, t2)).strip(" vs"))

            ms = str(_first(lv, "ms") or _first(e, "ms") or "").lower()
            started = bool(_first(e, "matchStarted")
                           or _first(lv, "matchStarted"))
            ended = (is_ended(status) or bool(_first(e, "matchEnded"))
                     or bool(_first(lv, "matchEnded")) or ms == "result")
            has_score = bool(_first(lv, "t1s") or _first(e, "t1s")
                             or _first(lv, "score") or _first(e, "score"))
            not_started = ("not started" in status.lower()
                           or ms == "fixture") and not has_score
            if ended:
                state = "recent"
            elif not_started:
                state = "upcoming"
            elif started or ms == "live" or mid in live_by_id:
                state = "live"
            else:
                state = "upcoming"

            sslug = (series_by_match.get(mid)
                     or series_by_id.get(str(_first(e, "series_id",
                                                    "seriesId") or ""))
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
            for _src in (lv, e):
                if _first(_src, "t1img") and t1:
                    imgs.setdefault(t1.lower(), _first(_src, "t1img"))
                if _first(_src, "t2img") and t2:
                    imgs.setdefault(t2.lower(), _first(_src, "t2img"))
            for ti in (_first(lv, "teamInfo", "teaminfo")
                       or _first(e, "teamInfo", "teaminfo") or []):
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
                "score_text": (_score_text(_first(lv, "score", "score")
                                           or _first(e, "score", "score"))
                               or _flat_score(lv, e)),
                "scorecard": scorecard, "team_imgs": imgs,
            })
            if state == "live":
                result["live_count"] += 1

        # Probe pass: CricAPI's bulk lists can lag a match that has
        # actually started (start time passed, still flagged fixture).
        # Ask the per-match endpoint for the truth — capped, and cached
        # for 20 minutes so a stale feed costs a few hits, not a flood.
        try:
            probes = _cache_get("probes.json", 20 * 60) or {}
            fresh_probes = 0
            for m in matches:
                if m["state"] != "upcoming" or not m["date"]:
                    continue
                if not (now - timedelta(hours=12) <= m["date"] <= now):
                    continue
                p = probes.get(m["id"])
                if p is None and fresh_probes < 5:
                    data, _ = cricapi("/v1/cricScore", {"id": m["id"]})
                    fresh_probes += 1
                    obj = None
                    if isinstance(data, dict):
                        obj = data.get("data") or data
                        if isinstance(obj, list):
                            obj = obj[0] if obj else None
                    p = {"state": "", "status": "", "score_text": ""}
                    if isinstance(obj, dict):
                        ms2 = str(_first(obj, "ms") or "").lower()
                        st2 = str(_first(obj, "status") or "")
                        sc2 = (_score_text(_first(obj, "score", "score"))
                               or _flat_score(obj, obj))
                        if (ms2 == "result" or is_ended(st2)
                                or _first(obj, "matchEnded")):
                            p = {"state": "recent", "status": st2,
                                 "score_text": sc2}
                        elif (ms2 == "live" or _first(obj, "matchStarted")
                                or sc2):
                            p = {"state": "live", "status": st2,
                                 "score_text": sc2}
                    probes[m["id"]] = p
                    log("probe %s (%s): -> %s"
                        % (m["id"], m["name"][:40], p["state"] or "no change"))
                if p and p.get("state") in ("live", "recent"):
                    m["state"] = p["state"]
                    if p.get("status"):
                        m["status"] = p["status"]
                    if p.get("score_text"):
                        m["score_text"] = p["score_text"]
                    if p["state"] == "live" and not m.get("scorecard"):
                        raw_sc = get_scorecard(m["id"], live=True)
                        if raw_sc:
                            m["scorecard"] = parse_scorecard(raw_sc)
            _cache_put("probes.json", probes)
            result["live_count"] = sum(1 for m in matches
                                       if m["state"] == "live")
        except Exception as e:
            log("probe pass failed gracefully: %s" % e)

        # drop ancient/undated noise, keep recent window
        cutoff = now - timedelta(days=14)
        matches = [m for m in matches
                   if m["state"] == "live"
                   or (m["date"] and m["date"] > cutoff)
                   or (not m["date"] and m["state"] == "upcoming")]
        matches.sort(key=lambda m: ({"live": 0, "upcoming": 1, "recent": 2}[m["state"]],
                                     m["date"] or datetime.max.replace(tzinfo=timezone.utc)))
        result["matches"] = matches
        result["results_store"] = _update_results_store(matches)
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
