"""
clean_destinations.py

Step 4 of the Container Dispatch Intelligence Platform build order.

Cleans dirty, free-typed AIS Destination strings (e.g. "US LAX", "ROTTDAM", "FOR ORDERS")
into a standardized UN/LOCODE + port name + coordinates, using three tiers, tried in
order, first match wins:

    1. Manual overrides  — data/reference/destination_corrections.csv, checked first.
       Handles recurring garbage strings a human has already resolved (or explicitly
       flagged as unresolvable — see "ignore rows" below).
    2. Direct UN/LOCODE match — if the cleaned string, stripped of whitespace, IS a
       real UN/LOCODE (e.g. "US LAX" -> "USLAX"), match it exactly. This is common in
       real AIS traffic: crews broadcast the raw code instead of a port name, and
       fuzzy-matching it against port *names* is actively dangerous — see the module
       docstring below the THRESHOLD constant for why.
    3. Fuzzy match — thefuzz token_sort_ratio against "port name, country" strings
       from the seaport reference, only above FUZZY_MATCH_THRESHOLD. Below that, the
       string is left unmatched rather than guessed.

Manual overrides CSV format (data/reference/destination_corrections.csv):
    raw_destination,unlocode,notes
    ROTTDAM,NLRTM,common misspelling of Rotterdam
    FOR ORDERS,,not a real destination - awaiting orders, intentionally unresolved
    N/A,,not a real destination

An override row with an EMPTY unlocode means "known garbage, don't attempt to match
this at all" — it short-circuits straight to an "ignored" result instead of falling
through to fuzzy matching, which is exactly what stops junk like "FOR ORDERS" from
being fuzzy-matched into a wrong port with a plausible-looking score.

Usage as a library (this is how Step 5's Parquet upsert will use it):
    from clean_destinations import DestinationCleaner
    cleaner = DestinationCleaner()
    result = cleaner.clean("US LAX")
    # {"raw_destination": "US LAX", "unlocode": "USLAX", "port_name": "Los Angeles",
    #  "country": "US", "latitude": 34.05, "longitude": -118.25,
    #  "match_method": "direct_code", "match_score": 100}

Usage standalone, to test against real captured strings from data/raw_snapshot.json:
    python scripts/clean_destinations.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import pandas as pd
from thefuzz import fuzz, process

# --- Config -------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SEAPORT_REFERENCE_PATH = REPO_ROOT / "data" / "reference" / "unlocode_seaports.csv"
OVERRIDES_PATH = REPO_ROOT / "data" / "reference" / "destination_corrections.csv"
RAW_SNAPSHOT_PATH = REPO_ROOT / "data" / "raw_snapshot.json"

# Below this thefuzz token_sort_ratio score, a fuzzy match is discarded rather than
# used. Real-world testing against this project's own seaport reference showed junk
# strings ("FOR ORDERS", "N/A") and even wrong-but-plausible matches ("US LAX" ->
# "Climax US") scoring 67-80. 85 was the lowest threshold that still caught genuine
# near-misses (e.g. "SINGAPORE" -> "Singapore SG" at 86) while rejecting that junk.
# Recurring strings that fall below this threshold belong in the overrides CSV, not
# in a lowered threshold.
FUZZY_MATCH_THRESHOLD = 85

# Strings shorter than this are never fuzzy-matched — too little signal, too easy to
# false-positive against a 3-letter location code.
MIN_LENGTH_FOR_FUZZY_MATCH = 4


# --- Helpers --------------------------------------------------------------------


def normalize_destination(raw: str) -> str:
    """Upper-case, collapse whitespace, strip. The one normalization every tier shares."""
    return re.sub(r"\s+", " ", raw or "").strip().upper()


class DestinationCleaner:
    def __init__(
        self,
        seaport_reference_path: Path = SEAPORT_REFERENCE_PATH,
        overrides_path: Path = OVERRIDES_PATH,
        fuzzy_threshold: int = FUZZY_MATCH_THRESHOLD,
    ):
        self.fuzzy_threshold = fuzzy_threshold
        self.seaports = self._load_seaport_reference(seaport_reference_path)
        self.seaports_by_unlocode = self.seaports.set_index("unlocode")
        # thefuzz's process.extractOne wants {key: choice_string} to get the index back.
        self._fuzzy_choices = dict(
            zip(self.seaports.index, self.seaports["name_ascii"] + " " + self.seaports["country"])
        )
        self.overrides = self._load_overrides(overrides_path)

    @staticmethod
    def _load_seaport_reference(path: Path) -> pd.DataFrame:
        if not path.exists():
            raise FileNotFoundError(
                f"Seaport reference not found at {path}. Run "
                "scripts/build_unlocode_reference.py first (Step 2)."
            )
        return pd.read_csv(path, dtype={"unlocode": str, "country": str, "location_code": str})

    @staticmethod
    def _load_overrides(path: Path) -> dict[str, Optional[str]]:
        """Load manual corrections. Returns {normalized_raw_string: unlocode_or_None}.

        A missing file is not an error — it just means no overrides yet, which is a
        normal state early in the project.
        """
        if not path.exists():
            return {}

        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        overrides: dict[str, Optional[str]] = {}
        for _, row in df.iterrows():
            key = normalize_destination(row["raw_destination"])
            unlocode = row["unlocode"].strip().upper()
            overrides[key] = unlocode if unlocode else None
        return overrides

    def _row_to_result(self, raw: str, row: pd.Series, match_method: str, match_score: int) -> dict:
        return {
            "raw_destination": raw,
            "unlocode": row.name if row.name in self.seaports_by_unlocode.index else row["unlocode"],
            "port_name": row["name"],
            "country": row["country"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "match_method": match_method,
            "match_score": match_score,
        }

    def _unmatched_result(self, raw: str, match_method: str) -> dict:
        return {
            "raw_destination": raw,
            "unlocode": None,
            "port_name": None,
            "country": None,
            "latitude": None,
            "longitude": None,
            "match_method": match_method,
            "match_score": None,
        }

    def clean(self, raw_destination: str) -> dict:
        """Resolve one raw AIS destination string. See module docstring for the tiers."""
        normalized = normalize_destination(raw_destination)

        if not normalized:
            return self._unmatched_result(raw_destination, "empty")

        # Tier 1: manual overrides.
        if normalized in self.overrides:
            unlocode = self.overrides[normalized]
            if unlocode is None:
                # Deliberately marked as unresolvable garbage — stop here, don't fuzzy match.
                return self._unmatched_result(raw_destination, "ignored_by_override")
            if unlocode in self.seaports_by_unlocode.index:
                row = self.seaports_by_unlocode.loc[unlocode]
                return self._row_to_result(raw_destination, row, "override", 100)
            # Override points at a code that isn't in the seaport reference (typo in the
            # overrides CSV, or a non-seaport UN/LOCODE) — surface that rather than silently
            # falling through, since it means the overrides CSV itself needs a fix.
            return self._unmatched_result(raw_destination, "override_unlocode_not_found")

        # Tier 2: direct UN/LOCODE literal match (e.g. "US LAX" -> "USLAX").
        code_candidate = normalized.replace(" ", "")
        if code_candidate in self.seaports_by_unlocode.index:
            row = self.seaports_by_unlocode.loc[code_candidate]
            return self._row_to_result(raw_destination, row, "direct_code", 100)

        # Tier 3: fuzzy match against port name + country, above threshold only.
        if len(normalized) >= MIN_LENGTH_FOR_FUZZY_MATCH:
            match = process.extractOne(normalized, self._fuzzy_choices, scorer=fuzz.token_sort_ratio)
            if match is not None:
                _, score, idx = match
                if score >= self.fuzzy_threshold:
                    row = self.seaports.loc[idx]
                    return self._row_to_result(raw_destination, row, "fuzzy", score)

        return self._unmatched_result(raw_destination, "unmatched")


# --- Standalone test against real captured AIS strings -------------------------


def _extract_destinations_from_snapshot(snapshot_path: Path) -> list[str]:
    """Pull unique, non-empty Destination strings out of a raw_snapshot.json from Step 3."""
    with open(snapshot_path) as f:
        snapshot = json.load(f)

    destinations = set()
    for vessel in snapshot.get("vessels", []):
        static = vessel.get("ship_static_data")
        if not static:
            continue
        dest = static.get("Message", {}).get("ShipStaticData", {}).get("Destination", "")
        if dest and dest.strip():
            destinations.add(dest.strip())
    return sorted(destinations)


def main() -> None:
    cleaner = DestinationCleaner()

    if not RAW_SNAPSHOT_PATH.exists():
        print(
            f"No snapshot found at {RAW_SNAPSHOT_PATH.relative_to(REPO_ROOT)} — "
            "run scripts/ingest_ais.py first to capture real destination strings to test against."
        )
        return

    raw_destinations = _extract_destinations_from_snapshot(RAW_SNAPSHOT_PATH)
    if not raw_destinations:
        print("Snapshot has no ShipStaticData.Destination values to clean.")
        return

    print(f"Cleaning {len(raw_destinations)} unique destination string(s) from the snapshot:\n")

    results = [cleaner.clean(raw) for raw in raw_destinations]
    counts: dict[str, int] = {}
    for r in results:
        counts[r["match_method"]] = counts.get(r["match_method"], 0) + 1
        score = f"score={r['match_score']}" if r["match_score"] is not None else ""
        if r["unlocode"]:
            print(f"  {r['raw_destination']!r:25} -> {r['unlocode']} ({r['port_name']}) "
                  f"[{r['match_method']} {score}]")
        else:
            print(f"  {r['raw_destination']!r:25} -> UNMATCHED [{r['match_method']}]")

    print(f"\nSummary: {counts}")


if __name__ == "__main__":
    main()