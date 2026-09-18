import requests
import time
import os
import random
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================

AGENTROUTER_URL = "https://agentrouter.org/api/user/model-status"

SESSION_COOKIE = os.getenv("AGENTROUTER_SESSION")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Normal check interval = 14 minutes
CHECK_INTERVAL = 14 * 60

# Small random variation so checks aren't exactly identical
# every cycle. This is just polite scheduling, not ban evasion.
JITTER_SECONDS = 30

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://agentrouter.org/console/model-status",
    "new-api-user": "185239",
}


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    try:
        r = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            },
            timeout=15
        )

        r.raise_for_status()
        return True

    except Exception as e:
        print("Telegram error:", e)
        return False


# ============================================================
# AGENT ROUTER
# ============================================================

def get_models():

    r = requests.get(
        AGENTROUTER_URL,
        headers=HEADERS,
        cookies={
            "session": SESSION_COOKIE
        },
        timeout=20
    )

    # Authentication/session expired
    if r.status_code in (401, 403):

        send_telegram(
            "⚠️ <b>AgentRouter monitor needs attention</b>\n\n"
            "The session appears to have expired or authentication "
            "was rejected.\n\n"
            "The monitor has stopped making requests."
        )

        raise RuntimeError(
            f"Authentication failed: HTTP {r.status_code}"
        )

    # Respect server-side rate limiting
    if r.status_code == 429:

        retry_after = r.headers.get("Retry-After")

        if retry_after:
            wait = int(retry_after)
        else:
            wait = 15 * 60

        print(
            f"Rate limited. Waiting {wait} seconds."
        )

        time.sleep(wait)

        return None

    r.raise_for_status()

    return r.json()["data"]


# ============================================================
# CLAUDE STATUS
# ============================================================

def latest_heartbeat(model):

    heartbeat = model.get("heartbeat", [])

    if not heartbeat:
        return "none"

    return heartbeat[-1]


def status_text(status):

    if status == "ok":
        return "🟢 AVAILABLE"

    if status == "warn":
        return "🟡 DEGRADED"

    if status == "degraded":
        return "🟠 DEGRADED"

    if status == "none":
        return "⚪ NO DATA"

    return f"❓ {status}"


# ============================================================
# MAIN
# ============================================================

previous_status = {}

print("=" * 60)
print("        AGENT ROUTER CLAUDE MONITOR")
print("=" * 60)
print("Polling interval: 14 minutes")
print()


while True:

    try:

        data = get_models()

        if data is None:
            continue

        for model in data["models"]:

            name = model["name"]

            # Automatically monitor any Claude model
            if not name.lower().startswith("claude-"):
                continue

            current = latest_heartbeat(model)
            previous = previous_status.get(name)

            print(
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "|",
                name,
                "|",
                previous,
                "→",
                current
            )

            # ------------------------------------------------
            # Claude became active again
            # ------------------------------------------------

            if (
                previous == "none"
                and current in ("ok", "warn", "degraded")
            ):

                message = (
                    "🚨 <b>Claude model detected!</b>\n\n"
                    f"Model: <b>{name}</b>\n"
                    f"Current: {status_text(current)}\n"
                    f"Heartbeat: <code>{current}</code>\n\n"
                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )

                print("\nSending Telegram notification...")
                send_telegram(message)

            previous_status[name] = current

    except RuntimeError as e:

        print("STOPPED:", e)
        break

    except requests.RequestException as e:

        # Network error: don't immediately retry repeatedly.
        print("Network error:", e)
        print("Backing off before next attempt...")

        time.sleep(60)

        continue

    except Exception as e:

        print("Unexpected error:", e)

        # Conservative recovery
        time.sleep(60)

        continue

    # --------------------------------------------------------
    # Wait approximately 14 minutes before next check
    # --------------------------------------------------------

    jitter = random.randint(
        -JITTER_SECONDS,
        JITTER_SECONDS
    )

    wait_time = CHECK_INTERVAL + jitter

    print(
        f"\nNext check in approximately "
        f"{wait_time // 60} minutes."
    )

    time.sleep(wait_time)