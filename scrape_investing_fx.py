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


FX_PAIRS = [
    {
        "pair": "美元/人民币",
        "symbol": "USD/CNY",
        "url": "https://cn.investing.com/currencies/usd-cny",
    },
    {
        "pair": "人民币/日元",
        "symbol": "CNY/JPY",
        "url": "https://cn.investing.com/currencies/cny-jpy",
    },
    {
        "pair": "美元/日元",
        "symbol": "USD/JPY",
        "url": "https://cn.investing.com/currencies/usd-jpy",
    },
    {
        "pair": "日元/俄罗斯卢布",
        "symbol": "JPY/RUB",
        "url": "https://cn.investing.com/currencies/jpy-rub",
    },
    {
        "pair": "人民币/俄罗斯卢布",
        "symbol": "CNY/RUB",
        "url": "https://cn.investing.com/currencies/cny-rub",
    },
    {
        "pair": "美元/俄罗斯卢布",
        "symbol": "USD/RUB",
        "url": "https://cn.investing.com/currencies/usd-rub",
    },
    {
        "pair": "日元/欧元",
        "symbol": "JPY/EUR",
        "url": "https://cn.investing.com/currencies/jpy-eur",
    },
    {
        "pair": "俄罗斯卢布/欧元",
        "symbol": "RUB/EUR",
        "url": "https://cn.investing.com/currencies/rub-eur",
    },
    {
        "pair": "人民币/欧元",
        "symbol": "CNY/EUR",
        "url": "https://cn.investing.com/currencies/cny-eur",
    },
    {
        "pair": "美元/欧元",
        "symbol": "USD/EUR",
        "url": "https://cn.investing.com/currencies/usd-eur",
    },
]

NUMBER_RE = re.compile(r"^[+-]?\d+(?:,\d{3})*(?:\.\d+)?$")
PERCENT_RE = re.compile(r"^\(?(?P<percent>[+-]?\d+(?:\.\d+)?%)\)?$")
CHANGE_RE = re.compile(
    r"(?P<change>[+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"\((?P<percent>[+-]?\d+(?:\.\d+)?%)\)"
)


@dataclass
class FxQuote:
    pair: str
    symbol: str
    sell_price: str
    buy_price: str
    change_percent: str
    time: str
    note: str
    source_url: str
    fetched_at: str


def fetch_page(url: str):
    headers = {
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
    }
    return Fetcher.get(url, headers=headers, timeout=30, impersonate="chrome")


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


def parse_change_percent(lines: list[str], start: int) -> tuple[int, str]:
    change_index, change_line = find_after(
        lines,
        start,
        lambda line: bool(CHANGE_RE.search(line) or NUMBER_RE.match(line)),
        limit=20,
    )
    combined = CHANGE_RE.search(change_line)
    if combined is not None:
        return change_index, combined.group("percent")

    percent_index, percent_line = find_after(
        lines,
        change_index + 1,
        lambda line: bool(PERCENT_RE.match(line)),
        limit=5,
    )
    percent_match = PERCENT_RE.match(percent_line)
    if percent_match is None:
        raise ValueError(f"Cannot parse percent field near line {percent_index}: {percent_line}")
    return percent_index, percent_match.group("percent")


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


def parse_label_value(lines: list[str], label: str, start: int) -> str:
    label_index, _ = find_after(lines, start, lambda line: line == label, limit=180)
    if label_index + 1 >= len(lines):
        raise ValueError(f"Missing value after {label}")
    value = lines[label_index + 1]
    if not NUMBER_RE.match(value):
        raise ValueError(f"Cannot parse {label}: {value}")
    return normalize_number(value)


def parse_quote(lines: list[str], pair_config: dict[str, str]) -> FxQuote:
    try:
        anchor = lines.index("添加至投资组合")
    except ValueError as exc:
        raise ValueError(f"Cannot find quote block for {pair_config['symbol']}") from exc

    _last_index, _last_price = find_after(
        lines,
        anchor + 1,
        lambda line: bool(NUMBER_RE.match(line)),
        limit=20,
    )
    percent_index, change_percent = parse_change_percent(lines, _last_index + 1)
    _time_index, time_value = parse_time(lines, percent_index + 1)
    sell_price = parse_label_value(lines, "卖价", anchor)
    buy_price = parse_label_value(lines, "买价", anchor)
    note = "价格已乘以100" if any("价格已乘以100" in line for line in lines) else ""

    return FxQuote(
        pair=pair_config["pair"],
        symbol=pair_config["symbol"],
        sell_price=sell_price,
        buy_price=buy_price,
        change_percent=change_percent,
        time=time_value,
        note=note,
        source_url=pair_config["url"],
        fetched_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def scrape() -> list[FxQuote]:
    quotes = []
    for pair_config in FX_PAIRS:
        page = fetch_page(pair_config["url"])
        quotes.append(parse_quote(page_lines(page), pair_config))
    return quotes


def write_csv(path: Path, rows: Iterable[FxQuote]) -> None:
    fieldnames = [
        "pair",
        "symbol",
        "sell_price",
        "buy_price",
        "change_percent",
        "time",
        "note",
        "source_url",
        "fetched_at",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def print_table(rows: list[FxQuote]) -> None:
    headers = ["货币对", "符号", "卖价", "买价", "涨跌幅", "时间", "备注"]
    table_rows = [
        [row.pair, row.symbol, row.sell_price, row.buy_price, row.change_percent, row.time, row.note]
        for row in rows
    ]
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
        description="Scrape selected forex bid/ask quotes from Investing.com with Scrapling."
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    parser.add_argument("--output", type=Path, help="Optional CSV output path.")
    args = parser.parse_args()

    rows = scrape()
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
