from __future__ import annotations

import importlib.util
import logging
import platform
from datetime import date
from typing import Annotated

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from market_data_collector.config import get_settings, masked_secret
from market_data_collector.logging_config import configure_logging, log_download_summary
from market_data_collector.models import ETF_SYMBOLS, SUPPORTED_INTERVALS, SUPPORTED_PROVIDERS, parse_symbols
from market_data_collector.providers import JQuantsProvider, ProviderError, TwelveDataProvider, YFinanceProvider
from market_data_collector.providers.base import MarketDataProvider
from market_data_collector.resample import resample_ohlcv
from market_data_collector.storage import load_symbol_data, save_dataframe, save_partitioned
from market_data_collector.validate import validate_dataframe

app = typer.Typer(no_args_is_help=True, help="Japanese ETF historical market data collector.")
console = Console()
LOGGER = logging.getLogger(__name__)


def _provider(name: str) -> MarketDataProvider:
    settings = get_settings()
    if name == "jquants":
        return JQuantsProvider(settings)
    if name == "twelvedata":
        return TwelveDataProvider(settings)
    if name == "yfinance":
        return YFinanceProvider(settings)
    supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
    raise typer.BadParameter(f"Unsupported provider {name!r}. Supported providers: {supported}")


def _ensure_interval(interval: str) -> str:
    if interval not in SUPPORTED_INTERVALS:
        supported = ", ".join(sorted(SUPPORTED_INTERVALS))
        raise typer.BadParameter(f"Unsupported interval {interval!r}. Supported intervals: {supported}")
    return interval


def _ensure_format(output_format: str) -> str:
    if output_format not in {"parquet", "csv"}:
        raise typer.BadParameter("format must be parquet or csv")
    return output_format


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid date {value!r}; expected YYYY-MM-DD") from exc


