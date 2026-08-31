from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
VESSELS_PATH = REPO_ROOT / "data" / "vessels.parquet"
VOYAGES_PATH = REPO_ROOT / "data" / "voyages.parquet"
OUTPUT_PATH = REPO_ROOT / "docs" / "DISPATCH_LOG.md"


def main():
    voyages = pd.read_parquet(VOYAGES_PATH)
    vessels = pd.read_parquet(VESSELS_PATH)

    merged = voyages.merge(vessels[["mmsi", "vessel_name"]], on="mmsi", how="left")
    merged = merged.sort_values("last_updated", ascending=False)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Dispatch Log",
        "",
        f"Generated: {now}",
        f"Active voyages: {len(merged)}",
        "",
        "| MMSI | Vessel | Destination | Lat | Lon | Speed (kn) | Blackout | Last Updated |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for _, row in merged.iterrows():
        dest_port_name = row["dest_port_name"] if pd.notna(row["dest_port_name"]) else None
        dest_raw = row["dest_raw_string"] if pd.notna(row["dest_raw_string"]) else None
        dest_unlocode = row["dest_unlocode"] if pd.notna(row["dest_unlocode"]) else None
        vessel_name = row["vessel_name"] if pd.notna(row["vessel_name"]) else "—"

        dest = dest_port_name or dest_raw or "—"
        unlocode = f" ({dest_unlocode})" if dest_unlocode else ""
        lat = f"{row['current_lat']:.3f}" if pd.notna(row["current_lat"]) else "—"
        lon = f"{row['current_lon']:.3f}" if pd.notna(row["current_lon"]) else "—"
        speed = f"{row['speed']:.1f}" if pd.notna(row["speed"]) else "—"
        blackout = "🔴 Yes" if row["ais_blackout_flag"] else "🟢 No"
        last_updated = pd.Timestamp(row["last_updated"]).strftime("%Y-%m-%d %H:%M UTC")

        lines.append(
            f"| {row['mmsi']} | {vessel_name} | {dest}{unlocode} | {lat} | {lon} | "
            f"{speed} | {blackout} | {last_updated} |"
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(merged)} rows -> {OUTPUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()