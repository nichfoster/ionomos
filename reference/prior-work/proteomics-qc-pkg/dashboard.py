"""
Streamlit trending dashboard for the proteomics QC pipeline.

Run it (separately from the watcher/worker) with:
    streamlit run dashboard.py

It reads the same SQLite database the worker writes to, so it always reflects
the latest searches. Refresh the browser (or use the rerun button) after a new
run completes to see it appear.

Layout:
  * Top: latest-run summary cards + protein/peptide counts over time, with a
    DDA / DIA filter. DIA and DDA are never plotted on the same axis because
    their quantities aren't comparable.
  * Per-peptide panel: pick a monitor peptide, see intensity and RT over time.
    Runs where the peptide was NOT detected are flagged distinctly rather than
    drawn as zero or silently skipped.
  * A runs table at the bottom, including any failed/running rows.

Design note: kept deliberately legible — this is an instrument-room QC view,
not a marketing page. Status colour-coding and the not-detected markers carry
the information.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config_loader import load_config
from store import Store


# ----------------------------------------------------------------- data layer

@st.cache_resource
def get_store() -> Store:
    cfg = load_config()
    return Store(cfg["paths"]["database"])


def runs_dataframe(store: Store, acquisition: str | None) -> pd.DataFrame:
    rows = store.get_runs(acquisition if acquisition != "All" else None)
    df = pd.DataFrame([dict(r) for r in rows])
    if not df.empty:
        df["run_timestamp"] = pd.to_datetime(df["run_timestamp"])
        df = df.sort_values("run_timestamp")
    return df


def monitor_dataframe(store: Store, seq: str, charge: int,
                      acquisition: str | None) -> pd.DataFrame:
    rows = store.get_monitor_timeseries(
        seq, charge, acquisition if acquisition != "All" else None
    )
    df = pd.DataFrame([dict(r) for r in rows])
    if not df.empty:
        df["run_timestamp"] = pd.to_datetime(df["run_timestamp"])
        df["detected"] = df["detected"].astype(bool)
    return df


# --------------------------------------------------------------------- charts

ACCENT = "#2dd4bf"      # teal — detected / primary trend
WARN = "#f59e0b"        # amber — RT trend
MISS = "#ef4444"        # red — not-detected markers
GRID = "rgba(148,163,184,0.15)"


def _base_layout(title: str, yaxis: str) -> dict:
    return dict(
        title=dict(text=title, font=dict(size=15)),
        margin=dict(l=10, r=10, t=40, b=10),
        height=300,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0", size=12),
        xaxis=dict(gridcolor=GRID, title="Run date"),
        yaxis=dict(gridcolor=GRID, title=yaxis),
        showlegend=False,
        hovermode="x unified",
    )


def counts_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    ok = df[df["status"] == "success"]
    fig.add_trace(go.Scatter(
        x=ok["run_timestamp"], y=ok["n_proteins"], name="Proteins",
        mode="lines+markers", line=dict(color=ACCENT, width=2),
        marker=dict(size=7),
    ))
    fig.add_trace(go.Scatter(
        x=ok["run_timestamp"], y=ok["n_peptides"], name="Peptides",
        mode="lines+markers", line=dict(color=WARN, width=2),
        marker=dict(size=7), yaxis="y2",
    ))
    layout = _base_layout("Identifications over time", "Proteins")
    layout["showlegend"] = True
    layout["legend"] = dict(orientation="h", y=1.15, x=0)
    layout["yaxis2"] = dict(
        title="Peptides", overlaying="y", side="right", gridcolor="rgba(0,0,0,0)"
    )
    fig.update_layout(**layout)
    return fig


def monitor_chart(df: pd.DataFrame, value_col: str, title: str,
                  yaxis: str, color: str) -> go.Figure:
    fig = go.Figure()
    det = df[df["detected"]]
    fig.add_trace(go.Scatter(
        x=det["run_timestamp"], y=det[value_col], mode="lines+markers",
        line=dict(color=color, width=2), marker=dict(size=8),
        name=yaxis,
    ))
    # Not-detected runs: mark them on the time axis at the bottom so a gap is
    # explicit, not invisible. Placed at the min of detected values (or 0).
    miss = df[~df["detected"]]
    if not miss.empty:
        baseline = det[value_col].min() if not det.empty else 0
        fig.add_trace(go.Scatter(
            x=miss["run_timestamp"], y=[baseline] * len(miss),
            mode="markers", marker=dict(size=11, color=MISS, symbol="x"),
            name="Not detected",
        ))
    layout = _base_layout(title, yaxis)
    if not miss.empty:
        layout["showlegend"] = True
        layout["legend"] = dict(orientation="h", y=1.15, x=0)
    fig.update_layout(**layout)
    return fig


# ----------------------------------------------------------------------- page

def main() -> None:
    st.set_page_config(page_title="Proteomics QC", layout="wide",
                       page_icon="🧪")
    store = get_store()

    st.markdown("## 🧪 Proteomics QC — trending dashboard")
    st.caption("Automated FragPipe QC. Reads live from the pipeline database.")

    acquisition = st.radio(
        "Acquisition", ["All", "DDA", "DIA"], horizontal=True, index=0
    )

    df = runs_dataframe(store, acquisition)
    if df.empty:
        st.info("No runs yet. Drop a .raw file into a watched folder to begin.")
        return

    # --- summary cards for the most recent successful run ---
    ok = df[df["status"] == "success"]
    if not ok.empty:
        latest = ok.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Latest run", latest["run_id"])
        c2.metric("Proteins", f"{int(latest['n_proteins']):,}")
        c3.metric("Peptides", f"{int(latest['n_peptides']):,}")
        c4.metric("Acquisition", latest["acquisition"])

    # --- counts over time ---
    st.plotly_chart(counts_chart(df), use_container_width=True)

    # --- per-peptide panel ---
    st.markdown("### Monitor peptides")
    peptides = store.get_monitored_peptides()
    if not peptides:
        st.info("No monitor-peptide data yet.")
    else:
        labels = [f"{p['sequence']}  (z{p['charge']})" for p in peptides]
        choice = st.selectbox("Peptide", labels, index=0)
        idx = labels.index(choice)
        seq, charge = peptides[idx]["sequence"], peptides[idx]["charge"]

        mdf = monitor_dataframe(store, seq, charge, acquisition)
        if mdf.empty:
            st.info("No data for this peptide in the selected acquisition.")
        else:
            n_missing = int((~mdf["detected"]).sum())
            if n_missing:
                st.warning(
                    f"⚠️ {seq} (z{charge}) was not detected in {n_missing} "
                    f"of {len(mdf)} runs — flagged on the charts below."
                )
            left, right = st.columns(2)
            with left:
                st.plotly_chart(
                    monitor_chart(mdf, "intensity", "Intensity over time",
                                  "Intensity", ACCENT),
                    use_container_width=True,
                )
            with right:
                st.plotly_chart(
                    monitor_chart(mdf, "rt", "Retention time over time",
                                  "RT", WARN),
                    use_container_width=True,
                )

    # --- runs table (includes failed/running) ---
    st.markdown("### All runs")
    show = df.sort_values("run_timestamp", ascending=False)[
        ["run_id", "acquisition", "run_timestamp", "n_proteins",
         "n_peptides", "status"]
    ].copy()
    show["run_timestamp"] = show["run_timestamp"].dt.strftime("%Y-%m-%d %H:%M")
    st.dataframe(show, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
