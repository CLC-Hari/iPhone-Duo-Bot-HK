"""Hourly Apple Store Hong Kong pickup monitor.

Watches one public product — iPhone Duo 7.6-inch, 512GB, starlight white —
and prints whether each Hong Kong Apple Store can fulfill in-store pickup.

This script only reads the same availability response Apple's own product
page uses. It sends one request per hour. It does not sign in, add anything
to a cart, check out, or retry quickly if Apple declines the request.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import winsound
from datetime import datetime
from pathlib import Path

PART_NUMBER = "MK2F4ZA/A"
PRODUCT_NAME = "iPhone Duo 512GB 星光白色"
PRODUCT_URL = (
    "https://www.apple.com/hk-zh/shop/buy-iphone/iphone-duo/"
    "7.6-吋顯示器-512gb-星光白色"
)
# "Central" returns every Hong Kong Apple Store in a single response.
LOCATION = "Central"
PICKUP_URL = "https://www.apple.com/hk-zh/shop/retail/pickup-message"
USER_AGENT = "IphoneDuoStockMonitor/1.0 (personal hourly availability check)"

INTERVAL_SECONDS = 60 * 60
MIN_INTERVAL_SECONDS = 30 * 60
MAX_BACKOFF_SECONDS = 6 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 30

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"
LOG_PATH = ROOT / "availability.log"

TAG_RE = re.compile(r"<[^>]+>")


class CheckError(Exception):
    """The availability request did not return a usable result."""


def interval_seconds(requested: int) -> int:
    if requested < MIN_INTERVAL_SECONDS:
        raise SystemExit(
            f"Interval must be at least {MIN_INTERVAL_SECONDS // 60} minutes. "
            "This monitor is meant to check about once an hour."
        )
    return requested


def fetch_pickup() -> dict:
    query = urllib.parse.urlencode(
        {
            "pl": "true",
            "parts.0": PART_NUMBER,
            "location": LOCATION,
        }
    )
    request = urllib.request.Request(
        f"{PICKUP_URL}?{query}",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 429, 503, 541}:
            raise CheckError(
                f"Apple declined the check (HTTP {exc.code}). "
                "Waiting longer before the next try."
            ) from exc
        raise CheckError(f"Apple returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise CheckError(f"Network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise CheckError("The request timed out.") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CheckError("Apple did not return JSON.") from exc

    body = data.get("body")
    if not isinstance(body, dict):
        raise CheckError("Apple's response had no body.")
    if not isinstance(body.get("stores"), list):
        message = body.get("errorMessage") or "response did not include stores"
        raise CheckError(str(message))
    return body


def plain_text(value: object) -> str:
    text = TAG_RE.sub("", str(value or ""))
    return " ".join(text.split())


def is_available(part: dict) -> bool:
    display = str(part.get("pickupDisplay") or "").lower()
    if display == "available":
        return True
    if display in {"unavailable", "ineligible"}:
        return False
    regular = (part.get("messageTypes") or {}).get("regular") or {}
    return bool(regular.get("storeSelectionEnabled"))


def parse_stores(body: dict) -> list[dict]:
    rows = []
    for store in body["stores"]:
        parts = store.get("partsAvailability") or {}
        part = parts.get(PART_NUMBER)
        if not isinstance(part, dict):
            rows.append(
                {
                    "store_number": store.get("storeNumber") or "",
                    "store_name": store.get("storeName") or "Unknown store",
                    "available": False,
                    "status": "no availability data",
                    "title": PRODUCT_NAME,
                }
            )
            continue
        regular = (part.get("messageTypes") or {}).get("regular") or {}
        quote = plain_text(
            regular.get("storePickupQuote") or part.get("pickupSearchQuote")
        )
        rows.append(
            {
                "store_number": store.get("storeNumber") or "",
                "store_name": store.get("storeName") or "Unknown store",
                "available": is_available(part),
                "status": quote or plain_text(part.get("pickupDisplay")) or "unknown",
                "title": plain_text(regular.get("storePickupProductTitle"))
                or PRODUCT_NAME,
            }
        )
    if not rows:
        raise CheckError("Apple returned an empty store list.")
    return rows


def format_report(rows: list[dict], checked_at: datetime) -> str:
    title = rows[0]["title"]
    lines = [
        f"{checked_at.strftime('%Y-%m-%d %H:%M:%S')}  {title}  ({PART_NUMBER})",
    ]
    for row in rows:
        flag = "AVAILABLE" if row["available"] else "unavailable"
        lines.append(f"  {row['store_name']:<18} {flag:<12} {row['status']}")
    available = [row["store_name"] for row in rows if row["available"]]
    if available:
        lines.append("In stock for pickup: " + ", ".join(available))
        lines.append(PRODUCT_URL)
    else:
        lines.append("No Hong Kong Apple Store currently offers pickup.")
    return "\n".join(lines)


def popup_text(rows: list[dict]) -> tuple[str, str, bool]:
    available = [row["store_name"] for row in rows if row["available"]]
    lines = [f"{row['store_name']}: {row['status']}" for row in rows]
    if available:
        title = "iPhone Duo pickup available"
        lines.insert(0, "Available now at: " + ", ".join(available))
        lines.append("")
        lines.append(PRODUCT_URL)
        return title, "\n".join(lines), True
    title = "iPhone Duo stock check"
    lines.append("")
    lines.append("No Hong Kong Apple Store currently offers pickup.")
    return title, "\n".join(lines), False


def show_desktop_popup(title: str, message: str, *, sound: bool, wait: bool) -> None:
    if sound:
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except RuntimeError:
            pass
    # Topmost dialog so it appears over the desktop even when the console is behind other windows.
    flags = 0x00040000 | 0x00010000 | (0x00000030 if sound else 0x00000040)

    def open_dialog() -> None:
        ctypes.windll.user32.MessageBoxW(0, message, title, flags)

    if wait:
        open_dialog()
        return
    threading.Thread(target=open_dialog, daemon=True).start()


def append_log(report: str) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(report + "\n\n")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(rows: list[dict], checked_at: datetime, failures: int) -> None:
    state = {
        "checked_at": checked_at.isoformat(timespec="seconds"),
        "part_number": PART_NUMBER,
        "consecutive_failures": failures,
        "stores": [
            {
                "store_number": row["store_number"],
                "store_name": row["store_name"],
                "available": row["available"],
                "status": row["status"],
            }
            for row in rows
        ],
    }
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_once(*, wait_for_popup: bool) -> list[dict]:
    checked_at = datetime.now()
    rows = parse_stores(fetch_pickup())
    report = format_report(rows, checked_at)
    print(report, flush=True)
    append_log(report)
    save_state(rows, checked_at, failures=0)
    title, message, in_stock = popup_text(rows)
    show_desktop_popup(title, message, sound=in_stock, wait=wait_for_popup)
    return rows


def watch(every_seconds: int) -> None:
    failures = int(load_state().get("consecutive_failures") or 0)
    print(
        f"Checking {PRODUCT_NAME} at Hong Kong Apple Stores every "
        f"{every_seconds // 60} minutes. Press Ctrl+C to stop.",
        flush=True,
    )
    print(PRODUCT_URL, flush=True)
    while True:
        try:
            run_once(wait_for_popup=False)
            failures = 0
            delay = every_seconds
        except CheckError as exc:
            failures += 1
            delay = min(every_seconds * (2 ** (failures - 1)), MAX_BACKOFF_SECONDS)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = f"{stamp}  check failed ({failures}): {exc}"
            print(message, flush=True)
            append_log(message)
            save_state([], datetime.now(), failures)
            show_desktop_popup(
                "iPhone Duo check failed",
                str(exc),
                sound=False,
                wait=False,
            )
        except KeyboardInterrupt:
            print("\nStopped.", flush=True)
            return
        print(f"Next check in {max(delay // 60, 1)} minutes.", flush=True)
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            print("\nStopped.", flush=True)
            return


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Check Hong Kong Apple Store pickup for iPhone Duo 512GB starlight white."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check one time and exit.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=INTERVAL_SECONDS,
        help=f"Seconds between checks (default {INTERVAL_SECONDS}, minimum {MIN_INTERVAL_SECONDS}).",
    )
    args = parser.parse_args()
    if args.once:
        try:
            run_once(wait_for_popup=True)
        except CheckError as exc:
            print(f"Check failed: {exc}", flush=True)
            raise SystemExit(1) from exc
        return
    watch(interval_seconds(args.interval))


if __name__ == "__main__":
    main()
