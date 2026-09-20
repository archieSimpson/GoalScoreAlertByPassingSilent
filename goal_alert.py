#!/usr/bin/env python3
"""
Goal Alert -> Pushover

Watches live football (soccer) scores for one team using the API-Football
(api-sports.io) API. The moment that team scores, it fires an
EMERGENCY-priority Pushover push -- on an iPhone with Pushover's "Critical
Alerts" turned on, that plays sound through silent mode, Focus and Do Not
Disturb; on Android, Pushover asks for Do-Not-Disturb-override permission
and does the same thing. If the OPPOSITION scores, you still get told --
just as a normal-priority push that behaves like any other notification
(respects silent mode/DND, no repeat, no siren), so you're kept in the
loop without a full alarm going off for the other team's goal.

To stretch the free 100-requests/day API-Football quota, this checks
slowly (every POLL_SECONDS_WAITING) before the match has kicked off, and
only switches to fast checking (every POLL_SECONDS_LIVE) once it detects
the match is actually live -- so starting the workflow early doesn't burn
through your daily quota.

HOW TO USE
----------
1. Fill in the three key/token values in the CONFIG section below (these
   stay the same every week -- when run from GitHub Actions they instead
   come from GitHub Secrets, and these fallback values are ignored).
2. Install the one dependency this script needs:
       pip install requests
3. Each week, pick the team by passing it as an argument:
       python3 goal_alert.py "Arsenal"
   (or set the TEAM_NAME environment variable instead -- either works;
   if you give neither, it falls back to DEFAULT_TEAM_NAME below.)
4. It checks for a live match every POLL_SECONDS_WAITING until kickoff,
   then every POLL_SECONDS_LIVE once the match is live, comparing the
   score each time. A goal for your team fires an Emergency Pushover
   alert (breaks through silent/DND); a goal for the opposition fires a
   normal-priority one (a regular notification). It automatically stops
   once the match is finished (or after MAX_RUNTIME_HOURS, as a safety
   net, if no match ever starts).

You do not need to look anything up yourself (like a team ID) -- the
script resolves the team's ID from the name you give it.
"""

import os
import sys
import time
from datetime import datetime, timedelta

import requests

# ----------------------------- CONFIG -------------------------------------

# Used only if you don't pass a team name as an argument or TEAM_NAME
# environment variable (see "HOW TO USE" above).
DEFAULT_TEAM_NAME = "Arsenal"

# From https://dashboard.api-football.com (free account -> "My Access" tab).
# Can also be supplied via the API_FOOTBALL_KEY environment variable
# (that's what GitHub Actions will do, via a Secret).
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "PASTE_YOUR_API_FOOTBALL_KEY_HERE")

# From the Pushover app / https://pushover.net/apps/build (create an
# "Application/API Token" for this script) and your Pushover dashboard
# (your personal "User Key" at the top of https://pushover.net). Can also
# be supplied via PUSHOVER_APP_TOKEN / PUSHOVER_USER_KEY environment
# variables (again, that's what GitHub Actions will do).
PUSHOVER_APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "PASTE_YOUR_PUSHOVER_APP_TOKEN_HERE")
PUSHOVER_USER_KEY = os.environ.get("PUSHOVER_USER_KEY", "PASTE_YOUR_PUSHOVER_USER_KEY_HERE")

# How often to check while WAITING for the match to kick off, in seconds.
# Slow on purpose -- nothing is happening yet, so there's no reason to
# spend API calls checking often. This is what lets you start the workflow
# well before kickoff without eating into your daily quota.
POLL_SECONDS_WAITING = 300

# How often to check once the match is confirmed LIVE, in seconds. 60-90 is
# a good balance: fast enough to feel instant, gentle enough to stay well
# inside the free API-Football quota of 100 requests/day (at 75s, a full
# ~120-minute match uses roughly 95 requests).
POLL_SECONDS_LIVE = 75

# If no live match for the chosen team is found within this many hours of
# starting the script, it gives up and exits (so you don't accidentally
# leave it running for days). Restart it closer to kickoff if this happens.
MAX_RUNTIME_HOURS = 4

# ----------------------------------------------------------------------


def resolve_team_name():
    if len(sys.argv) > 1 and sys.argv[1] != "--test":
        return sys.argv[1]
    return os.environ.get("TEAM_NAME", DEFAULT_TEAM_NAME)

API_BASE = "https://v3.football.api-sports.io"


