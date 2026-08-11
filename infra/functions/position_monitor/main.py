"""Position-monitor Cloud Function -- runs every 10 minutes during market
hours (see infra/terraform/scheduler.tf).

Trade entry is manual: you add a row to the Sheet yourself (status="open")
once you've actually placed a trade -- this function never creates rows, it
only advances existing "open" ones:

  open          -> pending_close  once profit target / stop loss / imminent
                                   expiration is detected (posts a new Discord
                                   message asking you to close, with two
                                   reaction options)
  pending_close -> closed         once you react (checkmark) on that message
  pending_close -> open           once you react (no-entry sign) instead,
                                   i.e. "not now" -- will re-alert on a later
                                   run if the same condition still holds,
                                   there's no snooze/cooldown here

Nothing here places or closes a real trade -- every state transition still
requires your own manual reaction. This function only decides *when to ask*
and records what you've confirmed.
"""
import os
from datetime import date, timedelta

import requests
import yfinance as yf
import google.auth
from googleapiclient.discovery import build

import functions_framework

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "")
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID", "")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
PROFIT_TARGET_PCT_DEFAULT = os.environ.get("PROFIT_TARGET_PCT", "50")
STOP_LOSS_MULTIPLE_DEFAULT = os.environ.get("STOP_LOSS_MULTIPLE", "2.0")

CLOSE_EMOJI = "%E2%9C%85"  # URL-encoded ✅ -- "I closed this"
DISMISS_EMOJI = "%F0%9F%9A%AB"  # URL-encoded 🚫 -- "not now"

# strategy (the exact string you type into the Sheet's strategy column) ->
# (option_side, legs). The only 4 values position_monitor understands --
# anything else is a hard error for that row (caught and logged, not
# silently mis-priced against the wrong side of the option chain).
STRATEGY_INFO = {
    "cash_secured_put": ("puts", 1),
    "covered_call": ("calls", 1),
    "put_credit_spread": ("puts", 2),
    "call_credit_spread": ("calls", 2),
}

# You create these rows yourself when you place a trade (status="open").
# This function only ever reads/updates existing rows -- see MANUAL_SETUP.md
# Part E for the required header row.
SHEET_HEADER = [
    "ticker", "strategy", "short_strike", "long_strike", "expiration", "credit",
    "entry_date", "status", "discord_message_id", "profit_target_pct",
    "stop_loss_multiple", "closed_date", "close_reason",
]


def _post_discord_message(content):
    resp = requests.post(
        DISCORD_WEBHOOK_URL,
        params={"wait": "true"},
        json={"content": content},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _reaction_users(message_id, emoji):
    if not message_id:
        return []
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages/{message_id}/reactions/{emoji}"
    resp = requests.get(url, headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}, timeout=30)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json()


def _reaction_choice(message_id):
    """Returns "close", "dismiss", or None. Prefers "close" if you somehow
    reacted with both."""
    close_users = _reaction_users(message_id, CLOSE_EMOJI)
    if any(str(u.get("id")) == str(DISCORD_USER_ID) for u in close_users):
        return "close"
    dismiss_users = _reaction_users(message_id, DISMISS_EMOJI)
    if any(str(u.get("id")) == str(DISCORD_USER_ID) for u in dismiss_users):
        return "dismiss"
    return None


def _sheets_service():
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds)


def _sheet_tab_name(service):
    meta = service.spreadsheets().get(spreadsheetId=GOOGLE_SHEET_ID).execute()
    return meta["sheets"][0]["properties"]["title"]


def _read_all_rows(service, tab):
    result = service.spreadsheets().values().get(
        spreadsheetId=GOOGLE_SHEET_ID, range=f"{tab}!A:M"
    ).execute()
    values = result.get("values", [])
    if len(values) < 2:
        return []
    header = values[0]
    rows = []
    for sheet_row_number, raw in enumerate(values[1:], start=2):
        row = dict(zip(header, raw))
        row["_row_number"] = sheet_row_number
        rows.append(row)
    return rows


