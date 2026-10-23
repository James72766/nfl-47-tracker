"""BetMGM snapshot robot for the 47 Rule Tracker.

Runs on GitHub's schedule (see .github/workflows/collect.yml). Each run asks
The Odds API for BetMGM's NFL totals (1 credit) and saves a row in
data/betmgm-lines.csv for every game that hasn't kicked off yet, changed or not.
So the first row for a game is the first number we saw (the opener), and a game
missing from the latest check means BetMGM has taken it off the board.

Every run, changed or not, is logged in data/robot-runs.csv so gaps show up.

Phone alerts go to the ntfy app (topic in the NTFY_TOPIC secret): one when new
openers appear, one when a line crosses 47 and flips the bet.

For open-air games it also saves the wind forecast for the kickoff hour
(Open-Meteo, free) to data/wind-forecasts.csv, so the 10–15 mph wind idea can
later be tested on what the forecast said when you'd bet, not kickoff wind.

And it checks two research rules (bad defenses → under, low-scoring team →
over) on every game, saving the result and the stats behind it to
data/research-flags.csv. The page grades each rule from the last check before
kickoff, so a flag is never rebuilt afterward from updated data.
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
WIND = os.path.join(HERE, "data", "wind-forecasts.csv")
WIND_COLS = ["seen_at_utc", "event_id", "kickoff_utc", "away", "home", "stadium_id",
             "forecast_hour_utc", "wind_mph", "gust_mph", "temp_f"]
GAMES_CSV = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
FLAGS = os.path.join(HERE, "data", "research-flags.csv")
FLAG_COLS = ["seen_at_utc", "event_id", "kickoff_utc", "away", "home", "total", "over_price",
             "under_price", "ref_spread_line", "home_prior_games", "home_pf", "home_pa",
             "away_prior_games", "away_pf", "away_pa", "r2_bad_def_under", "r2_4plus",
             "r3_low_scoring_over", "r3_teams", "r3_4plus"]
# Open-air stadiums (nflverse ids) and where they are. Domes and retractable
# roofs are skipped. Overseas grounds are open-air even where nflverse says dome.
OPEN_AIR = {
    "BAL00": (39.278, -76.623), "BOS00": (42.091, -71.264), "BUF00": (42.774, -78.787),
    "CAR00": (35.226, -80.853), "CHI98": (41.862, -87.617), "CIN00": (39.096, -84.516),
    "CLE00": (41.506, -81.700), "DEN00": (39.744, -105.020), "GNB00": (44.501, -88.062),
    "JAX00": (30.324, -81.637), "KAN00": (39.049, -94.484), "MIA00": (25.958, -80.239),
    "NAS00": (36.167, -86.771), "NYC01": (40.814, -74.075), "PHI00": (39.901, -75.168),
    "PIT00": (40.447, -80.016), "SEA00": (47.595, -122.332), "SFO01": (37.403, -121.970),
    "TAM00": (27.976, -82.503), "WAS00": (38.908, -76.865), "LON00": (51.556, -0.280),
    "LON02": (51.604, -0.066), "GER00": (48.219, 11.625), "MUN01": (48.219, 11.625),
    "PAR00": (48.925, 2.360), "MEL00": (-37.820, 144.983), "MEX00": (19.303, -99.151),
    "MAD01": (40.453, -3.688), "SAO00": (-23.545, -46.474), "RIO00": (-22.912, -43.230),
}
EASTERN = ZoneInfo("America/New_York")
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
    # Weather and research flags are extras; a failure here must never cost the odds snapshot.
    try:
        sched = schedule()
    except Exception as e:
        print(f"nflverse schedule not loaded, wind and flags skipped: {e}")
        return
    try:
        wind = wind_rows(rows, now, sched)
        if wind:
            append_rows(WIND, WIND_COLS, wind)
        print(f"wind forecasts saved for {len(wind)} open-air games")
    except Exception as e:
        print(f"wind forecasts not saved: {e}")
    try:
        flags = flag_rows(rows, sched)
        if flags:
            append_rows(FLAGS, FLAG_COLS, flags)
        print(f"research flags checked for {len(flags)} games "
              f"({sum(int(f['r2_bad_def_under']) for f in flags)} bad-defense, "
              f"{sum(int(f['r3_low_scoring_over']) for f in flags)} low-scoring)")
    except Exception as e:
        print(f"research flags not saved: {e}")


def schedule():
    """The nflverse schedule and results, each game with its kickoff as a datetime."""
    with urllib.request.urlopen(GAMES_CSV, timeout=60) as resp:
        text = resp.read().decode()
    games = []
    for g in csv.DictReader(text.splitlines()):
        g = {k: v.strip('"') for k, v in g.items()}
        if g["gameday"] and g["gametime"]:
            g["kick"] = datetime.fromisoformat(f"{g['gameday']}T{g['gametime']}").replace(tzinfo=EASTERN)
            games.append(g)
    return games


def match(sched, r):
    """The nflverse game for one of our BetMGM rows (same two teams, kickoff within 36 hours)."""
    kick = datetime.fromisoformat(r["kickoff_utc"].replace("Z", "+00:00"))
    pair = {r["away"], r["home"]}
    hits = [g for g in sched if {g["away_team"], g["home_team"]} == pair
            and abs((g["kick"] - kick).total_seconds()) < 36 * 3600]
    return hits[0] if hits else None


def pregame(sched, g, team):
    """Team's current-season regular-season games before this one: (count, points for/game, against/game)."""
    pf = pa = n = 0
    for h in sched:
        if (h["season"] == g["season"] and h["game_type"] == "REG" and h["home_score"] != ""
                and h["gameday"] < g["gameday"] and team in (h["home_team"], h["away_team"])):
            home = h["home_team"] == team
            pf += int(h["home_score" if home else "away_score"])
            pa += int(h["away_score" if home else "home_score"])
            n += 1
    return n, (pf / n if n else None), (pa / n if n else None)


