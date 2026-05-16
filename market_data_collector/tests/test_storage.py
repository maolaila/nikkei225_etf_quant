from __future__ import annotations

import pandas as pd
import pytest

from market_data_collector.storage import build_output_path, load_symbol_data, save_dataframe, split_by_year


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "datetime": pd.Timestamp("2024-12-30 15:30:00"),
                "date": "2024-12-30",
                "time": "15:30:00",
                "symbol": "1570",
                "code": "15700",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 100,
                "turnover": 1000,
                "provider": "unit",
                "fetched_at": "2026-01-01T00:00:00Z",
            },
            {
                "datetime": pd.Timestamp("2025-01-06 15:30:00"),
                "date": "2025-01-06",
                "time": "15:30:00",
                "symbol": "1570",
                "code": "15700",
                "open": 2.0,
                "high": 3.0,
                "low": 1.5,
                "close": 2.5,
                "volume": 200,
                "turnover": 2000,
                "provider": "unit",
                "fetched_at": "2026-01-01T00:00:00Z",
            },
        ]
    )


def test_parquet_save_and_load(tmp_path) -> None:
    path = build_output_path("unit", "1d", "1570", 2025, "raw", data_dir=tmp_path)
    save_dataframe(_frame().iloc[[1]], path, "parquet", overwrite=False)
    loaded = load_symbol_data(
        "1570",
        "unit",
        "1d",
        pd.Timestamp("2025-01-01").date(),
        pd.Timestamp("2025-12-31").date(),
        data_dir=tmp_path,
    )
    assert len(loaded) == 1
    assert loaded.iloc[0]["symbol"] == "1570"


def test_overwrite_false_does_not_overwrite(tmp_path) -> None:
    path = tmp_path / "sample.parquet"
    save_dataframe(_frame(), path, "parquet", overwrite=False)
    with pytest.raises(FileExistsError):
        save_dataframe(_frame(), path, "parquet", overwrite=False)


def test_split_by_year() -> None:
    split = split_by_year(_frame())
    assert sorted(split) == [2024, 2025]
    assert len(split[2024]) == 1
    assert len(split[2025]) == 1
