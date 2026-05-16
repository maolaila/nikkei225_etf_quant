from __future__ import annotations

from typing import Any

import pandas as pd

from src.data.data_lake import DataLake
from src.models.model_registry import create_model
from src.utils.paths import ensure_dir
from src.utils.serialization import write_json
from src.validation.leakage_checks import LeakageChecker
from src.validation.purged_split import simple_time_split, walk_forward_splits


def run_walk_forward(config: dict[str, Any], model_name: str | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    lake = DataLake()
    features = lake.read_frame("features", "features")
    labels = lake.read_frame("labels", "labels")
    features["timestamp"] = pd.to_datetime(features["timestamp"])
    labels["timestamp"] = pd.to_datetime(labels["timestamp"])
    checker = LeakageChecker()
    checker.assert_no_label_columns_in_features(features)
    checker.assert_daily_features_lagged(features)

    wf_config = config.get("walk_forward", {})
    windows = walk_forward_splits(
        features["timestamp"],
        train_window_months=int(wf_config.get("train_window_months", 12)),
        test_window_months=int(wf_config.get("test_window_months", 1)),
        step_months=int(wf_config.get("step_months", 1)),
        purge_minutes=int(wf_config.get("purge_minutes", 60)),
        min_train_rows=int(wf_config.get("min_train_rows", 100)),
        min_test_rows=int(wf_config.get("min_test_rows", 1)),
    )
    fallback_used = False
    if not windows:
        split = simple_time_split(features["timestamp"], train_fraction=float(wf_config.get("fallback_train_fraction", 0.35)))
        windows = [split]
        fallback_used = True

    prediction_frames: list[pd.DataFrame] = []
    window_summaries: list[dict[str, Any]] = []
    for index, window in enumerate(windows, start=1):
        train_features = features[(features["timestamp"] >= window.train_start) & (features["timestamp"] <= window.train_end)]
        train_labels = labels[(labels["timestamp"] >= window.train_start) & (labels["timestamp"] <= window.train_end)]
        test_features = features[(features["timestamp"] >= window.test_start) & (features["timestamp"] <= window.test_end)]
        if train_features.empty or train_labels.empty or test_features.empty:
            continue
        model = create_model(config, model_name).fit(train_features, train_labels)
        predictions = model.predict(test_features)
        predictions["walk_forward_window"] = index
        predictions["train_start"] = window.train_start
        predictions["train_end"] = window.train_end
        predictions["test_start"] = window.test_start
        predictions["test_end"] = window.test_end
        prediction_frames.append(predictions)
        window_summaries.append(
            {
                "window": index,
                "train_start": str(window.train_start),
                "train_end": str(window.train_end),
                "test_start": str(window.test_start),
                "test_end": str(window.test_end),
                "train_rows": int(len(train_features)),
                "test_rows": int(len(test_features)),
                "model": model.__class__.__name__,
            }
        )

    if prediction_frames:
        predictions = (
            pd.concat(prediction_frames, ignore_index=True)
            .sort_values(["timestamp", "walk_forward_window"])
            .drop_duplicates("timestamp", keep="first")
            .reset_index(drop=True)
        )
    else:
        split = simple_time_split(features["timestamp"], train_fraction=float(wf_config.get("fallback_train_fraction", 0.35)))
        train_features = features[features["timestamp"] <= split.train_end]
        train_labels = labels[labels["timestamp"] <= split.train_end]
        test_features = features[features["timestamp"] >= split.test_start]
        model = create_model(config, model_name).fit(train_features, train_labels)
        predictions = model.predict(test_features)
        predictions["walk_forward_window"] = 1
        predictions["train_start"] = split.train_start
        predictions["train_end"] = split.train_end
        predictions["test_start"] = split.test_start
        predictions["test_end"] = split.test_end
        fallback_used = True
        window_summaries = [
            {
                "window": 1,
                "train_start": str(split.train_start),
                "train_end": str(split.train_end),
                "test_start": str(split.test_start),
                "test_end": str(split.test_end),
                "train_rows": int(len(train_features)),
                "test_rows": int(len(test_features)),
                "model": model.__class__.__name__,
            }
        ]

    out_dir = ensure_dir("data/models")
    predictions.to_csv(out_dir / "walk_forward_predictions.csv", index=False)
    lake.write_frame(predictions, "models", "walk_forward_predictions", prefer_parquet=False)
    summary = {
        "windows": len(window_summaries),
        "fallback_used": fallback_used,
        "train_start": window_summaries[0]["train_start"],
        "train_end": window_summaries[-1]["train_end"],
        "test_start": window_summaries[0]["test_start"],
        "test_end": window_summaries[-1]["test_end"],
        "rows": int(len(predictions)),
        "model": str(window_summaries[-1]["model"]),
        "window_config": {
            "train_window_months": int(wf_config.get("train_window_months", 12)),
            "test_window_months": int(wf_config.get("test_window_months", 1)),
            "step_months": int(wf_config.get("step_months", 1)),
            "purge_minutes": int(wf_config.get("purge_minutes", 60)),
        },
        "window_summaries": window_summaries,
        "future_leakage_checks": "passed",
    }
    write_json(out_dir / "walk_forward_summary.json", summary)
    return predictions, summary