def flag_rows(rows, sched):
    """Check the two research rules for every game in this check (picks never change).

    Rule 2 (bad defenses): BetMGM total ≤ 45 and both teams allowing ≥ 26 a game → UNDER.
    Rule 3 (low-scoring team): a team scoring < 14 a game that isn't an underdog of
    more than 8 (nflverse reference spread) → OVER. Averages are unrounded, current
    season, regular season, before kickoff. "4plus" = every team the rule leans on
    has at least 4 prior games."""
    out = []
    for r in rows:
        g = match(sched, r)
        if not g:
            continue
        stats = {t: pregame(sched, g, t) for t in (r["home"], r["away"])}
        (hn, hpf, hpa), (an, apf, apa) = stats[r["home"]], stats[r["away"]]
        r2 = (float(r["total"]) <= 45 and hn >= 1 and an >= 1 and hpa >= 26 and apa >= 26)
        spread = g["spread_line"]
        r3_teams = []
        if spread != "":
            for t in (r["home"], r["away"]):
                n, pf, _ = stats[t]
                team_spread = -float(spread) if t == r["home"] else float(spread)
                if n >= 1 and pf < 14 and team_spread <= 8:
                    r3_teams.append(t)
        fmt = lambda x: "" if x is None else f"{x:.4f}"
        out.append({
            "seen_at_utc": r["seen_at_utc"], "event_id": r["event_id"], "kickoff_utc": r["kickoff_utc"],
            "away": r["away"], "home": r["home"], "total": r["total"],
            "over_price": r["over_price"], "under_price": r["under_price"], "ref_spread_line": spread,
            "home_prior_games": hn, "home_pf": fmt(hpf), "home_pa": fmt(hpa),
            "away_prior_games": an, "away_pf": fmt(apf), "away_pa": fmt(apa),
            "r2_bad_def_under": int(r2), "r2_4plus": int(r2 and hn >= 4 and an >= 4),
            "r3_low_scoring_over": int(bool(r3_teams)), "r3_teams": "+".join(r3_teams),
            "r3_4plus": int(any(stats[t][0] >= 4 for t in r3_teams)),
        })
    return out


def stadiums(sched):
    """Each game's stadium: {(team, team): [(kickoff, id)]}."""
    out = {}
    for g in sched:
        out.setdefault(frozenset((g["away_team"], g["home_team"])), []).append((g["kick"], g["stadium_id"]))
    return out


def wind_rows(rows, now, sched):
    """Forecast wind at the kickoff hour for each open-air game in this check."""
    sched = stadiums(sched)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for r in rows:
        kick = datetime.fromisoformat(r["kickoff_utc"].replace("Z", "+00:00"))
        if (kick - now).days > 15:  # past the forecast range
            continue
        match = [sid for k, sid in sched.get(frozenset((r["away"], r["home"])), [])
                 if abs((k - kick).total_seconds()) < 36 * 3600]
        if not match or match[0] not in OPEN_AIR:
            continue
        lat, lon = OPEN_AIR[match[0]]
        hour = datetime.fromtimestamp(round(kick.timestamp() / 3600) * 3600, timezone.utc)
        day = hour.strftime("%Y-%m-%d")
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
               "&hourly=wind_speed_10m,wind_gusts_10m,temperature_2m&wind_speed_unit=mph"
               f"&temperature_unit=fahrenheit&timezone=UTC&start_date={day}&end_date={day}")
        with urllib.request.urlopen(url, timeout=20) as resp:
            h = json.load(resp)["hourly"]
        key = hour.strftime("%Y-%m-%dT%H:00")
        if key not in h["time"]:
            continue
        i = h["time"].index(key)
        out.append({"seen_at_utc": stamp, "event_id": r["event_id"], "kickoff_utc": r["kickoff_utc"],
                    "away": r["away"], "home": r["home"], "stadium_id": match[0],
                    "forecast_hour_utc": key, "wind_mph": round(h["wind_speed_10m"][i]),
                    "gust_mph": round(h["wind_gusts_10m"][i]), "temp_f": round(h["temperature_2m"][i])})
    return out


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
