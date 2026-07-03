"""
Pipeline Throughput Analysis
-----------------------------
Downloads real, public pipeline throughput & capacity data from the Canada
Energy Regulator (CER) open data portal, cleans it, calculates a rolling
average and utilization variance, and produces a chart + summary stats.

Data source (public, no auth required):
https://www.cer-rec.gc.ca/open/energy/throughput-capacity/keystone-throughput-and-capacity.csv

Author: Mo Yusuf
"""

import pandas as pd
import matplotlib.pyplot as plt
import requests
from pathlib import Path

DATA_URL = "https://www.cer-rec.gc.ca/open/energy/throughput-capacity/keystone-throughput-and-capacity.csv"
RAW_PATH = Path("data/keystone_raw.csv")
CLEAN_PATH = Path("output/keystone_clean.csv")
CHART_PATH = Path("output/keystone_throughput_chart.png")
SUMMARY_PATH = Path("output/summary_stats.txt")

ROLLING_WINDOW = 3  # months


def download_data(url: str, dest: Path) -> Path:
    """Download the CSV if not already present locally."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"Using cached file: {dest}")
        return dest
    print(f"Downloading data from {url} ...")
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    print(f"Saved raw data to {dest}")
    return dest


def load_and_clean(raw_path: Path) -> pd.DataFrame:
    """Load the raw CER CSV and standardize it into a tidy monthly dataframe."""
    df = pd.read_csv(raw_path)
    df.columns = [c.strip() for c in df.columns]

    print("Raw columns found in file:", list(df.columns))

    # CER CSVs sometimes ship with slightly different column names/casing,
    # and can include MORE THAN ONE capacity-related column (e.g. "Available
    # Capacity" and "Nameplate Capacity"). Matching any column containing
    # "capacity" and blindly renaming all of them to "capacity" silently
    # collapses them into duplicate columns, which then get double-counted
    # during aggregation. To avoid that, only rename the FIRST matching
    # column for each target field, and explicitly warn if more than one
    # candidate column was found so it doesn't happen silently again.
    rename_map = {}
    matched_targets = {}
    for col in df.columns:
        low = col.lower()
        if low == "date":
            target = "date"
        elif low == "year":
            target = "year"
        elif low == "month":
            target = "month"
        elif "throughput" in low:
            target = "throughput"
        elif "available capacity" in low:
            target = "capacity"
        elif "capacity" in low:
            target = "capacity"
        elif "point" in low:
            target = "key_point"
        elif "product" in low:
            target = "product"
        else:
            continue

        if target in matched_targets:
            print(f"WARNING: column '{col}' also looks like '{target}' but "
                  f"'{matched_targets[target]}' was already matched first. "
                  f"Skipping '{col}' to avoid double-counting. Check this manually "
                  f"if the result looks wrong.")
            continue

        matched_targets[target] = col
        rename_map[col] = target

    df = df.rename(columns=rename_map)

    # Drop fully-empty rows/columns, coerce types, drop rows with no throughput value
    df = df.dropna(axis=1, how="all").dropna(how="all")

    # Build a proper date column from either a single "date" field or
    # separate year/month columns (CER files commonly use the latter).
    if "date" not in df.columns and {"year", "month"}.issubset(df.columns):
        df["date"] = pd.to_datetime(
            df["year"].astype(str) + "-" + df["month"].astype(str) + "-01", errors="coerce"
        )
    elif "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    if "throughput" in df.columns:
        df["throughput"] = pd.to_numeric(df["throughput"], errors="coerce")
    if "capacity" in df.columns:
        df["capacity"] = pd.to_numeric(df["capacity"], errors="coerce")

    df = df.dropna(subset=["throughput"])
    df = df.sort_values("date") if "date" in df.columns else df

    return df.reset_index(drop=True)


def analyze(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate rolling average throughput and utilization variance."""
    # If multiple key points exist, aggregate to a single monthly system total
    if "key_point" in df.columns and "date" in df.columns:
        monthly = df.groupby("date", as_index=False).agg(
            throughput=("throughput", "sum"),
            capacity=("capacity", "sum") if "capacity" in df.columns else ("throughput", "sum"),
        )
    else:
        monthly = df.copy()

    monthly = monthly.sort_values("date").reset_index(drop=True)
    monthly[f"rolling_avg_{ROLLING_WINDOW}mo"] = (
        monthly["throughput"].rolling(window=ROLLING_WINDOW, min_periods=1).mean()
    )

    if "capacity" in monthly.columns:
        monthly["utilization_pct"] = (monthly["throughput"] / monthly["capacity"]) * 100
        monthly["utilization_variance"] = monthly["utilization_pct"].diff()

    return monthly


def make_chart(monthly: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(11, 6))

    ax.plot(monthly["date"], monthly["throughput"], label="Monthly Throughput", alpha=0.5, linewidth=1.5)
    ax.plot(
        monthly["date"],
        monthly[f"rolling_avg_{ROLLING_WINDOW}mo"],
        label=f"{ROLLING_WINDOW}-Month Rolling Average",
        linewidth=2.5,
    )
    if "capacity" in monthly.columns:
        ax.plot(monthly["date"], monthly["capacity"], label="Available Capacity", linestyle="--", alpha=0.6)

    ax.set_title("Keystone Pipeline — Monthly Throughput vs. Rolling Average")
    ax.set_xlabel("Date")
    ax.set_ylabel("Volume")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    print(f"Chart saved to {out_path}")


def write_summary(monthly: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("PIPELINE THROUGHPUT ANALYSIS — SUMMARY\n")
    lines.append(f"Records analyzed: {len(monthly)}")
    if "date" in monthly.columns and monthly["date"].notna().any():
        lines.append(f"Date range: {monthly['date'].min().date()} to {monthly['date'].max().date()}")
    lines.append(f"Average monthly throughput: {monthly['throughput'].mean():,.1f}")
    lines.append(f"Max monthly throughput: {monthly['throughput'].max():,.1f}")
    lines.append(f"Min monthly throughput: {monthly['throughput'].min():,.1f}")
    if "utilization_pct" in monthly.columns:
        lines.append(f"Average utilization: {monthly['utilization_pct'].mean():.1f}%")
        biggest_swing = monthly["utilization_variance"].abs().max()
        lines.append(f"Largest month-over-month utilization swing: {biggest_swing:.1f} percentage points")
    text = "\n".join(lines)
    out_path.write_text(text)
    print("\n" + text)


def main():
    raw = download_data(DATA_URL, RAW_PATH)
    df = load_and_clean(raw)
    monthly = analyze(df)
    CLEAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(CLEAN_PATH, index=False)
    print(f"Cleaned data saved to {CLEAN_PATH}")
    make_chart(monthly, CHART_PATH)
    write_summary(monthly, SUMMARY_PATH)


if __name__ == "__main__":
    main()
