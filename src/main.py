from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.backtest.engine import run_backtest
from src.backtest.report import render_existing_report
from src.config.loader import get_nested, load_project_config
from src.data.cleaner import normalize_bars
from src.data.data_lake import DataLake
from src.data.downloader import download_history
from src.data.market_data_importer import import_market_data_collector_bars
from src.data.replay_feed import replay_date
from src.data.symbol_probe import probe_symbols
from src.data.validator import validate_normalized_data
from src.experiments.batch_search import run_batch_search
from src.experiments.event_sensitivity import run_event_sensitivity_backtests
from src.experiments.train_until_target import TargetGates, run_train_until_target
from src.features.feature_pipeline import build_features
from src.labeling.future_return_labeler import build_labels
from src.paper.realtime_runner import run_paper_trade
from src.review.training_cycle_report import generate_training_cycle_report
from src.validation.event_audit import run_event_audit
from src.validation.walk_forward import run_walk_forward


def _default_start(config: dict) -> str:
    return str(get_nested(config, "historical.start_date", "2023-01-01"))


def _default_end(config: dict) -> str:
    return str(get_nested(config, "historical.end_date", "2026-05-15"))


def ensure_raw_normalized(config: dict, start: str | None = None, end: str | None = None) -> None:
    lake = DataLake()
    start = start or _default_start(config)
    end = end or _default_end(config)
    if not lake.exists("raw", "minute_bars"):
        collector_enabled = bool(config.get("market_data_collector", {}).get("enabled", False))
        if collector_enabled:
            import_market_data_collector_bars(config, from_date=start, to_date=end)
        else:
            download_history(config, start, end, provider=str(get_nested(config, "data_sources.historical_primary", "jquants")))
    if not lake.exists("normalized", "minute_bars"):
        normalize_bars(config)


def ensure_features_labels(config: dict, start: str | None = None, end: str | None = None) -> None:
    lake = DataLake()
    ensure_raw_normalized(config, start, end)
    if not lake.exists("features", "features"):
        build_features(config)
    if not lake.exists("labels", "labels"):
        build_labels(config)