@app.command()
def download(
    provider: Annotated[str, typer.Option("--provider", help="jquants / twelvedata / yfinance")] = "jquants",
    symbols: Annotated[str, typer.Option("--symbols", help="Comma-separated ETF symbols")] = "1357,1570,1321,1571",
    interval: Annotated[str, typer.Option("--interval", help="1d / 1m / 3min / 5min / 30min")] = "1d",
    from_date: Annotated[str, typer.Option("--from-date", help="YYYY-MM-DD")] = "2024-01-01",
    to_date: Annotated[str, typer.Option("--to-date", help="YYYY-MM-DD")] = "2024-12-31",
    output_format: Annotated[str, typer.Option("--format", help="parquet / csv")] = "parquet",
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Overwrite existing files")] = False,
    incremental: Annotated[bool, typer.Option("--incremental", help="Merge with existing yearly files")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print requests without calling APIs")] = False,
    max_pages: Annotated[int | None, typer.Option("--max-pages", help="Stop after N pages for debugging")] = None,
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    provider = provider.lower()
    interval = _ensure_interval(interval)
    output_format = _ensure_format(output_format)
    start = _parse_date(from_date)
    end = _parse_date(to_date)
    selected_symbols = parse_symbols(symbols)
    client = _provider(provider)
    for symbol in selected_symbols:
        try:
            frame = (
                client.fetch_daily(symbol, start, end, dry_run=dry_run, max_pages=max_pages)
                if interval == "1d"
                else client.fetch_intraday(symbol, interval, start, end, dry_run=dry_run, max_pages=max_pages)
            )
            if dry_run:
                continue
            paths = save_partitioned(
                frame,
                provider=provider,
                interval=interval,
                symbol=symbol,
                raw_or_processed="raw",
                file_format=output_format,
                overwrite=overwrite,
                incremental=incremental,
                data_dir=settings.resolved_data_dir,
            )
            for path in paths:
                log_download_summary(LOGGER, symbol, interval, frame, path)
                console.print(f"[green]saved[/green] {path}")
        except (ProviderError, FileExistsError, ValueError) as exc:
            console.print(f"[red]download failed for {symbol}:[/red] {exc}")
            raise typer.Exit(1) from exc


@app.command()
def resample(
    provider: Annotated[str, typer.Option("--provider")] = "jquants",
    symbols: Annotated[str, typer.Option("--symbols")] = "1357,1570,1321,1571",
    source_interval: Annotated[str, typer.Option("--source-interval")] = "1m",
    target_intervals: Annotated[str, typer.Option("--target-intervals")] = "3min,5min,30min,1d",
    from_date: Annotated[str, typer.Option("--from-date")] = "2025-01-01",
    to_date: Annotated[str, typer.Option("--to-date")] = "2025-12-31",
    output_format: Annotated[str, typer.Option("--format")] = "parquet",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = True,
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if source_interval != "1m":
        raise typer.BadParameter("resample currently expects --source-interval 1m")
    start = _parse_date(from_date)
    end = _parse_date(to_date)
    targets = [_ensure_interval(item.strip()) for item in target_intervals.split(",") if item.strip()]
    selected_symbols = parse_symbols(symbols)
    for symbol in selected_symbols:
        try:
            source = load_symbol_data(
                symbol,
                provider,
                source_interval,
                start,
                end,
                data_dir=settings.resolved_data_dir,
            )
        except FileNotFoundError as exc:
            console.print(f"[red]No 1m data for {symbol}.[/red] Download minute data before resampling. {exc}")
            raise typer.Exit(1) from exc
        for target in targets:
            resampled = resample_ohlcv(source, target)
            paths = save_partitioned(
                resampled,
                provider=provider,
                interval=target,
                symbol=symbol,
                raw_or_processed="processed",
                file_format=_ensure_format(output_format),
                overwrite=overwrite,
                incremental=False,
                data_dir=settings.resolved_data_dir,
            )
            for path in paths:
                console.print(f"[green]saved[/green] {path}")


@app.command()
def validate(
    provider: Annotated[str, typer.Option("--provider")] = "jquants",
    symbols: Annotated[str, typer.Option("--symbols")] = "1357,1570,1321,1571",
    interval: Annotated[str, typer.Option("--interval")] = "1d",
    from_date: Annotated[str, typer.Option("--from-date")] = "2024-01-01",
    to_date: Annotated[str, typer.Option("--to-date")] = "2024-12-31",
) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    interval = _ensure_interval(interval)
    start = _parse_date(from_date)
    end = _parse_date(to_date)
    reports: list[pd.DataFrame] = []
    for symbol in parse_symbols(symbols):
        try:
            frame = load_symbol_data(symbol, provider, interval, start, end, "raw", settings.resolved_data_dir)
        except FileNotFoundError:
            try:
                frame = load_symbol_data(
                    symbol,
                    provider,
                    interval,
                    start,
                    end,
                    "processed",
                    settings.resolved_data_dir,
                )
            except FileNotFoundError:
                frame = pd.DataFrame()
        reports.append(validate_dataframe(frame, provider, interval, symbol))
    report = pd.concat(reports, ignore_index=True)
    report_dir = settings.resolved_data_dir / "processed" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"validation_{provider}_{interval}_{from_date}_{to_date}.csv"
    save_dataframe(report, path, "csv", overwrite=True)
    console.print(f"[green]wrote[/green] {path}")
    console.print(report.to_string(index=False))


@app.command("list-symbols")
def list_symbols() -> None:
    table = Table(title="Supported Nikkei 225 Related ETFs")
    for column in ["symbol", "jquants_code", "twelvedata_symbol", "yfinance_ticker", "name"]:
        table.add_column(column)
    for symbol, item in ETF_SYMBOLS.items():
        table.add_row(
            symbol,
            str(item["jquants_code"]),
            str(item["twelvedata_symbol"]),
            str(item["yfinance_ticker"]),
            str(item["name"]),
        )
    console.print(table)


@app.command()
def doctor() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    data_dir = settings.resolved_data_dir
    for path in [data_dir, data_dir / "raw", data_dir / "processed", data_dir / "processed" / "reports"]:
        path.mkdir(parents=True, exist_ok=True)
    table = Table(title="market-data-collector doctor")
    table.add_column("item")
    table.add_column("value")
    table.add_row("python", platform.python_version())
    table.add_row("data_dir", str(data_dir))
    table.add_row("default_provider", settings.default_provider)
    table.add_row("JQUANTS_API_KEY", masked_secret(settings.jquants_api_key))
    table.add_row("TWELVEDATA_API_KEY", masked_secret(settings.twelvedata_api_key))
    table.add_row("JQUANTS_ENABLE_MINUTE", str(settings.jquants_enable_minute))
    dependencies = [
        "httpx",
        "pandas",
        "pyarrow",
        "pydantic",
        "pydantic_settings",
        "tenacity",
        "typer",
        "rich",
        "yfinance",
    ]
    for dependency in dependencies:
        table.add_row(f"dependency:{dependency}", "installed" if importlib.util.find_spec(dependency) else "missing")
    console.print(table)


if __name__ == "__main__":
    app()
