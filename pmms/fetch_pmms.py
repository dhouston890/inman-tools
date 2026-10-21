#!/usr/bin/env python3
"""Refresh pmms.json with the latest Freddie Mac 30- and 15-year fixed rates.

Writes both rates FOR THE SAME WEEK:

    {"rate30": 7.4, "rate15": 6.71, "week": "2026-10-15", "source": "Freddie Mac PMMS"}

to pmms.json, the file the Inman buying-vs-renting calculator fetches for its
default rates (one per loan term). The calculator also still reads the older
one-rate shape {"rate": ..., "week": ...}. Freddie Mac data only: nothing from
Zillow goes in this file.

Sources, tried in order (2026-10-21; until then FRED was the only source):
  1. Freddie Mac's own history file, PMMS_history.csv (the publisher; no key;
     both rates on one row per week).
  2. FRED series MORTGAGE30US and MORTGAGE15US (needs FRED_API_KEY). FRED was
     down for maintenance on 2026-10-21 and served an HTML page instead of
     JSON, which is why Freddie Mac is now first.
Each source is tried up to 3 times with a pause between attempts. A response
that isn't the expected CSV or JSON (e.g. an HTML maintenance page) counts as
a failure of that source.

Usage:
    [FRED_API_KEY=...] python3 fetch_pmms.py [path/to/pmms.json]

The output path defaults to pmms.json next to this script. The API key is
read from the FRED_API_KEY environment variable only (a GitHub Actions secret
in production). It is never printed, logged or written anywhere. Without it,
only the Freddie Mac source is tried.

Exit status:
    0  pmms.json written, or already current, or every source failed but the
       file is still current (its week is at most 8 days old): a transient
       outage on a backstop run is logged as a warning, not an error.
    1  every source failed and the file is stale (more than 8 days old), or
       the sources work but have shown no new week for more than 15 days, or
       the file couldn't be written. The existing pmms.json is never damaged:
       a new file is written to a temp file and moved into place only after
       it validates, and the file never moves back to an older week.

Testing overrides (environment): PMMS_FREDDIE_URL, PMMS_FRED_API,
PMMS_RETRY_DELAYS (comma-separated seconds, e.g. "0,0"), PMMS_TODAY
(YYYY-MM-DD).

Standard library only (Python 3.8+).
"""
import csv
import datetime
import io
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

FREDDIE_URL = os.environ.get("PMMS_FREDDIE_URL", "https://www.freddiemac.com/pmms/docs/PMMS_history.csv")
FRED_API = os.environ.get("PMMS_FRED_API", "https://api.stlouisfed.org/fred/series/observations")
FRED_SERIES = {"rate30": "MORTGAGE30US", "rate15": "MORTGAGE15US"}
TIMEOUT_S = 30
RETRY_DELAYS = [float(x) for x in os.environ.get("PMMS_RETRY_DELAYS", "20,60").split(",") if x.strip()]
# Plausibility bounds for a fixed mortgage rate (either term), in percent.
RATE_MIN, RATE_MAX = 1.0, 20.0
STALE_IF_SOURCES_DOWN_DAYS = 8    # all sources failed: error only if the file is older than this
STALE_IF_NO_NEW_WEEK_DAYS = 15    # sources fine but no new week for this long: error
UA = {"User-Agent": "inman-tools-pmms-refresh (github.com/dhouston890/inman-tools)"}


class SourceError(Exception):
    """One source failed; the message is safe to log (never contains the key)."""


def log(msg):
    print("fetch_pmms: " + msg, flush=True)


def today():
    t = os.environ.get("PMMS_TODAY")
    return datetime.date.fromisoformat(t) if t else datetime.date.today()


def http_get(url, label):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            ctype = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise SourceError("%s returned HTTP %d" % (label, e.code))
    except urllib.error.URLError as e:
        raise SourceError("could not reach %s (%s)" % (label, type(e.reason).__name__))
    except OSError as e:
        raise SourceError("network error from %s (%s)" % (label, type(e).__name__))
    if "html" in ctype.lower() or body.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        raise SourceError("%s returned a web page instead of data (likely maintenance)" % label)
    return body


def parse_rate(x):
    x = (x or "").strip()
    if x in ("", "."):
        return None
    return round(float(x), 2)


def from_freddie():
    """Latest week with both rates, from Freddie Mac's PMMS_history.csv."""
    body = http_get(FREDDIE_URL, "Freddie Mac")
    rows = list(csv.DictReader(io.StringIO(body)))
    if not rows or not {"date", "pmms30", "pmms15"} <= set(rows[0].keys()):
        raise SourceError("Freddie Mac file has an unexpected layout (columns: %s)"
                          % ",".join(list(rows[0].keys())[:6]) if rows else "Freddie Mac file is empty")
    best = None
    for r in rows:
        try:
            week = datetime.datetime.strptime(r["date"].strip(), "%m/%d/%Y").date()
            r30, r15 = parse_rate(r["pmms30"]), parse_rate(r["pmms15"])
        except (ValueError, AttributeError):
            continue
        if r30 is None or r15 is None:
            continue
        if best is None or week > best[0]:
            best = (week, r30, r15)
    if best is None:
        raise SourceError("no usable week in the Freddie Mac file")
    return {"rate30": best[1], "rate15": best[2]}, best[0], "Freddie Mac PMMS"


