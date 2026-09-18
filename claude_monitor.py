import os
import time
import random
import threading
import requests
from datetime import datetime
from flask import Flask

# ============================================================
# CONFIG
# ============================================================

AGENTROUTER_URL = "https://agentrouter.org/api/user/model-status"

SESSION_COOKIE = os.getenv("AGENTROUTER_SESSION")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Check approximately every 14 minutes
CHECK_INTERVAL = 14 * 60

# Small scheduling jitter
JITTER_SECONDS = 30

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://agentrouter.org/console/model-status",
    "new-api-user": "185239",
}

# ============================================================
# WEB SERVER FOR UPTIMEROBOT
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Claude Monitor is running"


@app.route("/health")
def health():
    return "OK", 200


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    try:
        response = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            },
            timeout=15
        )

        response.raise_for_status()

        print("Telegram notification sent")
        return True

    except Exception as e:
        print("Telegram error:", e)
        return False


# ============================================================
# AGENTROUTER
# ============================================================

def get_models():

    response = requests.get(
        AGENTROUTER_URL,
        headers=HEADERS,
        cookies={
            "session": SESSION_COOKIE
        },
        timeout=20
    )

    if response.status_code in (401, 403):

        send_telegram(
            "⚠️ <b>AgentRouter Monitor</b>\n\n"
            "Session expired or authentication was rejected.\n"
            "Please update AGENTROUTER_SESSION on Render."
        )

        raise RuntimeError(
            f"Authentication failed: HTTP {response.status_code}"
        )

    if response.status_code == 429:

        retry_after = response.headers.get("Retry-After")

        try:
            wait = int(retry_after)
        except (TypeError, ValueError):
            wait = 15 * 60

        print(
            f"Rate limited. Waiting {wait} seconds."
        )

        time.sleep(wait)

        return None

    response.raise_for_status()

    return response.json()["data"]


# ============================================================
# HEARTBEAT
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
# MONITOR
# ============================================================

def monitor():

    # Keep previous status in memory
    previous_status = {}

    print("=" * 60)
    print("       AGENT ROUTER CLAUDE MONITOR")
    print("=" * 60)
    print("Polling interval: 14 minutes")
    print()

    while True:

        try:

            data = get_models()

            if data is None:
                continue

            print(
                "\n[" +
                datetime.now().strftime("%Y-%m-%d %H:%M:%S") +
                "] Checking models..."
            )

            for model in data.get("models", []):

                name = model.get("name", "")

                # Automatically detect Claude models
                if not name.lower().startswith("claude-"):
                    continue

                current = latest_heartbeat(model)
                previous = previous_status.get(name)

                print(
                    f"{name:<25} "
                    f"{previous} -> {current}"
                )

                # ------------------------------------------------
                # Claude came back
                # ------------------------------------------------

                if (
                    previous == "none"
                    and current in ("ok", "warn", "degraded")
                ):

                    message = (
                        "🚨 <b>Claude model is back!</b>\n\n"
                        f"Model: <b>{name}</b>\n"
                        f"Status: <b>{status_text(current)}</b>\n"
                        f"Heartbeat: <code>{current}</code>\n\n"
                        f"Time: "
                        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )

                    print("Sending Telegram alert...")

                    send_telegram(message)

                previous_status[name] = current

        except RuntimeError as e:

            print("STOPPED:", e)
            break

        except requests.RequestException as e:

            print("Network error:", e)
            print("Waiting 60 seconds before retry...")

            time.sleep(60)
            continue

        except Exception as e:

            print("Unexpected error:", e)
            print("Waiting 60 seconds...")

            time.sleep(60)
            continue

        # --------------------------------------------------------
        # 14-minute interval + small jitter
        # --------------------------------------------------------

        jitter = random.randint(
            -JITTER_SECONDS,
            JITTER_SECONDS
        )

        wait_time = CHECK_INTERVAL + jitter

        print(
            f"Next check in approximately "
            f"{wait_time // 60} minutes."
        )

        time.sleep(wait_time)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    # Start monitor in background
    monitor_thread = threading.Thread(
        target=monitor,
        daemon=True
    )

    monitor_thread.start()

    # Render provides PORT automatically
    port = int(os.environ.get("PORT", 10000))

    print(f"Web server starting on port {port}")

    app.run(
        host="0.0.0.0",
        port=port
    )