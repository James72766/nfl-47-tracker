# 47 Rule Tracker

James's NFL totals tracker. Line 47 or higher: over. Under 47: under.

- Live page: https://james72766.github.io/nfl-47-tracker/
- `index.html` is the page. Scores and season lines come from the free nflverse file.
- `collect.py` is the robot. GitHub runs it on the schedule in `.github/workflows/collect.yml`
  and it saves BetMGM's totals to `data/betmgm-lines.csv` (a new row only when the line moves).
- `data/robot-runs.csv` logs every run and how many Odds API credits are left (free plan: 500 a month).
- The Odds API key lives in the GitHub project's secrets as `ODDS_API_KEY`, never in these files.
- The robot pushes to GitHub every few hours, so always `git pull` before editing here.
