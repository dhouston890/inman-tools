#!/usr/bin/env python3
"""Refresh pmms.json with the latest Freddie Mac 30- and 15-year fixed rates.

Fetches the recent observations of FRED series MORTGAGE30US and MORTGAGE15US
(Freddie Mac Primary Mortgage Market Survey, 30-year and 15-year fixed,
weekly) and writes both rates FOR THE SAME WEEK, the latest week both series
report:

    {"rate30": 7.28, "rate15": 6.6, "week": "2026-10-01", "source": "Freddie Mac PMMS via FRED"}

to pmms.json, the file the Inman buying-vs-renting calculator fetches for its
default rates (one per loan term; the calculator's 30-year | 15-year switch,
2026-10-08). Until then the file held one rate, {"rate": ..., "week": ...};
the calculator still reads that shape. Freddie Mac data only: nothing from
Zillow goes in this file.

Usage:
    FRED_API_KEY=... python3 fetch_pmms.py [path/to/pmms.json]

The output path defaults to pmms.json next to this script. The API key is
read from the FRED_API_KEY environment variable only (a GitHub Actions secret
in production). It is never printed, logged or written anywhere.

Exit status:
    0  pmms.json written, or already current (nothing to do)
    1  any failure: missing key, network or API error, bad response. The
       existing pmms.json is left untouched: the new file is written to a
       temporary file and moved into place only after it validates.

Standard library only (Python 3.8+).
"""
import datetime
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

SERIES = {"rate30": "MORTGAGE30US", "rate15": "MORTGAGE15US"}
SOURCE = "Freddie Mac PMMS via FRED"
API = "https://api.stlouisfed.org/fred/series/observations"
TIMEOUT_S = 30
# Plausibility bounds for a fixed mortgage rate (either term), in percent.
# Anything outside them is treated as a bad response rather than published.
RATE_MIN, RATE_MAX = 1.0, 20.0


def fail(msg):
    print("fetch_pmms: ERROR: " + msg + " (pmms.json left unchanged)", file=sys.stderr)
    sys.exit(1)


def fetch_recent(api_key, series):
    """Return {week: rate} for the series' recent non-missing observations."""
    query = urllib.parse.urlencode({
        "series_id": series,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 10,  # a few rows, in case the newest is "." (missing)
    })
    req = urllib.request.Request(API + "?" + query,
                                 headers={"User-Agent": "inman-tools-pmms-refresh"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Report the status only: the request URL carries the API key.
        fail("FRED returned HTTP %d %s" % (e.code, e.reason))
    except urllib.error.URLError as e:
        fail("could not reach FRED (%s)" % type(e.reason).__name__)
    except (ValueError, OSError) as e:
        fail("bad response from FRED (%s)" % type(e).__name__)

    out = {}
    for obs in payload.get("observations", []):
        value, date = obs.get("value"), obs.get("date")
        if value in (None, "", "."):
            continue
        try:
            rate = round(float(value), 2)
            week = datetime.date.fromisoformat(date)
        except (TypeError, ValueError):
            continue
        out[week] = rate
    if not out:
        fail("no usable %s observation in the FRED response" % series)
    return out


def fetch_latest(api_key):
    """Return ({"rate30": r, "rate15": r}, week) for the latest week BOTH
    series report, so the two rates never mix weeks. Freddie Mac publishes
    them together; if one series lags, the older common week is used."""
    got = {k: fetch_recent(api_key, sid) for k, sid in SERIES.items()}
    common = set.intersection(*(set(v) for v in got.values()))
    if not common:
        fail("MORTGAGE30US and MORTGAGE15US share no recent week")
    week = max(common)
    latest = {k: max(v) for k, v in got.items()}
    if len(set(latest.values())) > 1:
        print("fetch_pmms: the series' latest weeks differ (%s); using %s, the latest week both report"
              % (", ".join("%s %s" % (SERIES[k], w) for k, w in sorted(latest.items())), week))
    return {k: got[k][week] for k in SERIES}, week


def read_existing(path):
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        return old, datetime.date.fromisoformat(old["week"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(here, "pmms.json")

    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        fail("FRED_API_KEY is not set")

    rates, week = fetch_latest(api_key)
    for k, rate in rates.items():
        if not (RATE_MIN <= rate <= RATE_MAX):
            fail("implausible %s rate %.2f for week of %s" % (SERIES[k], rate, week))
    if week > datetime.date.today() + datetime.timedelta(days=7):
        fail("observation date %s is in the future" % week)

    new = {"rate30": rates["rate30"], "rate15": rates["rate15"], "week": week.isoformat(), "source": SOURCE}
    desc = "30-yr %.2f%%, 15-yr %.2f%%, week of %s" % (rates["rate30"], rates["rate15"], week)
    old, old_week = read_existing(out_path)
    if old == new:
        print("fetch_pmms: already current: " + desc)
        return
    # Never roll the file back to an older week (e.g. a stale API mirror).
    if old_week is not None and week < old_week:
        print("fetch_pmms: FRED's latest week (%s) is older than pmms.json (%s); "
              "leaving the file unchanged" % (week, old_week))
        return

    # Write to a temp file in the same folder, then move it into place, so a
    # failure mid-write can never leave a truncated pmms.json.
    out_dir = os.path.dirname(out_path)
    fd, tmp = tempfile.mkstemp(prefix=".pmms-", suffix=".json", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(new, f, separators=(",", ":"))
            f.write("\n")
        with open(tmp, encoding="utf-8") as f:
            check = json.load(f)
        if check != new:
            raise ValueError("round-trip mismatch")
        os.chmod(tmp, 0o644)
        os.replace(tmp, out_path)
    except (OSError, ValueError) as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        fail("could not write %s (%s)" % (out_path, type(e).__name__))
    print("fetch_pmms: wrote %s: %s" % (out_path, desc))


if __name__ == "__main__":
    main()
