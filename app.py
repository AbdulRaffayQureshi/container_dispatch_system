from pathlib import Path
import pandas as pd
import pydeck as pdk
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent
VESSELS_PATH = REPO_ROOT / "data" / "vessels.parquet"
VOYAGES_PATH = REPO_ROOT / "data" / "voyages.parquet"

st.set_page_config(page_title="Oceanic Command Center", layout="wide")

# Minimal CSS just for the glassmorphism KPI cards.
# Streamlit's native engine handles the rest (tables, buttons, inputs) flawlessly.
st.markdown("""
<style>
.glass-card {
    background: rgba(13, 18, 32, 0.6);
    border: 1px solid rgba(57, 255, 158, 0.35);
    border-radius: 18px;
    padding: 18px 22px;
    box-shadow: 0 0 22px rgba(57, 255, 158, 0.18);
    margin-bottom: 8px;
}
.glass-card.alert {
    border-color: rgba(255, 61, 90, 0.45);
    box-shadow: 0 0 22px rgba(255, 61, 90, 0.18);
}
.glass-card .label {
    font-size: 0.8rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #39ff9e;
    opacity: 0.9;
}
.glass-card.alert .label {
    color: #ff3d5a;
}
.glass-card .value {
    font-size: 2.1rem;
    font-weight: 700;
    color: #e6f7ff;
}
#MainMenu, footer, header {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

def kpi_card(col, label, value, alert=False):
    css_class = "glass-card alert" if alert else "glass-card"
    col.markdown(
        f'<div class="{css_class}"><div class="label">{label}</div>'
        f'<div class="value">{value}</div></div>',
        unsafe_allow_html=True,
    )

@st.cache_data(ttl=300)
def load_data():
    vessels = pd.read_parquet(VESSELS_PATH)
    voyages = pd.read_parquet(VOYAGES_PATH)
    return voyages.merge(vessels[["mmsi", "vessel_name"]], on="mmsi", how="left")

st.title("🌙 🚢 Oceanic Command Center ⚓")

if not VESSELS_PATH.exists() or not VOYAGES_PATH.exists():
    st.warning("No data yet — run the ingestion pipeline first.")
    st.stop()

df = load_data()

active_voyages = len(df)
blackout_count = int(df["ais_blackout_flag"].sum())
avg_speed = df["speed"].dropna().mean()
avg_speed_display = f"{avg_speed:.1f} kn" if pd.notna(avg_speed) else "—"

col1, col2, col3 = st.columns(3)
kpi_card(col1, "🚢 Active Voyages", active_voyages)
kpi_card(col2, "⚠️ Blackout Warnings", blackout_count, alert=blackout_count > 0)
kpi_card(col3, "📦 Avg Fleet Speed", avg_speed_display)

st.markdown("### 🌐 Live Fleet Positions")

map_df = df.dropna(subset=["current_lat", "current_lon"]).copy()
map_df["color"] = map_df["ais_blackout_flag"].apply(
    lambda flag: [255, 61, 90, 220] if flag else [57, 255, 158, 200]
)
map_df["vessel_display"] = map_df["vessel_name"].replace("", None).fillna("Unknown vessel")
dest_fallback = map_df["dest_port_name"].replace("", None)
dest_fallback = dest_fallback.fillna(map_df["dest_raw_string"].replace("", None))
map_df["dest_display"] = dest_fallback.fillna("Unresolved")
map_df["speed_display"] = map_df["speed"].apply(lambda s: f"{s:.1f}" if pd.notna(s) else "N/A")

if not map_df.empty:
    view_state = pdk.ViewState(
        latitude=map_df["current_lat"].mean(),
        longitude=map_df["current_lon"].mean(),
        zoom=4,
        pitch=45,
        bearing=15,
    )

    scatter_layer = pdk.Layer(
        "ScatterplotLayer",
        data=map_df,
        get_position="[current_lon, current_lat]",
        get_fill_color="color",
        get_radius=500,
        radius_min_pixels=6,
        radius_max_pixels=18,
        pickable=True,
        stroked=True,
        get_line_color=[255, 255, 255, 80],
        line_width_min_pixels=1,
    )

    deck = pdk.Deck(
        layers=[scatter_layer],
        initial_view_state=view_state,
        map_style="dark",
        tooltip={
            "html": "<b>{vessel_display}</b><br/>Dest: {dest_display}<br/>Speed: {speed_display} kn",
            "style": {"backgroundColor": "#0d1220", "color": "#e6f7ff"},
        },
    )
    st.pydeck_chart(deck)
else:
    st.info("No positional data available yet.")

st.markdown("### 📦 Voyage Manifest")

blackout_filter = st.multiselect(
    "Blackout status",
    options=["Active", "Blackout"],
    default=["Active", "Blackout"],
)
search = st.text_input("Search destination or vessel")

filtered = df.copy()
statuses = filtered["ais_blackout_flag"].map({True: "Blackout", False: "Active"})
filtered = filtered[statuses.isin(blackout_filter)]

if search:
    mask = (
        filtered["vessel_name"].fillna("").str.contains(search, case=False)
        | filtered["dest_port_name"].fillna("").str.contains(search, case=False)
        | filtered["dest_raw_string"].fillna("").str.contains(search, case=False)
    )
    filtered = filtered[mask]

display_df = filtered.copy()
text_cols = ["origin_unlocode", "dest_unlocode", "dest_port_name", "dest_raw_string", "vessel_name"]
for col in text_cols:
    display_df[col] = display_df[col].replace("", None).fillna("—")

# Native Streamlit dataframe. Zero CSS hacks needed. 
# Streamlit will automatically style the headers, borders, and tools to match the dark theme in config.toml.
st.dataframe(display_df, width="stretch", hide_index=True)

st.download_button(
    "⬇ Export Filtered CSV",
    data=filtered.to_csv(index=False).encode("utf-8"),
    file_name="voyages_export.csv",
    mime="text/csv",
)