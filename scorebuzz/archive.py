"""Permanent match archive for the ScoreBuzz static generator.

Every match ever seen is kept forever (up to ARCHIVE_KEEP_DAYS), so ended
matches keep their stable URLs, stay in sitemap.xml and keep earning search
traffic. Finished matches are never re-fetched: their pages are rendered once
and skipped on later builds unless the data changed.

Storage: <script_dir>/match_archive/<league_slug>.json
Each file maps match_id -> record:
    {"m": match_dict, "s": summary_dict_or_None, "hash": content_hash,
     "rendered_hash": hash_of_last_render_or_None, "archived_at": iso}
"""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

ARCHIVE_KEEP_DAYS = 1825  # 5 years of match history (covers 2022 backfill)
TEMPLATE_VERSION = 4  # bump when match page templates change (forces re-render)


def _encode(o):
    if isinstance(o, datetime):
        return {"$dt": o.isoformat()}
    raise TypeError("unserialisable %r" % type(o))


def _decode(d):
    if isinstance(d, dict) and set(d.keys()) == {"$dt"}:
        try:
            return datetime.fromisoformat(d["$dt"])
        except Exception:
            return None
    return d


def _dumps(obj):
    return json.dumps(obj, default=_encode, separators=(",", ":"),
                      sort_keys=True)


def record_hash(m, s):
    """Stable content hash: match core fields + canonical summary."""
    core = {
        "id": m.get("id"),
        "state": m.get("state"),
        "date": m.get("date").isoformat() if isinstance(m.get("date"), datetime) else None,
        "status_text": m.get("status_text"),
        "clock": m.get("clock"),
        "period": m.get("period"),
        "hs": (m.get("home") or {}).get("score"),
        "as": (m.get("away") or {}).get("score"),
        "hn": (m.get("home") or {}).get("name"),
        "an": (m.get("away") or {}).get("name"),
    }
    blob = _dumps(core) + "\n" + (_dumps(s) if s else "")
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def load(archive_dir):
    """Read the archive. Returns {league_slug: {match_id: record}}."""
    arc = {}
    if not os.path.isdir(archive_dir):
        return arc
    for fn in os.listdir(archive_dir):
        if not fn.endswith(".json"):
            continue
        slug = fn[:-5]
        try:
            with open(os.path.join(archive_dir, fn), encoding="utf-8") as f:
                arc[slug] = json.load(f, object_hook=_decode)
        except Exception:
            continue
    return arc


def save(archive_dir, arc):
    """Write the archive back to disk."""
    os.makedirs(archive_dir, exist_ok=True)
    for slug, matches in arc.items():
        tmp = os.path.join(archive_dir, slug + ".json.tmp")
        final = os.path.join(archive_dir, slug + ".json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(matches, f, default=_encode)
        os.replace(tmp, final)


def merge(arc, fresh_matches, summaries):
    """Fold freshly fetched matches + summaries into the archive.

    - New matches are added (with summary when fetched).
    - Live matches always update.
    - A match that just finished updates once to its final state.
    - Finished matches keep their archived (richest) data forever.
    """
    now = datetime.now(timezone.utc)
    for m in fresh_matches:
        lg_slug = (m.get("league") or {}).get("slug") or "unknown"
        mid = str(m.get("id"))
        league_arc = arc.setdefault(lg_slug, {})
        s = summaries.get(m.get("id"))
        if mid not in league_arc:
            league_arc[mid] = {
                "m": m,
                "s": s,
                "hash": record_hash(m, s),
                "rendered_hash": None,
                "archived_at": now.isoformat(),
            }
            continue
        rec = league_arc[mid]
        fm_state = m.get("state")
        rec_state = (rec.get("m") or {}).get("state")
        if fm_state == "in" or (fm_state == "post" and rec_state != "post"):
            rec["m"] = m
            if s is not None:
                rec["s"] = s
            rec["hash"] = record_hash(m, rec["s"])
        elif s is not None and rec.get("s") is None:
            rec["s"] = s
            rec["hash"] = record_hash(rec["m"], s)


def prune(arc):
    """Drop matches older than ARCHIVE_KEEP_DAYS. Returns counts."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_KEEP_DAYS)
    kept, dropped = 0, 0
    for slug in list(arc.keys()):
        for mid in list(arc[slug].keys()):
            d = (arc[slug][mid].get("m") or {}).get("date")
            if isinstance(d, datetime) and d < cutoff:
                del arc[slug][mid]
                dropped += 1
            else:
                kept += 1
        if not arc[slug]:
            del arc[slug]
    return kept, dropped


def all_records(arc):
    """Flat list of (league_slug, match_id, record)."""
    out = []
    for slug, matches in arc.items():
        for mid, rec in matches.items():
            out.append((slug, mid, rec))
    return out