def api_get(path, params=None):
    resp = requests.get(
        f"{API_BASE}/{path}",
        headers={"x-apisports-key": API_FOOTBALL_KEY},
        params=params or {},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def find_team_id(name):
    data = api_get("teams", {"search": name})
    results = data.get("response", [])
    if not results:
        sys.exit(f'No team found matching "{name}". Try a different spelling.')
    team = results[0]["team"]
    print(f'Matched "{name}" -> {team["name"]} (id {team["id"]}, {team.get("country")})')
    if len(results) > 1:
        print("  (Other matches found too -- if this is the wrong team, "
              "make TEAM_NAME more specific, e.g. add the country.)")
    return team["id"], team["name"]


def find_live_fixture(team_id):
    data = api_get("fixtures", {"team": team_id, "live": "all"})
    fixtures = data.get("response", [])
    return fixtures[0] if fixtures else None


def _send_pushover(title, message, emergency):
    data = {
        "token": PUSHOVER_APP_TOKEN,
        "user": PUSHOVER_USER_KEY,
        "title": title,
        "message": message,
    }
    if emergency:
        data["priority"] = 2   # Emergency -- required for Critical Alerts / DND override
        data["retry"] = 60     # resend every 60s until acknowledged...
        data["expire"] = 3600  # ...for up to an hour
        data["sound"] = "siren"
    else:
        data["priority"] = 0   # Normal -- a regular notification, respects silent/DND
    resp = requests.post("https://api.pushover.net/1/messages.json", data=data, timeout=15)
    ok = resp.status_code == 200 and resp.json().get("status") == 1
    kind = "Emergency" if emergency else "normal"
    print(f"  -> Pushover {kind} alert sent: {'OK' if ok else 'FAILED: ' + resp.text}")


def send_pushover_goal_alert(team_display_name, home_name, away_name, home_goals, away_goals):
    message = f"{home_name} {home_goals} - {away_goals} {away_name}"
    _send_pushover(f"GOAL! {team_display_name}", message, emergency=True)


def send_pushover_opposition_alert(team_display_name, home_name, away_name, home_goals, away_goals):
    message = f"{home_name} {home_goals} - {away_goals} {away_name}"
    _send_pushover(f"Opposition goal ({team_display_name} match)", message, emergency=False)


def send_pushover_test():
    _send_pushover(
        "Goal Alert: test",
        "If your phone just made noise through silent mode, it's working.",
        emergency=True,
    )


def main():
    if "PASTE_YOUR" in API_FOOTBALL_KEY or "PASTE_YOUR" in PUSHOVER_APP_TOKEN or "PASTE_YOUR" in PUSHOVER_USER_KEY:
        sys.exit("Fill in API_FOOTBALL_KEY, PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY "
                  "at the top of this file first.")

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        send_pushover_test()
        return

    team_name = resolve_team_name()
    print(f"Selected team for this run: {team_name}")
    team_id, team_display_name = find_team_id(team_name)

    deadline = datetime.now() + timedelta(hours=MAX_RUNTIME_HOURS)
    last_home_goals = None
    last_away_goals = None
    match_seen = False

    print(f"Watching for a live match... (checking every {POLL_SECONDS_WAITING}s until kickoff, "
          f"then every {POLL_SECONDS_LIVE}s while live; giving up after {MAX_RUNTIME_HOURS}h "
          f"if nothing starts)")

    while datetime.now() < deadline:
        try:
            fixture = find_live_fixture(team_id)
        except requests.RequestException as e:
            print(f"  (network hiccup, will retry: {e})")
            time.sleep(POLL_SECONDS_WAITING if not match_seen else POLL_SECONDS_LIVE)
            continue

        if fixture is None:
            if match_seen:
                print("Match no longer live -- assuming it finished. Done.")
                return
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] no live match yet...")
            time.sleep(POLL_SECONDS_WAITING)
            continue

        match_seen = True
        home = fixture["teams"]["home"]
        away = fixture["teams"]["away"]
        home_goals = fixture["goals"]["home"] or 0
        away_goals = fixture["goals"]["away"] or 0
        status = fixture["fixture"]["status"]["short"]

        if last_home_goals is None:
            print(f"Live: {home['name']} {home_goals} - {away_goals} {away['name']} "
                  f"(status {status}). Watching for goals...")
            last_home_goals, last_away_goals = home_goals, away_goals
        else:
            our_side_scored = (
                (home["id"] == team_id and home_goals > last_home_goals) or
                (away["id"] == team_id and away_goals > last_away_goals)
            )
            if home_goals > last_home_goals or away_goals > last_away_goals:
                print(f"  SCORE CHANGE: {home['name']} {home_goals} - {away_goals} {away['name']}")
                if our_side_scored:
                    send_pushover_goal_alert(team_display_name, home["name"], away["name"],
                                              home_goals, away_goals)
                else:
                    send_pushover_opposition_alert(team_display_name, home["name"], away["name"],
                                                     home_goals, away_goals)
            last_home_goals, last_away_goals = home_goals, away_goals

        if status in ("FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"):
            print(f"Match ended (status {status}). Final: "
                  f"{home['name']} {home_goals} - {away_goals} {away['name']}. Done.")
            return

        time.sleep(POLL_SECONDS_LIVE)

    print("Gave up waiting for a live match (MAX_RUNTIME_HOURS reached). "
          "Restart the script closer to kickoff.")


if __name__ == "__main__":
    main()
