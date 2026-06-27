# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
SCRAPLING_SRC = ROOT / "Scrapling-main"
if SCRAPLING_SRC.exists():
    sys.path.insert(0, str(SCRAPLING_SRC))

from scrapling.fetchers import Fetcher  # noqa: E402


MARKET_URL = "https://cn.investing.com/markets/united-states"
INSTRUMENTS = [
    {
        "name": "美国10年期国债",
        "title": "美国十年期国债收益率",
        "url": "https://cn.investing.com/rates-bonds/u.s.-10-year-bond-yield",
    },
    {
        "name": "美国2年期国债",
        "title": "美国二年期国债收益率",
        "url": "https://cn.investing.com/rates-bonds/u.s.-2-year-bond-yield",
    },
    {
        "name": "日本10年期国债",
        "title": "日本十年期国债收益率",
        "url": "https://cn.investing.com/rates-bonds/japan-10-year-bond-yield",
    },
    {
        "name": "日本2年期国债",
        "title": "日本二年期国债收益率",
        "url": "https://cn.investing.com/rates-bonds/japan-2-year-bond-yield",
    },
    {
        "name": "中国10年期国债",
        "title": "中国十年期国债收益率",
        "url": "https://cn.investing.com/rates-bonds/china-10-year-bond-yield",
    },
    {
        "name": "中国2年期国债",
        "title": "中国二年期国债收益率",
        "url": "https://cn.investing.com/rates-bonds/china-2-year-bond-yield",
    },
    {
        "name": "德国10年期国债",
        "title": "德国十年期国债收益率",
        "url": "https://cn.investing.com/rates-bonds/germany-10-year-bond-yield",
    },
    {
        "name": "德国2年期国债",
        "title": "德国二年期国债收益率",
        "url": "https://cn.investing.com/rates-bonds/germany-2-year-bond-yield",
    },
]

NUMBER_RE = re.compile(r"^-?\d+(?:,\d{3})*(?:\.\d+)?$")
CHANGE_NUMBER_RE = re.compile(r"^[+-]?\d+(?:,\d{3})*(?:\.\d+)?$")
PERCENT_RE = re.compile(r"^\(?(?P<percent>[+-]?\d+(?:\.\d+)?%)\)?$")
CHANGE_RE = re.compile(
    r"(?P<change>[+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"\((?P<percent>[+-]?\d+(?:\.\d+)?%)\)"
)


@dataclass
class BondQuote:
    name: str
    yield_rate: str
    change: str
    change_percent: str
    time: str
    source_url: str
    fetched_at: str


def fetch_page(url: str, *, dynamic: bool = False):
    headers = {
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    }
    if not dynamic:
        return Fetcher.get(url, headers=headers, timeout=30, impersonate="chrome")

    from scrapling.fetchers import DynamicFetcher

    return DynamicFetcher.fetch(
        url,
        headless=True,
        timeout=60000,
        wait=1500,
        network_idle=True,
        disable_resources=True,
        extra_headers=headers,
    )


def page_lines(page) -> list[str]:
    text = str(page.get_all_text(separator="\n", strip=True))
    return [line.strip() for line in text.splitlines() if line.strip()]


def normalize_number(value: str) -> str:
    return value.replace(",", "")


def find_after(lines: list[str], start: int, predicate, *, limit: int = 30) -> tuple[int, str]:
    stop = min(len(lines), start + limit)
    for index in range(start, stop):
        if predicate(lines[index]):
            return index, lines[index]
    raise ValueError("matching value was not found")


def parse_change(lines: list[str], start: int) -> tuple[int, str, str]:
    change_index, change_line = find_after(
        lines,
        start,
        lambda line: bool(CHANGE_RE.search(line) or CHANGE_NUMBER_RE.match(line)),
        limit=20,
    )

    combined = CHANGE_RE.search(change_line)
    if combined is not None:
        return (
            change_index,
            normalize_number(combined.group("change")),
            combined.group("percent"),
        )

    percent_index, percent_line = find_after(
        lines,
        change_index + 1,
        lambda line: bool(PERCENT_RE.match(line)),
        limit=5,
    )
    percent_match = PERCENT_RE.match(percent_line)
    if percent_match is None:
        raise ValueError(f"Cannot parse percent field near line {percent_index}: {percent_line}")
    return percent_index, normalize_number(change_line), percent_match.group("percent")


