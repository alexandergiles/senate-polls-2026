# Senate Polls 2026

An interactive, static tracker of public polling in the 35 U.S. Senate races on the ballot on
November 3, 2026. One page, no build dependencies beyond Python, no server: it hosts on GitHub Pages as is.

**What's on the page**

- **Seat math.** Seats not on the ballot plus the leader of each race's polling average (Cook Political
  Report rating where no public polls exist). A snapshot of the polls, not a forecast.
- **Race-by-race margins.** A dot plot of every polled race's average, with the spread of recent polls.
- **Latest polls.** A newest-first feed across all races.
- **All 35 races.** Filterable cards; each race opens its own view (`#TX`, `#OH-special`, …) with every
  poll on a trend chart and in a sortable table with links back to each pollster's release, plus how the
  final polls did in that state in 2018–2024.
- **Election night** (`#election-night`). Poll-closing times for all 35 races, hour by hour, with each
  state's counting rules.
- **How polls have missed** (`#accuracy`). Every Senate race with public polling in the final three weeks
  of 2018, 2020, 2022 and 2024, scored against the certified result; a what-if that applies each cycle's
  average miss to the 2026 averages.

## Data

Everything the page shows is in [`data/senate-polls-2026.json`](data/senate-polls-2026.json):

| Field | Source |
|---|---|
| Individual polls | [RealClearPolling](https://www.realclearpolling.com/latest-polls/senate) race pages and latest-polls listing, read 2026-10-29 (fieldwork dates for the newest polls from the pollsters' releases) |
| Nebraska polls RCP does not list | [270toWin](https://www.270towin.com/2026-senate-polls/nebraska) (marked `"via": "270toWin"`) |
| Candidates, incumbents, open seats | [270toWin](https://www.270towin.com/2026-senate-election/) and [Ballotpedia](https://ballotpedia.org/United_States_Senate_elections,_2026) |
| Race ratings | Cook Political Report, as shown on 270toWin (Oct. 20, 2026 update) |

Each race's **average** is the site's own: the plain mean of the five most recent polls (all polls
where fewer than five exist). RCP's published average as of the read is kept in the data file as
`rcp_average_when_read` for reference but is not shown. Trend lines are a Gaussian-kernel smoothing
(12-day bandwidth) of the listed polls, drawn only where polls exist.

The New York Times runs its own tracker with a different poll set and method:
<https://www.nytimes.com/interactive/polls/senate-polls-2026.html>.

### The accuracy study (`data/accuracy-2018-2024.json`)

Built by `scripts/accuracy.py` from one file per cycle of RealClearPolitics/RealClearPolling race pages
(final RCP average, the last five polls listed, the result), read through the Wayback Machine for
2018–2022 and RCP's own data feed for 2024, with results checked against Wikipedia's election pages.

- The polling number for each past race is the same estimator the site uses for 2026: the plain mean of
  the last five polls. RCP's published average is kept alongside where it issued one.
- **Miss** = result margin − polled margin, Democratic side minus Republican. Negative: the polls
  overrated the Democrat (or the independent running against a Republican). Positive: they overrated
  the Republican.
- Cycle statistics use races whose last poll ended within 21 days of the election; older ones are listed
  as stale. Runoffs are listed but excluded. "Competitive" means the polls had the race within 10 points.
- The what-if view shifts every 2026 average by a cycle's mean miss in competitive races; it is a
  sensitivity check, not a forecast.

Regenerate with `python3 scripts/accuracy.py <dir of cycle_YYYY.json files> data/accuracy-2018-2024.json`,
then `python3 build.py`.

## Updating

1. Edit `data/senate-polls-2026.json`: append new polls to a race's `polls` array and update
   `generated`. Averages are recomputed at build time, so there is nothing else to edit.
2. `python3 build.py` regenerates `index.html` from `src/page.html` and the data file (and rewrites
   the data file with sorted polls and fresh averages).
3. Commit and push; GitHub Pages redeploys.

`scripts/merge_sources.py` is the one-off script that merged the extracted RCP race files and the
seat roster into the data file.

## Layout

```
index.html                 generated page (commit this; Pages serves it)
src/page.html              page source: styles, markup, script, __DATA__ placeholder
data/senate-polls-2026.json
data/accuracy-2018-2024.json   the 2018–2024 scoring, embedded at build time if present
build.py
scripts/merge_sources.py
scripts/accuracy.py        scores past cycles' final polls against results
```