def _update_sheet_row(service, tab, row_number, updates):
    data = []
    for col_name, value in updates.items():
        col_idx = SHEET_HEADER.index(col_name)
        col_letter = chr(ord("A") + col_idx)
        data.append({"range": f"{tab}!{col_letter}{row_number}", "values": [[value]]})
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=GOOGLE_SHEET_ID,
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()


def _yf_symbol(ticker):
    return ticker.replace(".", "-")


def _current_close_cost(ticker, expiration, short_strike, long_strike, option_side):
    """Conservative cost to close right now: buy back the short leg at its
    ask (single-leg), or buy back the short leg at ask and sell the long leg
    at bid (spread) -- the mirror image of how these were priced when
    fetch_weekly_option_snapshot originally opened them."""
    stock = yf.Ticker(_yf_symbol(ticker))
    chain = stock.option_chain(expiration)
    table = chain.puts if option_side == "puts" else chain.calls

    short_rows = table[table["strike"] == float(short_strike)]
    if short_rows.empty:
        return None
    short_ask = float(short_rows.iloc[0]["ask"])

    if not long_strike:
        return short_ask

    long_rows = table[table["strike"] == float(long_strike)]
    if long_rows.empty:
        return None
    long_bid = float(long_rows.iloc[0]["bid"])
    return short_ask - long_bid


def _check_open_position(row):
    """Returns a close_reason string ("profit_target"/"stop_loss"/
    "expiration") if this position should be flagged to close now, else
    None. Raises ValueError for an unrecognized strategy or a strategy/
    long_strike mismatch -- the caller catches this per-row and skips it
    rather than silently pricing it against the wrong side of the chain."""
    strategy = row.get("strategy", "")
    if strategy not in STRATEGY_INFO:
        raise ValueError(f"Unknown strategy '{strategy}' -- must be one of {sorted(STRATEGY_INFO)}")
    option_side, legs = STRATEGY_INFO[strategy]

    long_strike = row.get("long_strike") or None
    if legs == 2 and not long_strike:
        raise ValueError(f"{strategy} is a 2-leg spread but long_strike is blank")
    if legs == 1 and long_strike:
        raise ValueError(f"{strategy} is single-leg but long_strike is set to {long_strike}")

    expiration = row["expiration"]
    days_left = (date.fromisoformat(expiration) - date.today()).days
    if days_left <= 1:
        return "expiration"

    credit = float(row["credit"])
    profit_target_pct = float(row.get("profit_target_pct") or PROFIT_TARGET_PCT_DEFAULT)
    stop_loss_multiple = float(row.get("stop_loss_multiple") or STOP_LOSS_MULTIPLE_DEFAULT)

    cost_to_close = _current_close_cost(
        row["ticker"], expiration, row["short_strike"], long_strike, option_side
    )
    if cost_to_close is None:
        return None

    profit_so_far = credit - cost_to_close
    if profit_so_far >= credit * (profit_target_pct / 100):
        return "profit_target"
    if cost_to_close >= credit * stop_loss_multiple:
        return "stop_loss"
    return None


def _nth_weekday(year, month, weekday, n):
    """weekday: Monday=0 ... Sunday=6. Returns the date of the n-th occurrence."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def _easter_sunday(year):
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher) -- no external
    dependency needed for one date-per-year."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(d):
    """NYSE shifts a fixed-date holiday off a weekend: Saturday -> preceding
    Friday, Sunday -> following Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _nyse_holidays(year):
    """NYSE full-market-closure holidays for a given year. Not exhaustive of
    every historical special closure (e.g. a one-off day of mourning), just
    the fixed annual calendar -- good enough to skip the routine no-op case
    of "scheduler fired on a known holiday", not a certified trading calendar."""
    return {
        _observed(date(year, 1, 1)),          # New Year's Day
        _nth_weekday(year, 1, 0, 3),           # MLK Day -- 3rd Monday of Jan
        _nth_weekday(year, 2, 0, 3),           # Washington's Birthday -- 3rd Monday of Feb
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),             # Memorial Day -- last Monday of May
        _observed(date(year, 6, 19)),          # Juneteenth
        _observed(date(year, 7, 4)),           # Independence Day
        _nth_weekday(year, 9, 0, 1),           # Labor Day -- 1st Monday of Sept
        _nth_weekday(year, 11, 3, 4),          # Thanksgiving -- 4th Thursday of Nov
        _observed(date(year, 12, 25)),         # Christmas
    }