def ensure_pipeline(config: dict, start: str | None = None, end: str | None = None) -> None:
    lake = DataLake()
    ensure_features_labels(config, start, end)
    if not lake.exists("models", "walk_forward_predictions"):
        run_walk_forward(config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nikkei 225 ETF historical backtest and paper-trading CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    config_parent = argparse.ArgumentParser(add_help=False)
    config_parent.add_argument("--config-dir", default="config", help="Base config directory")
    config_parent.add_argument(
        "--config-overrides",
        action="append",
        default=None,
        help="Experiment YAML overlay; may be supplied multiple times",
    )

    probe = sub.add_parser("probe-symbols", parents=[config_parent])
    probe.add_argument("--config", default="config/data_sources.yaml")

    download = sub.add_parser("download-history", parents=[config_parent])
    download.add_argument("--provider", default="jquants")
    download.add_argument("--start")
    download.add_argument("--end")
    download.add_argument("--interval", default="1m")

    import_data = sub.add_parser("import-market-data", parents=[config_parent])
    import_data.add_argument("--collector-dir", default=None)
    import_data.add_argument("--provider", default=None)
    import_data.add_argument("--interval", default=None)
    import_data.add_argument("--symbols", default=None, help="Comma-separated symbols; default uses enabled config symbols")
    import_data.add_argument("--from-date", default=None)
    import_data.add_argument("--to-date", default=None)
    import_data.add_argument("--keep-derived", action="store_true")

    normalize = sub.add_parser("normalize-data", parents=[config_parent])
    normalize.add_argument("--start")
    normalize.add_argument("--end")

    validate = sub.add_parser("validate-data", parents=[config_parent])
    validate.add_argument("--start")
    validate.add_argument("--end")

    event_audit = sub.add_parser("event-audit", parents=[config_parent])
    event_audit.add_argument("--output-dir", default=None)

    event_sensitivity = sub.add_parser("event-sensitivity", parents=[config_parent])
    event_sensitivity.add_argument("--output-root", default="data/reports/event_sensitivity")
    event_sensitivity.add_argument("--model", default="latest")

    features = sub.add_parser("build-features", parents=[config_parent])
    features.add_argument("--start")
    features.add_argument("--end")

    labels = sub.add_parser("build-labels", parents=[config_parent])
    labels.add_argument("--start")
    labels.add_argument("--end")

    walk = sub.add_parser("walk-forward", parents=[config_parent])
    walk.add_argument("--start")
    walk.add_argument("--end")
    walk.add_argument("--model", default=None)

    backtest = sub.add_parser("backtest", parents=[config_parent])
    backtest.add_argument("--model", default="latest")

    report = sub.add_parser("report", parents=[config_parent])
    report.add_argument("--type", default="backtest", choices=["backtest", "training-cycles"])

    cycle_report = sub.add_parser("training-cycle-report", parents=[config_parent])
    cycle_report.add_argument("--state-path", default=".codex_quant_agent/state/state.json")
    cycle_report.add_argument("--reports-root", default="data/reports")
    cycle_report.add_argument("--output", default=None)
    cycle_report.add_argument("--archive-current", action="store_true")
    cycle_report.add_argument("--objective-filter", default=None)

    batch = sub.add_parser("batch-search", parents=[config_parent])
    batch.add_argument("--candidates", type=int, default=24)
    batch.add_argument("--seed", type=int, default=42)
    batch.add_argument("--objective", choices=["profit", "stable-loss", "stable-band"], default="profit")
    batch.add_argument("--risk-profile", choices=["default", "aggressive"], default="default")
    batch.add_argument("--target-monthly-return-pct", type=float, default=3.0)
    batch.add_argument("--target-monthly-loss-pct", type=float, default=5.0)
    batch.add_argument("--target-total-loss-pct", type=float, default=5.0)
    batch.add_argument("--max-total-loss-pct", type=float, default=20.0)
    batch.add_argument("--target-total-abs-return-pct", type=float, default=None)
    batch.add_argument("--max-total-abs-return-pct", type=float, default=None)
    batch.add_argument("--min-trades", type=int, default=50)
    batch.add_argument("--min-profit-factor", type=float, default=1.2)
    batch.add_argument("--max-drawdown-pct", type=float, default=15.0)
    batch.add_argument("--min-positive-month-ratio", type=float, default=0.55)
    batch.add_argument("--max-positive-month-ratio", type=float, default=0.20)
    batch.add_argument("--min-monthly-return-floor-pct", type=float, default=-8.0)
    batch.add_argument("--min-negative-month-ratio", type=float, default=0.60)
    batch.add_argument("--min-stable-month-ratio", type=float, default=0.60)
    batch.add_argument("--min-loss-month-ratio", type=float, default=0.0)
    batch.add_argument("--min-walk-forward-windows", type=int, default=6)
    batch.add_argument("--output-root", default="data/reports/experiments")
    batch.add_argument("--skip-pipeline", action="store_true")

    train_until = sub.add_parser("train-until-target", parents=[config_parent])
    train_until.add_argument("--max-cycles", type=int, default=100, help="Use 0 or negative for no fixed cycle cap")
    train_until.add_argument("--candidates-per-cycle", type=int, default=48)
    train_until.add_argument("--seed", type=int, default=42)
    train_until.add_argument("--target-monthly-return-pct", type=float, default=3.0)
    train_until.add_argument("--min-trades", type=int, default=50)
    train_until.add_argument("--min-profit-factor", type=float, default=1.2)
    train_until.add_argument("--max-drawdown-pct", type=float, default=15.0)
    train_until.add_argument("--min-positive-month-ratio", type=float, default=0.55)
    train_until.add_argument("--min-monthly-return-floor-pct", type=float, default=-8.0)
    train_until.add_argument("--min-walk-forward-windows", type=int, default=6)
    train_until.add_argument("--model", default=None)
    train_until.add_argument("--output-root", default="data/reports/long_run")
    train_until.add_argument("--force-rebuild", action="store_true")
    train_until.add_argument("--skip-event-sensitivity", action="store_true")

    replay = sub.add_parser("replay", parents=[config_parent])
    replay.add_argument("--date", required=True)

    paper = sub.add_parser("paper-trade", parents=[config_parent])
    paper.add_argument("--provider", default="twelvedata")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_project_config(args.config_dir, args.config_overrides)

    if args.command == "probe-symbols":
        probes = probe_symbols(config)
        out = Path("data/reports/symbol_probe.csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        probes.to_csv(out, index=False)
        print(probes.to_string(index=False))
        print(f"wrote {out}")
        return 0

    if args.command == "download-history":
        start = args.start or _default_start(config)
        end = args.end or _default_end(config)
        frame, path = download_history(config, start, end, provider=args.provider)
        print(f"downloaded/offline-generated rows={len(frame)} path={path} interval={args.interval}")
        return 0

    if args.command == "import-market-data":
        symbols = [part.strip() for part in args.symbols.split(",") if part.strip()] if args.symbols else None
        frame, path = import_market_data_collector_bars(
            config,
            collector_dir=args.collector_dir,
            provider=args.provider,
            interval=args.interval,
            from_date=args.from_date,
            to_date=args.to_date,
            symbols=symbols,
            invalidate=not args.keep_derived,
        )
        print(f"imported real collector rows={len(frame)} path={path}")
        return 0

    if args.command == "normalize-data":
        if not DataLake().exists("raw", "minute_bars"):
            ensure_raw_normalized(config, args.start, args.end)
        frame, path = normalize_bars(config)
        print(f"normalized rows={len(frame)} path={path}")
        return 0

    if args.command == "validate-data":
        ensure_raw_normalized(config, args.start, args.end)
        report = validate_normalized_data(config)
        print(report)
        return 0

    if args.command == "event-audit":
        ensure_raw_normalized(config)
        result = run_event_audit(config, output_dir=args.output_dir)
        print(result.summary)
        print(f"wrote {result.output_dir}")
        return 0

    if args.command == "event-sensitivity":
        ensure_pipeline(config)
        result = run_event_sensitivity_backtests(config, output_root=args.output_root, model_name=args.model)
        print(result)
        return 0

    if args.command == "build-features":
        ensure_raw_normalized(config, args.start, args.end)
        frame, path = build_features(config)
        print(f"features rows={len(frame)} path={path}")
        return 0

    if args.command == "build-labels":
        ensure_features_labels(config, args.start, args.end)
        frame, path = build_labels(config)
        print(f"labels rows={len(frame)} path={path}")
        return 0

    if args.command == "walk-forward":
        ensure_features_labels(config, args.start, args.end)
        predictions, summary = run_walk_forward(config, model_name=args.model)
        print(f"walk-forward predictions={len(predictions)} summary={summary}")
        return 0

    if args.command == "backtest":
        ensure_pipeline(config)
        metrics, _, _, _ = run_backtest(config, model_name=args.model)
        print(metrics)
        return 0

    if args.command == "report":
        if args.type == "training-cycles":
            path = generate_training_cycle_report()
            print(f"wrote {path}")
            return 0
        ensure_pipeline(config)
        out = Path(get_nested(config, "backtest.report.output_dir", "data/reports/backtest"))
        if not (out / "metrics.json").exists():
            run_backtest(config)
        path = render_existing_report(config, args.type)
        print(f"wrote {path}")
        return 0

    if args.command == "training-cycle-report":
        path = generate_training_cycle_report(
            state_path=args.state_path,
            reports_root=args.reports_root,
            output_path=args.output,
            archive_current=args.archive_current,
            objective_filter=args.objective_filter,
        )
        print(f"wrote {path}")
        return 0

    if args.command == "batch-search":
        if not args.skip_pipeline:
            ensure_pipeline(config)
        ranking, batch_dir = run_batch_search(
            config,
            candidates=args.candidates,
            seed=args.seed,
            objective=args.objective,
            risk_profile=args.risk_profile,
            target_monthly_return_pct=args.target_monthly_return_pct,
            target_monthly_loss_pct=args.target_monthly_loss_pct,
            target_total_loss_pct=args.target_total_loss_pct,
            max_total_loss_pct=args.max_total_loss_pct,
            target_total_abs_return_pct=args.target_total_abs_return_pct,
            max_total_abs_return_pct=args.max_total_abs_return_pct,
            min_trades=args.min_trades,
            min_profit_factor=args.min_profit_factor,
            max_drawdown_pct=args.max_drawdown_pct,
            min_positive_month_ratio=args.min_positive_month_ratio,
            max_positive_month_ratio=args.max_positive_month_ratio,
            min_monthly_return_floor_pct=args.min_monthly_return_floor_pct,
            min_negative_month_ratio=args.min_negative_month_ratio,
            min_stable_month_ratio=args.min_stable_month_ratio,
            min_loss_month_ratio=args.min_loss_month_ratio,
            min_walk_forward_windows=args.min_walk_forward_windows,
            output_root=args.output_root,
        )
        print(f"batch-search wrote {batch_dir}")
        if not ranking.empty:
            columns = [
                "rank",
                "candidate_id",
                "score",
                "passes_target",
                "average_monthly_return_pct",
                "total_return_pct",
                "total_abs_return_pct",
                "dominant_month_ratio",
                "loss_month_ratio",
                "negative_month_ratio",
                "total_loss_pct",
                "drawdown_loss_pct",
                "total_trades",
                "profit_factor",
                "max_drawdown_pct",
            ]
            print(ranking[[column for column in columns if column in ranking.columns]].head(10).to_string(index=False))
        return 0

    if args.command == "train-until-target":
        result = run_train_until_target(
            config,
            max_cycles=args.max_cycles,
            candidates_per_cycle=args.candidates_per_cycle,
            seed=args.seed,
            force_rebuild=args.force_rebuild,
            model_name=args.model,
            output_root=args.output_root,
            gates=TargetGates(
                target_monthly_return_pct=args.target_monthly_return_pct,
                min_trades=args.min_trades,
                min_profit_factor=args.min_profit_factor,
                max_drawdown_pct=args.max_drawdown_pct,
                min_positive_month_ratio=args.min_positive_month_ratio,
                min_monthly_return_floor_pct=args.min_monthly_return_floor_pct,
                min_walk_forward_windows=args.min_walk_forward_windows,
            ),
            run_event_sensitivity=not args.skip_event_sensitivity,
        )
        print(result)
        return 0

    if args.command == "replay":
        ensure_pipeline(config)
        bars = list(replay_date(args.date))
        print(f"replay date={args.date} bars={len(bars)}")
        if bars[:3]:
            for bar in bars[:3]:
                print(bar)
        return 0

    if args.command == "paper-trade":
        path = run_paper_trade(config, args.provider)
        print(f"paper account state written to {path}; no live orders enabled")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
