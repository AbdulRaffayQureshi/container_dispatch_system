from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clean_destinations import DestinationCleaner

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_SNAPSHOT_PATH = REPO_ROOT / "data" / "raw_snapshot.json"
VESSELS_PATH = REPO_ROOT / "data" / "vessels.parquet"
VOYAGES_PATH = REPO_ROOT / "data" / "voyages.parquet"

BLACKOUT_THRESHOLD_HOURS = 6

VESSEL_COLUMNS = ["mmsi", "imo", "vessel_name", "vessel_type", "flag", "length", "width"]
VOYAGE_COLUMNS = [
    "voyage_id", "mmsi", "origin_unlocode", "dest_unlocode", "dest_port_name",
    "dest_raw_string", "current_lat", "current_lon", "speed",
    "ais_blackout_flag", "last_updated",
]


def load_or_empty(path: Path, columns: list[str]) -> pd.DataFrame:
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=columns)


def build_vessel_row(vessel: dict) -> dict | None:
    static = vessel.get("ship_static_data")
    if not static:
        return None
    sd = static.get("Message", {}).get("ShipStaticData", {})
    dim = sd.get("Dimension") or {}
    length = dim.get("A", 0) + dim.get("B", 0) if dim else None
    width = dim.get("C", 0) + dim.get("D", 0) if dim else None
    return {
        "mmsi": vessel["mmsi"],
        "imo": sd.get("ImoNumber"),
        "vessel_name": sd.get("Name"),
        "vessel_type": sd.get("Type"),
        "flag": None,  # not derived yet - would need MMSI MID lookup table
        "length": length or None,
        "width": width or None,
    }


def build_voyage_row(vessel: dict, cleaner: DestinationCleaner, now: datetime) -> dict | None:
    pos = vessel.get("position_report")
    if not pos:
        return None
    pr = pos.get("Message", {}).get("PositionReport", {})
    meta = pos.get("MetaData", {})

    static = vessel.get("ship_static_data")
    dest_raw = ""
    if static:
        dest_raw = static.get("Message", {}).get("ShipStaticData", {}).get("Destination", "") or ""
    cleaned = cleaner.clean(dest_raw) if dest_raw else cleaner.clean("")

    return {
        "voyage_id": str(vessel["mmsi"]),
        "mmsi": vessel["mmsi"],
        "origin_unlocode": None,  # not tracked yet - needs port-call/departure detection
        "dest_unlocode": cleaned["unlocode"],
        "dest_port_name": cleaned["port_name"],
        "dest_raw_string": dest_raw,
        "current_lat": meta.get("latitude", meta.get("Latitude")),
        "current_lon": meta.get("longitude", meta.get("Longitude")),
        "speed": pr.get("Sog"),
        "ais_blackout_flag": False,  # heard from it this run, so by definition not dark
        "last_updated": now,
    }


def upsert(existing: pd.DataFrame, new: pd.DataFrame, key: str) -> pd.DataFrame:
    """Concat with new rows last, then drop_duplicates(keep='last') so new values win
    on matching keys while untouched existing rows and brand-new rows both survive."""
    if existing.empty:
        return new.reset_index(drop=True)
    combined = pd.concat([existing, new], ignore_index=True)
    combined = combined.drop_duplicates(subset=key, keep="last")
    return combined.reset_index(drop=True)


def recompute_blackout(voyages: pd.DataFrame, refreshed_mmsis: set, now: datetime) -> pd.DataFrame:
    """For voyages NOT refreshed this run, flag blackout if last_updated is stale."""
    stale_mask = ~voyages["mmsi"].isin(refreshed_mmsis)
    if stale_mask.any():
        last_updated = pd.to_datetime(voyages.loc[stale_mask, "last_updated"], utc=True)
        hours_since = (pd.Timestamp(now) - last_updated).dt.total_seconds() / 3600
        voyages.loc[stale_mask, "ais_blackout_flag"] = hours_since > BLACKOUT_THRESHOLD_HOURS
    return voyages


def main() -> None:
    if not RAW_SNAPSHOT_PATH.exists():
        print(f"No snapshot at {RAW_SNAPSHOT_PATH.relative_to(REPO_ROOT)} — run ingest_ais.py first.", file=sys.stderr)
        sys.exit(1)

    with open(RAW_SNAPSHOT_PATH) as f:
        snapshot = json.load(f)

    cleaner = DestinationCleaner()
    now = datetime.now(timezone.utc)

    vessel_rows = [r for v in snapshot["vessels"] if (r := build_vessel_row(v))]
    voyage_rows = [r for v in snapshot["vessels"] if (r := build_voyage_row(v, cleaner, now))]

    new_vessels = pd.DataFrame(vessel_rows, columns=VESSEL_COLUMNS)
    new_voyages = pd.DataFrame(voyage_rows, columns=VOYAGE_COLUMNS)

    existing_vessels = load_or_empty(VESSELS_PATH, VESSEL_COLUMNS)
    existing_voyages = load_or_empty(VOYAGES_PATH, VOYAGE_COLUMNS)

    vessels_out = upsert(existing_vessels, new_vessels, key="mmsi")
    voyages_out = upsert(existing_voyages, new_voyages, key="voyage_id")

    refreshed_mmsis = set(new_voyages["mmsi"]) if not new_voyages.empty else set()
    voyages_out = recompute_blackout(voyages_out, refreshed_mmsis, now)

    VESSELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    vessels_out.to_parquet(VESSELS_PATH, index=False)
    voyages_out.to_parquet(VOYAGES_PATH, index=False)

    print(f"vessels.parquet: {len(vessels_out)} rows ({len(new_vessels)} from this run)")
    print(f"voyages.parquet: {len(voyages_out)} rows ({len(new_voyages)} from this run, "
          f"{voyages_out['ais_blackout_flag'].sum()} flagged blackout)")


if __name__ == "__main__":
    main()