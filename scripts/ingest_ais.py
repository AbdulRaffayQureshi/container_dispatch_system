from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import websockets
from dotenv import load_dotenv

# --- Config -------------------------------------------------------------------

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"

# How long to listen before closing the connection. GitHub Actions jobs aren't
# long-lived processes, so this must be a short, fixed window per the spec.
LISTEN_DURATION_SECONDS = 90

# AISstream requires the subscription message to be sent within 3 seconds of the
# WebSocket connection opening, or the server closes the connection.
SUBSCRIBE_TIMEOUT_SECONDS = 3

# Bounding box: Strait of Malacca approach, one of the busiest shipping lanes in the
# world. Format per AISstream docs: [[[lat1, lon1], [lat2, lon2]]] — two opposite
# corners of the box. Swap this for the US West Coast box (commented below) or add
# a second box to the list to cover both.
MALACCA_STRAIT_BBOX = [[1.0, 98.0], [6.5, 104.5]]
# US_WEST_COAST_BBOX = [[32.0, -125.5], [49.0, -117.0]]
BOUNDING_BOXES = [MALACCA_STRAIT_BBOX]

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "raw_snapshot.json"


# --- Steps ----------------------------------------------------------------------


def load_api_key() -> str:
    """Load AISSTREAM_API_KEY from .env (local) or the environment (CI)."""
    load_dotenv()
    api_key = os.environ.get("AISSTREAM_API_KEY")
    if not api_key:
        print(
            "ERROR: AISSTREAM_API_KEY not found. Create a .env file locally with "
            "AISSTREAM_API_KEY=your_key_here, or set it as an environment variable "
            "/ repo secret in CI.",
            file=sys.stderr,
        )
        sys.exit(1)
    return api_key


async def collect_position_reports(
    websocket: websockets.WebSocketClientProtocol, duration_seconds: int
) -> tuple[dict[int, dict], int]:
    """Drain PositionReport messages for a fixed window, deduped by MMSI (latest wins).

    Returns (vessels_by_mmsi, total_raw_message_count). Using a plain dict keyed by
    MMSI and overwriting on every new message for that vessel is what implements the
    "keep only the latest ping per vessel" dedup rule — no separate dedup pass needed.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + duration_seconds

    vessels: dict[int, dict] = {}
    raw_message_count = 0

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break

        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break

        raw_message_count += 1

        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if message.get("MessageType") != "PositionReport":
            continue

        meta = message.get("MetaData", {})
        mmsi = meta.get("MMSI")
        if mmsi is None:
            continue

        vessels[mmsi] = message

    return vessels, raw_message_count


async def run_ingestion(api_key: str) -> None:
    subscribe_message = {
        "APIKey": api_key,
        "BoundingBoxes": BOUNDING_BOXES,
        "FilterMessageTypes": ["PositionReport"],
    }

    started_at = datetime.now(timezone.utc)
    print(f"Connecting to {AISSTREAM_URL} ...")

    async with websockets.connect(AISSTREAM_URL) as websocket:
        await asyncio.wait_for(
            websocket.send(json.dumps(subscribe_message)),
            timeout=SUBSCRIBE_TIMEOUT_SECONDS,
        )
        print(
            f"Subscribed. Bounding box(es): {BOUNDING_BOXES}. "
            f"Listening for {LISTEN_DURATION_SECONDS}s ..."
        )
        vessels, raw_message_count = await collect_position_reports(
            websocket, LISTEN_DURATION_SECONDS
        )
        # `async with` closes the connection cleanly on exit from this block.

    finished_at = datetime.now(timezone.utc)
    print(
        f"Done. {raw_message_count} raw messages received, "
        f"{len(vessels)} unique vessels after MMSI dedup."
    )

    snapshot = {
        "collected_at_utc_start": started_at.isoformat(),
        "collected_at_utc_end": finished_at.isoformat(),
        "listen_duration_seconds": LISTEN_DURATION_SECONDS,
        "bounding_boxes": BOUNDING_BOXES,
        "raw_message_count": raw_message_count,
        "unique_vessel_count": len(vessels),
        "vessels": list(vessels.values()),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(snapshot, f, indent=2)

    print(f"Wrote snapshot -> {OUTPUT_PATH.relative_to(REPO_ROOT)}")


def main() -> None:
    api_key = load_api_key()
    try:
        asyncio.run(run_ingestion(api_key))
    except websockets.exceptions.WebSocketException as exc:
        print(f"WebSocket error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()