def parse_time(lines: list[str], start: int) -> tuple[int, str]:
    time_index, time_line = find_after(
        lines,
        start,
        lambda line: "实时数据" in line or "延迟数据" in line or "闭盘" in line,
        limit=20,
    )
    if "·" in time_line:
        return time_index, time_line.split("·", 1)[-1].strip()

    cursor = time_index + 1
    if cursor < len(lines) and lines[cursor] == "·":
        cursor += 1
    if cursor < len(lines):
        return cursor, lines[cursor].strip()
    return time_index, time_line.strip()


def parse_quote(lines: list[str], instrument: dict[str, str], source_url: str) -> BondQuote:
    title = instrument["title"]
    try:
        title_index = lines.index(title)
    except ValueError:
        title_index = 0

    try:
        anchor, _ = find_after(
            lines,
            title_index + 1,
            lambda line: line == "添加至投资组合",
            limit=60,
        )
    except ValueError:
        try:
            anchor = lines.index(title)
        except ValueError as exc:
            raise ValueError(f"Cannot find quote block for {instrument['name']}") from exc

    value_index, yield_rate = find_after(
        lines,
        anchor + 1,
        lambda line: bool(NUMBER_RE.match(line)),
        limit=20,
    )
    change_index, change, change_percent = parse_change(lines, value_index + 1)
    _time_index, time_value = parse_time(lines, change_index + 1)
    return BondQuote(
        name=instrument["name"],
        yield_rate=normalize_number(yield_rate),
        change=change,
        change_percent=change_percent,
        time=time_value,
        source_url=source_url,
        fetched_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def check_market_page(dynamic: bool) -> None:
    try:
        market_page = fetch_page(MARKET_URL, dynamic=dynamic)
        market_text = str(market_page.get_all_text(separator="\n", strip=True))
        if "美国10年期国债" not in market_text and not dynamic:
            fetch_page(MARKET_URL, dynamic=True)
    except Exception as exc:
        if dynamic:
            raise
        print(f"Warning: market page check failed, continuing with detail pages: {exc}", file=sys.stderr)


def scrape(dynamic: bool = False) -> list[BondQuote]:
    check_market_page(dynamic)

    quotes = []
    for instrument in INSTRUMENTS:
        try:
            detail_page = fetch_page(instrument["url"], dynamic=dynamic)
            quotes.append(parse_quote(page_lines(detail_page), instrument, instrument["url"]))
        except Exception:
            if dynamic:
                raise
            detail_page = fetch_page(instrument["url"], dynamic=True)
            quotes.append(parse_quote(page_lines(detail_page), instrument, instrument["url"]))
    return quotes


def write_csv(path: Path, rows: Iterable[BondQuote]) -> None:
    fieldnames = ["name", "yield_rate", "change", "change_percent", "time", "source_url", "fetched_at"]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def print_table(rows: list[BondQuote]) -> None:
    headers = ["名称", "收益率", "涨跌额", "涨跌幅", "时间"]
    table_rows = [[row.name, row.yield_rate, row.change, row.change_percent, row.time] for row in rows]
    widths = [
        max(len(str(value)) for value in column)
        for column in zip(headers, *table_rows, strict=False)
    ]
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths, strict=True)))
    print("  ".join("-" * width for width in widths))
    for row in table_rows:
        print("  ".join(str(value).ljust(width) for value, width in zip(row, widths, strict=True)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape 10Y and 2Y government bond yields from Investing.com with Scrapling."
    )
    parser.add_argument("--dynamic", action="store_true", help="Use Scrapling DynamicFetcher from the start.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    parser.add_argument("--output", type=Path, help="Optional CSV output path.")
    args = parser.parse_args()

    rows = scrape(dynamic=args.dynamic)
    if args.output:
        write_csv(args.output, rows)

    if args.json:
        print(json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2))
    else:
        print_table(rows)
        if args.output:
            print(f"\nSaved CSV: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
