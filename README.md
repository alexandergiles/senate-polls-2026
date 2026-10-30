# Senate Polls 2026

An interactive, static tracker of public polling in the 35 U.S. Senate races on the ballot on
November 3, 2026. One page, no build dependencies beyond Python, no server: it hosts on GitHub Pages as is.

**What's on the page**

- **Seat math.** Seats not on the ballot plus the leader of each race's polling average (Cook Political
  Report rating where no public polls exist). A snapshot of the polls, not a forecast.
- **Race-by-race margins.** A dot plot of every polled race's average, with the spread of recent polls.
- **Latest polls.** A newest-first feed across all races.
- **All 35 races.** Filterable cards; each race opens its own view (`#TX`, `#OH-special`, …) with every
  poll on a trend chart and in a sortable table with links back to each pollster's release.

## Data

Everything the page shows is in [`data/senate-polls-2026.json`](data/senate-polls-2026.json):

| Field | Source |
|---|---|
| Individual polls, RCP averages | [RealClearPolling](https://www.realclearpolling.com/latest-polls/senate) race pages, read 2026-10-29 |
| Nebraska polls RCP does not list | [270toWin](https://www.270towin.com/2026-senate-polls/nebraska) (marked `"via": "270toWin"`) |
| Candidates, incumbents, open seats | [270toWin](https://www.270towin.com/2026-senate-election/) and [Ballotpedia](https://ballotpedia.org/United_States_Senate_elections,_2026) |
| Race ratings | Cook Political Report, as shown on 270toWin (Oct. 20, 2026 update) |

Where RCP publishes no average (Nebraska, Alaska's ranked-choice first round) the page shows a plain
mean of the five most recent polls and says so. Trend lines are a Gaussian-kernel smoothing
(12-day bandwidth) of the listed polls, drawn only where polls exist.

The New York Times runs its own tracker with a different poll set and method:
<https://www.nytimes.com/interactive/polls/senate-polls-2026.html>.

## Updating

1. Edit `data/senate-polls-2026.json` (add polls to a race's `polls` array; update `average` and
   `generated`).
2. `python3 build.py` regenerates `index.html` from `src/page.html` and the data file.
3. Commit and push; GitHub Pages redeploys.

`scripts/merge_sources.py` is the one-off script that merged the extracted RCP race files and the
seat roster into the data file.

## Layout

```
index.html                 generated page (commit this; Pages serves it)
src/page.html              page source: styles, markup, script, __DATA__ placeholder
data/senate-polls-2026.json
build.py
scripts/merge_sources.py
```