REACTION_LEGEND = "✅ = confirm you closed this trade. 🚫 = no action for now."


def _format_close_message(row, reason):
    reason_text = {
        "profit_target": "profit target reached",
        "stop_loss": "stop-loss level reached",
        "expiration": "expiration is imminent",
    }.get(reason, reason)
    return (
        f"**{row['ticker']}** ({row['strategy']}) -- {reason_text}.\n"
        f"{REACTION_LEGEND}"
    )


@functions_framework.http
def main(request):
    # Smoke test: infra-deploy.yml calls this right after every successful
    # `terraform apply` with ?smoke_test=1 so a broken webhook URL/bot
    # token/channel ID surfaces immediately as a failed deploy step, not
    # silently the first time a real trade actually needs an alert. Exercises
    # the exact same _post_discord_message() path production alerts use, via
    # the exact deployed service account -- not a separate direct-from-CI
    # webhook call, which wouldn't prove the function's own secret wiring works.
    if request.args.get("smoke_test"):
        try:
            _post_discord_message(
                "✅ `position_monitor` deployed successfully -- this is a smoke test, no action needed."
            )
            return ("smoke test message sent", 200)
        except Exception as exc:
            print(f"[position_monitor] smoke test failed: {exc}")
            return (f"smoke test failed: {exc}", 500)

    # scheduler.tf's cron only knows weekdays, not NYSE holidays -- skip the
    # real work (Sheets read + yfinance calls per open row) on a day the
    # market's actually closed, rather than running it for nothing.
    today = date.today()
    if today in _nyse_holidays(today.year):
        print(f"[position_monitor] {today.isoformat()} is a NYSE holiday -- skipping")
        return ("market closed today, nothing to monitor", 200)

    # Setup (Parts D/E of MANUAL_SETUP.md) may not be finished yet -- a
    # placeholder GOOGLE_SHEET_ID, or a real sheet not yet shared with this
    # function's service account, both raise an HttpError here. Treat that as
    # "nothing to monitor yet" rather than a failed execution; once the real
    # sheet ID + sharing are in place, this same call just starts succeeding.
    # A plain socket/read timeout can also land here (e.g. under memory
    # pressure) even once setup is genuinely done -- don't mislabel that as a
    # config problem, since it's misleading to debug against.
    try:
        service = _sheets_service()
        tab = _sheet_tab_name(service)
        rows = _read_all_rows(service, tab)
    except TimeoutError as exc:
        print(f"[position_monitor] Sheets API call timed out (transient, will retry next run): {exc}")
        return ("Sheets API timed out, will retry next run", 200)
    except Exception as exc:
        print(f"[position_monitor] Sheet not accessible (placeholder ID? not shared?): {exc}")
        return ("Sheet not accessible, nothing to monitor", 200)

    actioned = 0
    for row in rows:
        status = row.get("status", "")
        row_number = row["_row_number"]
        try:
            if status == "open":
                reason = _check_open_position(row)
                if reason:
                    message_id = _post_discord_message(_format_close_message(row, reason))
                    _update_sheet_row(service, tab, row_number, {
                        "status": "pending_close",
                        "discord_message_id": message_id,
                        "close_reason": reason,
                    })
                    actioned += 1

            elif status == "pending_close":
                choice = _reaction_choice(row.get("discord_message_id"))
                if choice == "close":
                    _update_sheet_row(service, tab, row_number, {
                        "status": "closed",
                        "closed_date": date.today().isoformat(),
                    })
                    actioned += 1
                elif choice == "dismiss":
                    _update_sheet_row(service, tab, row_number, {"status": "open"})
                    actioned += 1
        except Exception as exc:
            print(f"[position_monitor] row {row_number} ({row.get('ticker')}) failed: {exc}")

    return (f"processed {len(rows)} rows, actioned {actioned}", 200)
