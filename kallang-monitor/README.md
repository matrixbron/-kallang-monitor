# Kallang Tennis Hub Court Monitor

Checks the outdoor (zoneTypeId 83) and indoor (zoneTypeId 85) court calendars
every 15 minutes and sends you a Telegram message the moment a slot flips
from unavailable to "Bookable" — catching courts that get quietly released
after an association reservation falls through.

It uses your **wife's** login session (bearer token) purely to read the
calendar — it never books anything.

---

## 1. Create the Telegram bot

1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts.
2. It gives you a token like `123456789:AAH...` — save this, it's your
   `TELEGRAM_BOT_TOKEN`.
3. Send your new bot **any message** first (e.g. "hi") so it's allowed to
   message you back.
4. Find your chat ID: message **@userinfobot** (a different bot) and it
   will reply with your numeric Telegram ID — that's `TELEGRAM_CHAT_ID`.

## 2. Create the GitHub repo

1. On github.com, create a new **private** repository, e.g. `kallang-monitor`.
2. Upload every file in this folder, keeping the same structure:
   ```
   .github/workflows/monitor.yml
   scripts/monitor.py
   state/outdoor.json
   state/indoor.json
   requirements.txt
   README.md
   ```
   (Easiest way: on the repo page, "Add file" → "Upload files", drag the
   whole folder in.)

## 3. Add the secrets

In the repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add three:

| Name | Value |
|---|---|
| `KALLANG_BEARER_TOKEN` | see step 4 below |
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | from userinfobot |

## 4. Get the Kallang bearer token (on your wife's logged-in session)

1. On a desktop browser, log in to `thekallang.perfectgym.com/clientportal2`
   on her account.
2. Open DevTools (F12) → **Network** tab → filter **Fetch/XHR**.
3. Click into any date on the booking calendar so a request fires.
4. Click any request named `GetWeeklySchedule` → **Headers** tab → find
   `Authorization: Bearer eyJ...` under Request Headers.
5. Copy everything **after** `Bearer ` (the long `eyJ...` string) — that's
   the secret value for `KALLANG_BEARER_TOKEN`.

This token is a JWT with an expiry baked in — when it expires, the bot will
automatically message you on Telegram asking for a fresh one. Just repeat
step 4 and update the secret when that happens.

## 5. Turn it on

Go to the **Actions** tab in the repo → you should see "Kallang Tennis
Court Monitor" → click **Run workflow** once manually to do the first
(silent) baseline run. After that it runs automatically every 15 minutes.

## Notes / tuning

- `DAYS_AHEAD` in `scripts/monitor.py` controls how far forward it scans
  (default 21 days). Increase if you want more lead time on association
  releases that show up further out.
- The schedule is every 15 minutes, but GitHub's free-tier scheduler can
  delay runs under load — don't expect second-precision.
- If you ever want to watch a different facility type, add its
  `zoneTypeId` to the `ZONE_TYPES` dict in `monitor.py` (find it in the
  page URL the same way you found 83/85).