def from_fred(api_key):
    """Latest week BOTH FRED series report, so the rates never mix weeks."""
    got = {}
    for k, sid in FRED_SERIES.items():
        q = urllib.parse.urlencode({"series_id": sid, "api_key": api_key, "file_type": "json",
                                    "sort_order": "desc", "limit": 10})
        body = http_get(FRED_API + "?" + q, "FRED")
        try:
            payload = json.loads(body)
        except ValueError:
            raise SourceError("FRED returned something other than JSON")
        obs = {}
        for o in payload.get("observations", []):
            try:
                rate = parse_rate(o.get("value"))
                week = datetime.date.fromisoformat(o.get("date"))
            except (TypeError, ValueError):
                continue
            if rate is not None:
                obs[week] = rate
        if not obs:
            raise SourceError("no usable %s observation from FRED" % sid)
        got[k] = obs
    common = set.intersection(*(set(v) for v in got.values()))
    if not common:
        raise SourceError("FRED's two series share no recent week")
    week = max(common)
    return {k: got[k][week] for k in FRED_SERIES}, week, "Freddie Mac PMMS via FRED"


def with_retries(fn, label):
    attempts = len(RETRY_DELAYS) + 1
    for i in range(attempts):
        try:
            return fn()
        except SourceError as e:
            log("%s attempt %d/%d failed: %s" % (label, i + 1, attempts, e))
            if i < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[i])
    return None


def read_existing(path):
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        return old, datetime.date.fromisoformat(old["week"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def write_atomic(out_path, new):
    out_dir = os.path.dirname(out_path)
    fd, tmp = tempfile.mkstemp(prefix=".pmms-", suffix=".json", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(new, f, separators=(",", ":"))
            f.write("\n")
        with open(tmp, encoding="utf-8") as f:
            if json.load(f) != new:
                raise ValueError("round-trip mismatch")
        os.chmod(tmp, 0o644)
        os.replace(tmp, out_path)
    except (OSError, ValueError) as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        log("ERROR: could not write %s (%s); pmms.json left unchanged" % (out_path, type(e).__name__))
        sys.exit(1)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "pmms.json")
    old, old_week = read_existing(out_path)
    age = (today() - old_week).days if old_week else None

    sources = [("Freddie Mac", from_freddie)]
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if api_key:
        sources.append(("FRED", lambda: from_fred(api_key)))
    else:
        log("FRED_API_KEY not set; the FRED fallback is skipped")

    result = None
    for label, fn in sources:
        got = with_retries(fn, label)
        if got is None:
            continue
        rates, week, source = got
        bad = [k for k, r in rates.items() if not (RATE_MIN <= r <= RATE_MAX)]
        if bad:
            log("%s gave an implausible rate (%s); trying the next source" % (label, rates))
            continue
        if week > today() + datetime.timedelta(days=7):
            log("%s gave a future week (%s); trying the next source" % (label, week))
            continue
        result = got
        break

    if result is None:
        if age is not None and age <= STALE_IF_SOURCES_DOWN_DAYS:
            log("WARNING: every source failed, but pmms.json is current (week of %s, %d days old); "
                "leaving it unchanged" % (old_week, age))
            return
        log("ERROR: every source failed and pmms.json is stale (week of %s); left unchanged" % old_week)
        sys.exit(1)

    rates, week, source = result
    desc = "30-yr %.2f%%, 15-yr %.2f%%, week of %s (%s)" % (rates["rate30"], rates["rate15"], week, source)
    # Compare rates and week only, so a change of source alone doesn't churn the file.
    same = old is not None and old_week == week and old.get("rate30") == rates["rate30"] \
        and old.get("rate15") == rates["rate15"]
    if same or (old_week is not None and week < old_week):
        if old_week is not None and week < old_week:
            log("the source's latest week (%s) is older than pmms.json (%s); leaving the file unchanged"
                % (week, old_week))
        else:
            log("already current: " + desc)
        latest = max(week, old_week) if old_week else week
        if (today() - latest).days > STALE_IF_NO_NEW_WEEK_DAYS:
            log("ERROR: no new PMMS week for %d days (latest %s); check Freddie Mac's release"
                % ((today() - latest).days, latest))
            sys.exit(1)
        return

    write_atomic(out_path, {"rate30": rates["rate30"], "rate15": rates["rate15"],
                            "week": week.isoformat(), "source": source})
    log("wrote %s: %s" % (out_path, desc))


if __name__ == "__main__":
    main()
