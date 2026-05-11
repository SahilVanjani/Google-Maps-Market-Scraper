from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .output import RecordBatchWriter
from .state import ScrapeState


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    parser = build_parser()
    args = parser.parse_args(argv)
    queries = load_queries(args)
    if not queries:
        parser.error("Provide a query or --query-file.")
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1.")

    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_format = args.format or infer_format(args.output)
    output_path = resolve_output_path(
        requested_output=args.output,
        query_name=queries[0] if len(queries) == 1 else "market_scrape",
        output_format=output_format,
        timestamp=run_timestamp,
        timestamp_output=args.timestamp_output,
    )
    state_path = Path(args.state_file) if args.state_file else default_state_path(output_path)

    try:
        from selenium.common.exceptions import TimeoutException

        from .google_maps import GoogleMapsBlockedError, GoogleMapsScraper, ScraperConfig
    except ModuleNotFoundError as exc:
        missing = exc.name or "a required package"
        print(f"Missing dependency: {missing}. Run: pip install -r requirements.txt")
        return 1

    config = ScraperConfig(
        browser=args.browser,
        headless=args.headless,
        max_results=None if args.max_results == 0 else args.max_results,
        scroll_pause=args.scroll_pause,
        details_pause=args.details_pause,
        polite_delay_min=args.polite_delay_min,
        polite_delay_max=args.polite_delay_max,
        with_emails=not args.skip_emails,
        email_timeout=args.email_timeout,
        email_pages=args.email_pages,
        max_stagnant_scrolls=args.max_stagnant_scrolls,
        profile_dir=args.profile_dir,
        reuse_browser_profile=args.reuse_browser_profile,
    )

    output_exists = Path(output_path).exists()
    state_exists = state_path.exists()
    if args.resume and state_exists and not output_exists:
        print(
            f"Checkpoint exists at {state_path}, but output file {output_path} is missing. "
            "Starting fresh to avoid silently skipping records."
        )
        state = ScrapeState()
        append_output = False
    else:
        state = ScrapeState.load(state_path) if args.resume else ScrapeState()
        append_output = args.resume

    writer = RecordBatchWriter(output_path, output_format, append=append_output)
    buffer = []
    written = 0
    duplicates = 0

    print(f"Output file: {output_path}")
    print(f"Checkpoint file: {state_path}")

    try:
        with GoogleMapsScraper(config) as scraper:
            for query in queries:
                if args.resume and query in state.completed_queries:
                    print(f"Skipping completed query: {query}")
                    continue

                print(f"Starting query: {query}")
                try:
                    for item in scraper.iter_query(query, skip_urls=state.processed_urls):
                        record = item.record
                        key = record.lead_key() or item.place.url
                        state.processed_urls.add(item.place.url)

                        if key in state.lead_keys:
                            duplicates += 1
                            state.save(state_path)
                            continue

                        state.lead_keys.add(key)
                        buffer.append(record)

                        if len(buffer) >= args.batch_size:
                            written += writer.write_batch(buffer)
                            buffer.clear()
                            state.save(state_path)
                            print(f"Checkpoint saved: {written} records written")
                except TimeoutException as exc:
                    written += writer.write_batch(buffer)
                    buffer.clear()
                    state.save(state_path)
                    print(f"Timed out while scraping query: {query}")
                    print(f"Continuing with next query. Details: {exc}")
                    continue

                state.completed_queries.add(query)
                state.save(state_path)

    except GoogleMapsBlockedError as exc:
        written += writer.write_batch(buffer)
        buffer.clear()
        state.save(state_path)
        print(str(exc))
        print(f"Paused with checkpoint saved at {state_path}")
        print(f"Records written before pause: {written}; duplicates skipped: {duplicates}")
        return 2

    written += writer.write_batch(buffer)
    state.save(state_path)
    print(f"Saved {written} records to {output_path}")
    print(f"Checkpoint: {state_path}")
    if duplicates:
        print(f"Skipped {duplicates} duplicate leads")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch scrape Google Maps business search results.")
    parser.add_argument("query", nargs="?", help='Google Maps search query, for example "exporters in noida".')
    parser.add_argument("--query-file", help="Text file with one search query per line.")
    parser.add_argument("--output", "-o", help="Output file path. Defaults to output/<query>_<timestamp>.csv.")
    parser.add_argument("--format", choices=["csv", "json", "jsonl"], help="Output format. Inferred from --output if omitted.")
    parser.add_argument("--state-file", help="Checkpoint file path. Defaults to <output>.state.json.")
    parser.add_argument(
        "--timestamp-output",
        dest="timestamp_output",
        action="store_true",
        default=True,
        help="Append date/time to --output so each run creates a new file.",
    )
    parser.add_argument(
        "--exact-output",
        dest="timestamp_output",
        action="store_false",
        help="Use --output exactly as provided, useful when resuming a specific prior file.",
    )
    parser.add_argument("--batch-size", type=int, default=25, help="Write and checkpoint after this many new leads.")
    parser.add_argument("--browser", choices=["chrome", "edge"], default="chrome", help="Browser to automate.")
    parser.add_argument("--max-results", type=int, default=0, help="Maximum businesses per query. Use 0 for all visible results.")
    parser.add_argument("--scroll-pause", type=float, default=1.5, help="Seconds to wait after each result scroll.")
    parser.add_argument("--details-pause", type=float, default=1.0, help="Seconds to wait after opening a place.")
    parser.add_argument("--polite-delay-min", type=float, default=0.8, help="Minimum extra delay between browser actions.")
    parser.add_argument("--polite-delay-max", type=float, default=2.2, help="Maximum extra delay between browser actions.")
    parser.add_argument("--max-stagnant-scrolls", type=int, default=8, help="Stop after this many scrolls with no new results.")
    parser.add_argument("--profile-dir", help="Optional explicit Chrome/Edge profile directory.")
    parser.add_argument("--reuse-browser-profile", action="store_true", help="Reuse .browser-profile/<browser> instead of a fresh per-run profile.")
    parser.add_argument("--email-timeout", type=float, default=10.0, help="Seconds to wait per website page.")
    parser.add_argument("--email-pages", type=int, default=5, help="Maximum pages to crawl per business website.")
    parser.add_argument("--skip-emails", action="store_true", help="Skip crawling websites for emails.")
    parser.add_argument("--resume", dest="resume", action="store_true", default=True, help="Resume from checkpoint if present.")
    parser.add_argument("--fresh", dest="resume", action="store_false", help="Ignore checkpoint and start a fresh output file.")
    parser.add_argument("--headless", dest="headless", action="store_true", default=True, help="Run browser headless.")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="Show the browser window.")
    return parser


