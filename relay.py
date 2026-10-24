"""Picks the wait before the robot's next run (the relay).

GitHub's own schedule skips and delays runs, so each run books the next one
itself: it starts a new run that sits behind a GitHub "wait timer" (an
environment named wait-0 ... wait-120, in 10-minute steps) and then takes the
snapshot. Waiting this way costs nothing and needs no outside service.

Prints the environment name for the next run, e.g. "wait-110".
"""
from datetime import datetime, timedelta, timezone


def slots(day):
    """The robot's check times (UTC) on one day: every 2 hours at :10, 15:10
    for the 8am-Vegas check after the clocks change, and extra Sunday-night
    checks (23:10 Sun, 01:10/03:10/05:10 Mon) when openers come out."""
    hours = set(range(0, 24, 2)) | {15}
    if day.weekday() == 6:      # Sunday
        hours.add(23)
    if day.weekday() == 0:      # Monday
        hours |= {1, 3, 5}
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return [base + timedelta(hours=h, minutes=10) for h in sorted(hours)]


def next_wait(now):
    # The slot this run belongs to: anything within 25 minutes either side.
    times = [t for d in (-1, 0, 1, 2) for t in slots(now + timedelta(days=d))]
    current = [t for t in times if abs((t - now).total_seconds()) <= 25 * 60]
    after = max([now] + [t + timedelta(minutes=1) for t in current])
    nxt = min(t for t in times if t > after)
    minutes = int((nxt - now).total_seconds() // 60)
    return f"wait-{min(120, max(0, minutes // 10 * 10))}"


if __name__ == "__main__":
    print(next_wait(datetime.now(timezone.utc)))
