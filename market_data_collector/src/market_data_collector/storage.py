from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from market_data_collector.config import get_settings


def normalize_for_storage(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    frame = df.copy()
    if "datetime" not in frame.columns:
        return frame
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    frame = frame.sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)
    if "date" not in frame.columns:
        frame["date"] = frame["datetime"].dt.strftime("%Y-%m-%d")
    if "time" not in frame.columns:
        frame["time"] = frame["datetime"].dt.strftime("%H:%M:%S")
    return frame


def save_dataframe(df: pd.DataFrame, path: Path, format: str = "parquet", overwrite: bool = False) -> Path:
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists. Use --overwrite or --incremental to replace it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = normalize_for_storage(df)
    if format == "parquet":
        frame.to_parquet(path, index=False, engine="pyarrow")
    elif format == "csv":
        frame.to_csv(path, index=False, encoding="utf-8")
    else:
        raise ValueError(f"Unsupported output format: {format}")
    return path


def split_by_year(df: pd.DataFrame) -> dict[int, pd.DataFrame]:
    if df.empty:
        return {}
    frame = normalize_for_storage(df)
    years = frame["datetime"].dt.year
    return {int(year): group.reset_index(drop=True) for year, group in frame.groupby(years, sort=True)}


def build_output_path(
    provider: str,
    interval: str,
    symbol: str,
    year: int,
    raw_or_processed: str,
    file_format: str = "parquet",
    data_dir: Path | None = None,
) -> Path:
    base = data_dir or get_settings().resolved_data_dir
    return base / raw_or_processed / provider / interval / symbol / f"{year}.{file_format}"


def load_symbol_data(
    symbol: str,
    provider: str,
    interval: str,
    from_date: date,
    to_date: date,
    raw_or_processed: str = "raw",
    data_dir: Path | None = None,
) -> pd.DataFrame:
    base = data_dir or get_settings().resolved_data_dir
    directory = base / raw_or_processed / provider / interval / symbol
    if not directory.exists():
        raise FileNotFoundError(f"No data directory found: {directory}")
    frames: list[pd.DataFrame] = []
    for path in sorted(directory.glob("*.parquet")):
        frames.append(pd.read_parquet(path))
    for path in sorted(directory.glob("*.csv")):
        frames.append(pd.read_csv(path))
    if not frames:
        raise FileNotFoundError(f"No data files found under {directory}")
    frame = normalize_for_storage(pd.concat(frames, ignore_index=True))
    start = pd.Timestamp(from_date)
    end = pd.Timestamp(to_date) + pd.Timedelta(days=1)
    naive_dt = pd.to_datetime(frame["datetime"]).dt.tz_localize(None)
    return frame[(naive_dt >= start) & (naive_dt < end)].reset_index(drop=True)


def save_partitioned(
    df: pd.DataFrame,
    provider: str,
    interval: str,
    symbol: str,
    raw_or_processed: str,
    file_format: str,
    overwrite: bool,
    incremental: bool = False,
    data_dir: Path | None = None,
) -> list[Path]:
    paths: list[Path] = []
    for year, year_frame in split_by_year(df).items():
        path = build_output_path(provider, interval, symbol, year, raw_or_processed, file_format, data_dir)
        output = year_frame
        if incremental and path.exists():
            existing = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
            output = pd.concat([existing, year_frame], ignore_index=True)
            overwrite = True
        save_dataframe(output, path, file_format, overwrite=overwrite)
        paths.append(path)
    return paths
