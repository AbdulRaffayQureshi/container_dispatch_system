"""
build_unlocode_reference.py

Step 2 of the Container Dispatch Intelligence Platform build order.

Fetches the UN/LOCODE master code list (maintained by UNECE, mirrored as clean CSV by the
`datasets/un-locode` project on GitHub), filters it down to maritime seaports with usable
coordinates, converts those coordinates from UN/LOCODE's degrees-minutes string format to
decimal lat/lon floats, and writes a small reference CSV that the destination-cleaning
pipeline (Step 4) will fuzzy-match dirty AIS destination strings against.

Source data:
    https://github.com/datasets/un-locode  (data/code-list.csv)
    Original data comes from UNECE: https://unece.org/trade/cefact/UNLOCODE-Download
    License: ODC-PDDL-1.0 (Open Data Commons Public Domain Dedication and License)

Output:
    data/reference/unlocode_seaports.csv
    Columns: unlocode, country, location_code, name, name_ascii, subdivision, latitude, longitude

Usage:
    python scripts/build_unlocode_reference.py
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pandas as pd
import requests

# --- Config -----------------------------------------------------------------

# raw.githubusercontent.com mirror of datasets/un-locode's cleaned code list.
UNLOCODE_CSV_URL = "https://raw.githubusercontent.com/datasets/un-locode/main/data/code-list.csv"

# Function code '1' = "Port, as defined in Rec 16" (maritime seaport). It's the first
# character of the 8-character Function classifier string, e.g. "1-3-----".
SEAPORT_FUNCTION_CODE = "1"

# UN/LOCODE coordinate format: "DDMM[N/S] DDDMM[E/W]", e.g. "4230N 00131E".
COORD_PATTERN = re.compile(r"^(\d{2})(\d{2})([NS])\s(\d{3})(\d{2})([EW])$")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "data" / "reference"
OUTPUT_PATH = OUTPUT_DIR / "unlocode_seaports.csv"

REQUEST_TIMEOUT_SECONDS = 30


# --- Steps --------------------------------------------------------------------


def fetch_raw_codelist(url: str = UNLOCODE_CSV_URL) -> pd.DataFrame:
    """Download the full UN/LOCODE code list and load it as a string-typed DataFrame.

    dtype=str + keep_default_na=False matters here: location codes like "AE" or
    subdivision codes could otherwise be mangled by pandas' type inference, and a
    genuinely empty Coordinates field should read as "" rather than NaN so the
    coordinate parser below can treat "no coordinates" as a plain, checkable case.
    """
    print(f"Fetching UN/LOCODE code list from {url} ...")
    response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()

    df = pd.read_csv(io.StringIO(response.text), dtype=str, keep_default_na=False)
    print(f"  -> {len(df):,} total UN/LOCODE entries loaded")
    return df


def filter_seaports_with_coordinates(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only maritime seaports (Function code '1') that have a non-empty Coordinates field."""
    is_seaport = df["Function"].str.startswith(SEAPORT_FUNCTION_CODE, na=False)
    has_coords = df["Coordinates"].str.strip() != ""

    filtered = df[is_seaport & has_coords].copy()
    print(
        f"  -> {is_seaport.sum():,} seaports total, "
        f"{len(filtered):,} of those have coordinates and are kept"
    )
    return filtered


def parse_coordinates(coord_str: str) -> tuple[float, float] | tuple[None, None]:
    """Convert a UN/LOCODE coordinate string ('DDMM[N/S] DDDMM[E/W]') to (lat, lon) decimal degrees.

    Example: "4230N 00131E" -> (42.5, 1.5166666666666666)
    Returns (None, None) if the string doesn't match the expected format, so a bad row
    is dropped explicitly downstream rather than silently propagating a wrong coordinate.
    """
    match = COORD_PATTERN.match(coord_str.strip())
    if not match:
        return None, None

    lat_deg, lat_min, lat_hemi, lon_deg, lon_min, lon_hemi = match.groups()

    latitude = int(lat_deg) + int(lat_min) / 60.0
    if lat_hemi == "S":
        latitude *= -1

    longitude = int(lon_deg) + int(lon_min) / 60.0
    if lon_hemi == "W":
        longitude *= -1

    return latitude, longitude


def build_reference_table(seaports: pd.DataFrame) -> pd.DataFrame:
    """Parse coordinates and assemble the final lightweight reference table."""
    parsed = seaports["Coordinates"].apply(parse_coordinates)
    seaports = seaports.assign(
        latitude=[lat for lat, _ in parsed],
        longitude=[lon for _, lon in parsed],
    )

    # Drop anything that failed to parse (shouldn't happen against clean source data,
    # but never trust upstream data quality silently — see the spec's data-quality section).
    before = len(seaports)
    seaports = seaports.dropna(subset=["latitude", "longitude"])
    dropped = before - len(seaports)
    if dropped:
        print(f"  -> dropped {dropped} row(s) with unparseable coordinates")

    seaports["unlocode"] = seaports["Country"] + seaports["Location"]

    reference = seaports.rename(
        columns={
            "Country": "country",
            "Location": "location_code",
            "Name": "name",
            "NameWoDiacritics": "name_ascii",
            "Subdivision": "subdivision",
        }
    )[
        [
            "unlocode",
            "country",
            "location_code",
            "name",
            "name_ascii",
            "subdivision",
            "latitude",
            "longitude",
        ]
    ]

    reference = reference.sort_values("unlocode").reset_index(drop=True)
    return reference


def main() -> None:
    raw = fetch_raw_codelist()
    seaports = filter_seaports_with_coordinates(raw)

    if seaports.empty:
        print("No seaports with coordinates found — aborting without writing output.", file=sys.stderr)
        sys.exit(1)

    reference = build_reference_table(seaports)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    reference.to_csv(OUTPUT_PATH, index=False)

    print(f"Wrote {len(reference):,} seaport reference rows -> {OUTPUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()