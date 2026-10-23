"""BetMGM snapshot robot for the 47 Rule Tracker.

Runs on GitHub's schedule (see .github/workflows/collect.yml). Each run asks
The Odds API for BetMGM's NFL totals (1 credit) and saves a row in
data/betmgm-lines.csv for every game that hasn't kicked off yet, changed or not.
So the first row for a game is the first number we saw (the opener), and a game
missing from the latest check means BetMGM has taken it off the board.

Every run, changed or not, is logged in data/robot-runs.csv so gaps show up.

Phone alerts go to the ntfy app (topic in the NTFY_TOPIC secret): one when new
openers appear, one when a line crosses 47 and flips the bet.
"""
import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

URL = ("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
       "?regions=us&markets=totals&bookmakers=betmgm&oddsFormat=american&apiKey=")
HERE = os.path.dirname(os.path.abspath(__file__))
LINES = os.path.join(HERE, "data", "betmgm-lines.csv")
RUNS = os.path.join(HERE, "data", "robot-runs.csv")
LINE_COLS = ["seen_at_utc", "event_id", "kickoff_utc", "away", "home",
             "total", "over_price", "under_price", "book_updated_utc"]
RUN_COLS = ["run_at_utc", "games_with_lines", "rows_added", "credits_left"]
THRESHOLD = 47
VEGAS = ZoneInfo("America/Los_Angeles")

# The Odds API uses full names; the nflverse score file uses these codes.
TEAM = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LA", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def append_rows(path, cols, rows):
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new_file:
            w.writeheader()
        w.writerows(rows)


def bet(total):
    total = float(total)
    return f"{'OVER' if total >= THRESHOLD else 'UNDER'} {total:g}"


def kickoff(row):
    t = datetime.fromisoformat(row["kickoff_utc"].replace("Z", "+00:00")).astimezone(VEGAS)
    return t.strftime("%a %-m/%-d %-I:%M%p").replace(":00", "").lower().capitalize()


def alerts_for(last, added):
    """Build (title, message) alerts from this run's new rows.

    `last` is each game's previous row (empty on the robot's first-ever run,
    which never alerts: those lines were already up, not fresh openers)."""
    if not last:
        return []
    alerts = []
    openers = [r for r in added if r["event_id"] not in last]
    if openers:
        openers.sort(key=lambda r: r["kickoff_utc"])
        alerts.append((
            f"BetMGM openers: {len(openers)} new game{'s' if len(openers) > 1 else ''}",
            "\n".join(f"{bet(r['total'])}  {r['away']} @ {r['home']}  {kickoff(r)}" for r in openers),
        ))
    for r in added:
        prev = last.get(r["event_id"])
        if prev and (float(prev["total"]) >= THRESHOLD) != (float(r["total"]) >= THRESHOLD):
            alerts.append((
                f"Rule flipped: {r['away']} @ {r['home']} now says {bet(r['total']).split()[0]}",
                f"BetMGM moved {float(prev['total']):g} → {float(r['total']):g}. "
                f"Rule now says {bet(r['total'])}  ({kickoff(r)}). "
                f"This doesn't change a bet you already placed.",
            ))
    return alerts


def send(title, message):
    print(f"ALERT: {title}\n{message}")
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=message.encode(),
                                 headers={"Title": title, "Tags": "football"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # a failed alert must never stop the snapshot being saved
        print(f"alert not sent: {e}")


def main():
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        sys.exit("ODDS_API_KEY is not set")
    with urllib.request.urlopen(URL + key, timeout=30) as resp:
        games = json.load(resp)
        credits_left = resp.headers.get("x-requests-remaining", "")

    now = datetime.now(timezone.utc)
    last = {}
    for r in read_rows(LINES):
        last[r["event_id"]] = r

    rows = pregame_rows(games, now)
    # Lines that moved (or are new) since the last check drive the alerts.
    moved = [r for r in rows if not same_line(last.get(r["event_id"]), r)]

    os.makedirs(os.path.dirname(LINES), exist_ok=True)
    if rows:
        append_rows(LINES, LINE_COLS, rows)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    append_rows(RUNS, RUN_COLS, [{"run_at_utc": stamp, "games_with_lines": len(rows),
                                  "rows_added": len(rows), "credits_left": credits_left}])
    print(f"{stamp}: {len(rows)} pregame games with BetMGM totals, {len(moved)} moved or new, "
          f"{credits_left} credits left")
    for title, message in alerts_for(last, moved):
        send(title, message)


def same_line(prev, row):
    return bool(prev) and all(str(prev[k]) == str(row[k])
                              for k in ("total", "over_price", "under_price", "kickoff_utc"))


def pregame_rows(games, now):
    """One row per game BetMGM has a clean pregame total for, saved every check.

    Games already kicked off are skipped (their live totals would look like
    line moves), and so is any market whose over and under totals disagree."""
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for g in games:
        kick = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
        if kick <= now:
            continue
        book = next((b for b in g.get("bookmakers", []) if b["key"] == "betmgm"), None)
        market = book and next((m for m in book["markets"] if m["key"] == "totals"), None)
        if not market:
            continue
        sides = {o["name"]: o for o in market["outcomes"]}
        if "Over" not in sides or "Under" not in sides:
            continue
        if sides["Over"].get("point") is None or sides["Over"]["point"] != sides["Under"].get("point"):
            continue
        rows.append({
            "seen_at_utc": stamp,
            "event_id": g["id"],
            "kickoff_utc": g["commence_time"],
            "away": TEAM.get(g["away_team"], g["away_team"]),
            "home": TEAM.get(g["home_team"], g["home_team"]),
            "total": sides["Over"]["point"],
            "over_price": sides["Over"]["price"],
            "under_price": sides["Under"]["price"],
            "book_updated_utc": book.get("last_update", ""),
        })
    return rows


if __name__ == "__main__":
    main()
