# 47 Rule Tracker

James's NFL totals tracker. Line 47 or higher: over. Under 47: under.

- Live page: https://jd-47-rule.netlify.app (Netlify site id c099db6e-9e6b-498d-b35d-36997d13135d).
  GitHub Pages was switched off 2026-10-23 so there's only one link.
- To update the page: copy `index.html` alone into an empty folder, then
  `netlify deploy --prod --dir <that folder> --site c099db6e-9e6b-498d-b35d-36997d13135d`.
  Only the page goes to Netlify; it reads the robot's data straight from this GitHub repo.
- `index.html` is the page. Scores and season lines come from the free nflverse file.
- `collect.py` is the robot. GitHub runs it on the schedule in `.github/workflows/collect.yml`
  and it saves BetMGM's totals to `data/betmgm-lines.csv` (every pregame game, every check).
- `data/robot-runs.csv` logs every run and how many Odds API credits are left (free plan: 500 a month).
- The Odds API key (`ODDS_API_KEY`) and the phone-alert channel (`NTFY_TOPIC`) live in the
  GitHub project's secrets, never in these files.
- The robot pushes to GitHub every few hours, so always `git pull` before editing here.
- `_47-Rule-Tracker-Overview.md` (local only) is the full write-up for outside review.
