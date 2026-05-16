from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from rich.logging import RichHandler


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, markup=False)],
        force=True,
    )


def log_request(
    logger: logging.Logger,
    provider: str,
    symbol: str,
    code: str,
    endpoint_type: str,
    from_date: object,
    to_date: object,
    page: int,
    rows: int | None = None,
) -> None:
    suffix = "" if rows is None else f" rows={rows}"
    logger.info(
        "request provider=%s symbol=%s code=%s endpoint=%s from=%s to=%s page=%s%s",
        provider,
        symbol,
        code,
        endpoint_type,
        from_date,
        to_date,
        page,
        suffix,
    )


def log_download_summary(
    logger: logging.Logger,
    symbol: str,
    interval: str,
    frame: pd.DataFrame,
    output_path: Path,
) -> None:
    first = frame["datetime"].min() if not frame.empty and "datetime" in frame else ""
    last = frame["datetime"].max() if not frame.empty and "datetime" in frame else ""
    logger.info(
        "downloaded symbol=%s interval=%s rows=%s first=%s last=%s output=%s",
        symbol,
        interval,
        len(frame),
        first,
        last,
        output_path,
    )
