"""
Kallang Tennis Hub court-release monitor.

Polls the (undocumented) FacilityBooking calendar API, compares the current
availability against the last saved snapshot, and sends a Telegram message
for every (date, hour) slot that has newly become "Bookable" since the last
run — i.e. a court that was reserved/blocked and has just been released.

Required environment variables (set as GitHub Actions secrets):
    KALLANG_BEARER_TOKEN  - Authorization bearer token from a logged-in
                             browser session on thekallang.perfectgym.com
    TELEGRAM_BOT_TOKEN    - token for your Telegram bot (from @BotFather)
    TELEGRAM_CHAT_ID      - chat id the bot should message
"""

import os
import sys
import json
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

KALLANG_BASE = "https://thekallang.perfectgym.com/clientportal2"
ENDPOINT = f"{KALLANG_BASE}/FacilityBookings/FacilityCalendar/GetWeeklySchedule"

CLUB_ID = 1

# label -> zoneTypeId, add more here if you ever want to watch another facility type
ZONE_TYPES = {
    "outdoor": 83,
    "indoor": 85,
}

WINDOW_DAYS = 7   # you can only book 7 days ahead, so only watch today .. today+7
DAYS_AHEAD = 7    # scan range (2 API calls per zone type cover it)
STEP_DAYS = 6     # each API call returns about a 6-day window

SG = ZoneInfo("Asia/Singapore")       # GitHub runners use UTC; Kallang uses SG time
BOOKING_ADVANCE = timedelta(days=6)   # a slot opens for booking this long before it starts
LAG_BUFFER = timedelta(minutes=45)    # tolerance for late scheduler runs

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")

BEARER_TOKEN = os.environ["KALLANG_BEARER_TOKEN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def telegram_send(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=15)
        if r.status_code != 200:
            print(f"Telegram send failed: {r.status_code} {r.text}")
    except Exception as e:
        print(f"Telegram send error: {e}")


def fetch_window(zone_type_id: int, start_date: date) -> dict:
    headers = {
        "CP-LANG": "en",
        "CP-MODE": "desktop",
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "Referer": f"{KALLANG_BASE}/",
        "X-Hash": f"#/FacilityBooking?clubId={CLUB_ID}&zoneTypeId={zone_type_id}&date={start_date.isoformat()}",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    }
    payload = {
        "clubId": CLUB_ID,
        "date": start_date.isoformat(),
        "zoneTypeId": zone_type_id,
        "zoneId": None,
        "slots": None,
        "daysInWeek": 4,
    }

    resp = requests.post(ENDPOINT, headers=headers, json=payload, timeout=30)

    if resp.status_code in (401, 403):
        telegram_send(
            "⚠️ Kallang monitor: the login session has expired (got "
            f"HTTP {resp.status_code}).\n\n"
            "Please log in to thekallang.perfectgym.com again on the wife's "
            "account, grab a fresh Authorization bearer token from DevTools, "
            "and update the KALLANG_BEARER_TOKEN secret in the GitHub repo."
        )
        sys.exit(1)

    resp.raise_for_status()
    return resp.json()


def extract_availability(data: dict) -> dict:
    """Returns {date_str: [hour_str, ...]} for every slot currently Bookable."""
    days = data["PagerData"]["Days"]
    # record every day we saw (even with no free slots) so we can tell a day
    # that is genuinely full apart from a day that just entered the window
    avail: dict[str, set[str]] = {d: set() for d in days}
    for hour_block in data["CalendarData"]:
        hour_str = hour_block["Hour"][11:16]  # "HH:MM" from "0001-01-01THH:MM:00"
        for day_idx, day_str in enumerate(days):
            if day_idx >= len(hour_block["ClassesPerDay"]):
                continue
            for slot in hour_block["ClassesPerDay"][day_idx]:
                if slot.get("Status") == "Bookable":
                    avail.setdefault(day_str, set()).add(hour_str)
    return {d: sorted(h) for d, h in avail.items()}


def scan_zone(zone_type_id: int) -> dict:
    merged: dict[str, set[str]] = {}
    today = datetime.now(SG).date()
    cursor = today
    while (cursor - today).days < DAYS_AHEAD:
        data = fetch_window(zone_type_id, cursor)
        chunk = extract_availability(data)
        for d, hours in chunk.items():
            merged.setdefault(d, set()).update(hours)
        cursor = cursor + timedelta(days=STEP_DAYS)
        time.sleep(1)  # be polite to their server
    last_day = (today + timedelta(days=WINDOW_DAYS)).isoformat()
    return {d: sorted(h) for d, h in merged.items() if today.isoformat() <= d <= last_day}


def load_state(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def is_real_release(day: str, hour: str) -> bool:
    """True if this slot's normal opening time has already passed.

    Slots open hour by hour, so a slot at the far edge of the window that
    just appeared is routine rollout, not a quietly released court.
    """
    start = datetime.fromisoformat(f"{day}T{hour}:00").replace(tzinfo=SG)
    return start < datetime.now(SG) + BOOKING_ADVANCE - LAG_BUFFER


def diff_newly_available(old: dict, new: dict) -> list[tuple[str, str]]:
    newly = []
    for day, hours in new.items():
        if day not in old:
            # day just rolled into the 7-day window (new day opens each morning)
            # -> not a "release", skip it
            continue
        old_hours = set(old.get(day, []))
        for h in hours:
            if h not in old_hours and is_real_release(day, h):
                newly.append((day, h))
    newly.sort()
    return newly


def main() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    message_lines = []

    for label, zone_type_id in ZONE_TYPES.items():
        state_path = os.path.join(STATE_DIR, f"{label}.json")
        old_state = load_state(state_path)
        is_first_run = not old_state  # nothing to compare against yet
        new_state = scan_zone(zone_type_id)

        newly_available = [] if is_first_run else diff_newly_available(old_state, new_state)
        if newly_available:
            message_lines.append(f"🎾 {label.capitalize()} court(s) just opened up:")
            for day, hour in newly_available:
                message_lines.append(f"  • {day} at {hour}")

        save_state(state_path, new_state)

    if message_lines:
        text = "\n".join(message_lines) + "\n\nBook now: " + KALLANG_BASE + "/#/FacilityBooking"
        telegram_send(text)
        print("Sent alert:\n" + text)
    else:
        print("No new slots this run.")


if __name__ == "__main__":
    main()