def infer_format(output_path: str | None) -> str:
    if output_path and Path(output_path).suffix.lower() == ".jsonl":
        return "jsonl"
    if output_path and Path(output_path).suffix.lower() == ".json":
        return "json"
    return "csv"


def default_output_path(query: str, output_format: str) -> str:
    safe_query = "".join(char.lower() if char.isalnum() else "_" for char in query).strip("_")
    safe_query = "_".join(part for part in safe_query.split("_") if part)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(unique_path(Path("output") / f"{safe_query}_{timestamp}.{output_format}"))


def resolve_output_path(
    requested_output: str | None,
    query_name: str,
    output_format: str,
    timestamp: str,
    timestamp_output: bool,
) -> str:
    if not requested_output:
        return default_output_path(query_name, output_format)

    path = Path(requested_output)
    if not timestamp_output:
        return str(path)

    suffix = path.suffix or f".{output_format}"
    stem = path.stem if path.suffix else path.name
    return str(unique_path(path.parent / f"{stem}_{timestamp}{suffix}"))


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not create a unique output path for {path}")


def default_state_path(output_path: str | Path) -> Path:
    path = Path(output_path)
    return path.with_suffix(path.suffix + ".state.json")


def load_queries(args: argparse.Namespace) -> list[str]:
    queries = []
    if args.query:
        queries.append(args.query.strip())

    if args.query_file:
        path = Path(args.query_file)
        lines = path.read_text(encoding="utf-8").splitlines()
        queries.extend(line.strip() for line in lines if line.strip() and not line.strip().startswith("#"))

    deduped = []
    seen = set()
    for query in queries:
        normalized = " ".join(query.split()).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(query)
    return deduped
