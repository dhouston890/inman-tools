# pmms: live mortgage rates for Inman calculators

`pmms.json` holds the latest Freddie Mac 30-year and 15-year fixed rates
(Primary Mortgage Market Survey), both for the same week:

```json
{"rate30":7.4,"rate15":6.71,"week":"2026-10-15","source":"Freddie Mac PMMS"}
```

Since 2026-10-21 the rates come from Freddie Mac's own history file
(`https://www.freddiemac.com/pmms/docs/PMMS_history.csv`, no key needed),
with FRED (`MORTGAGE30US`, `MORTGAGE15US`) as a fallback. FRED's maintenance
outage on 2026-10-21 failed the only-FRED version of the job.

The buying-vs-renting calculator (`buy-vs-rent-condo/`) fetches
`../pmms/pmms.json` on load and uses it for its default rates: `rate30` for
the 30-year term and `rate15` for the 15-year term (the calculator's
30-year | 15-year switch, added 2026-10-08), labeled with the week. If the
fetch fails, or the file is older than the rates baked into the page at
build time, the page uses the baked-in rates. The file holds Freddie Mac
data only; no Zillow data goes here.

**Old shape.** Before 2026-10-08 the file held one rate,
`{"rate":7.28,"week":"2026-10-01",...}`. The calculator still reads it: its
`rate` is the 30-year rate, and the 15-year rate then comes from the page's
build-time reading, but only if the file's week is the build week (otherwise
the file is ignored, so the two terms never show rates from different weeks).
`fetch_pmms.py` and `60_build_pitch2_data.R` both replace an old-shape file
with the new shape.

**Status: active since 2026-10-08.** The files are in the `inman-tools` repo
(`pmms/` and `.github/workflows/pmms-refresh.yml`), the `FRED_API_KEY`
repository secret is set, and the calculator uses the file's rates
(`LIVE_RATE_ACTIVE = true` in its template). The "Activate" steps below are
kept for reference, e.g. to set this up again in another repo.

## Files

| File | Goes to (inman-tools repo) | What it does |
|---|---|---|
| `pmms.json` | `pmms/pmms.json` | The rate file. Initial copy written by `scripts/condo_study/60_build_pitch2_data.R` from `clean_data/condo_study/pmms_latest.csv` (which `65_rate_snapshot.R` writes with both rates for one week). |
| `fetch_pmms.py` | `pmms/fetch_pmms.py` | Gets the latest week with both rates from Freddie Mac's `PMMS_history.csv`, falling back to the FRED API (`MORTGAGE30US`/`MORTGAGE15US`, key from `FRED_API_KEY`), each tried 3 times. Rejects web pages (e.g. maintenance notices), implausible rates and future dates; never moves the file back to an older week; compares rates and week only, so a change of source alone doesn't rewrite the file. Exits 0 with a warning if every source is down but the file is at most 8 days old; exits 1 if the file is stale or no new week has appeared for 15 days. Python standard library only. |
| `.github-workflow/pmms-refresh.yml` | `.github/workflows/pmms-refresh.yml` | GitHub Actions workflow: Thursdays hourly 17:17–23:17 UTC, Wednesdays 17:17 and 18:17 UTC (holiday weeks), a daily 14:17 UTC backstop (see the comments in the file), and a manual "Run workflow" button. GitHub starts scheduled runs late and sometimes drops one, hence the many runs; each extra run is a no-op. Commits `pmms/pmms.json` only if it changed, using the built-in `GITHUB_TOKEN` (`contents: write`), rebasing first if another push landed. |
| `README.md` | optional | This file. |

Do not copy the `.github-workflow/` folder itself into `pmms/`. The workflow
file only runs from `.github/workflows/` at the repo root.

## Activate

1. **Copy the files** into `~/Desktop/inman-tools` as listed in the table above,
   then commit and push them (GitHub Desktop is fine).
2. **Add the API key as a repository secret.** On GitHub, open inman-tools, then
   Settings > Secrets and variables > Actions > New repository secret. Name it
   `FRED_API_KEY` and paste the key as the value. A free key is available from
   the FRED website (fredaccount.stlouisfed.org). Never put the key in a file,
   a commit or a workflow.
3. **Check that the workflow can push.** The workflow asks for
   `contents: write` itself, which GitHub's default settings allow. If the
   commit step fails with a 403, look at Settings > Actions > General >
   Workflow permissions.
4. **Run it once by hand.** Open the Actions tab, pick "Refresh PMMS rate", then
   "Run workflow". The run should end green. If Freddie Mac has published since
   the file was written, it will add one commit that changes `pmms/pmms.json`.
5. **Confirm the site picked it up.** After a run that commits, the
   "pages build and deployment" run should follow in the Actions tab. Then
   `https://dhouston890.github.io/inman-tools/pmms/pmms.json` should show the
   new week with both `rate30` and `rate15`, and the calculator's sources line
   should read "week of" that date.
   (GitHub Pages rebuilds on pushes made with `GITHUB_TOKEN` when it publishes
   from a branch, as this repo does, from `main`. If no Pages build follows,
   that is the thing to investigate.)

## Keep it running

**GitHub pauses scheduled workflows in public repos after 60 days without
repository activity.** inman-tools is public. It is not clear whether the
workflow's own commits count as activity, so don't rely on them.

- **Check:** the Actions tab shows a banner on the workflow saying it has been
  disabled, or run `gh workflow list --all --repo dhouston890/inman-tools`
  and look for a state of `disabled_inactivity`.
- **Resume:** click "Enable workflow" on that banner, or run
  `gh workflow enable pmms-refresh.yml --repo dhouston890/inman-tools`. Then
  run it once by hand (step 4) to catch up.
- A paused workflow fails safe. The calculator keeps using the last
  `pmms.json` and labels it with its week, so the rate is dated, not wrong.
  Any push to the repo, such as publishing a new tool, resets the 60-day clock.

## Turn it off

Disable the workflow from the Actions tab (or `gh workflow disable`), or delete
`.github/workflows/pmms-refresh.yml`. The calculator keeps working on whatever
`pmms.json` holds, or on its baked-in rate if the file is removed.

## The calculator's other data (2026-10-21)

The same workflow now has two small jobs for the buying-vs-renting
calculator's **Zillow** data, which refreshes monthly on Houston's Mac (not
here; Zillow data never goes into this repo except inside the page):

- **`calc-watchdog`** (daily 14:17 UTC run): opens the issue "Calculator data
  refresh needs attention" if the deployed page's Zillow month ended more than
  13 weeks ago, which means the Mac job has stopped (comments on Mondays while
  it stays open). Test it with `gh workflow run pmms-refresh.yml -f watchdog_month=2026-01`.
- **`calc-alert`** (dispatch only): the Mac job opens, comments on or closes
  that issue through it, so GitHub emails Houston.

Both skip the PMMS job, which runs as before. Everything about the monthly
Zillow refresh, and the **annual checklist** for the calculator's other
sources (ACS PUMS, NAR, Enact, FHFA, Angi, LodeStar, NAIC, ICE, BLS PPI), is in
`active_workspace/documentation/condo_study/calculator_maintenance.md`.
