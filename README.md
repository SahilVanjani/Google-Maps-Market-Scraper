# Google Maps Market Scraper

Selenium-based Python scraper for Google Maps/Places business search results with batching, checkpoints, and resume support.

Example:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scrape_maps.py "exporters in noida" --max-results 0 --batch-size 25 --output output\exporters_noida.csv
```

Even when `--output` is provided, the scraper appends a date/time suffix so each run creates a new file, for example `output\exporters_noida_20260428_174512.csv`. If that name already exists, it adds `_2`, `_3`, and so on instead of overwriting.

The scraper collects:

- business name
- category
- address
- phone
- website
- public email addresses found on the business website
- scrape timestamp

## Notes

Google Maps changes its page markup often and may show consent screens, rate limits, or CAPTCHA challenges. This tool uses normal Selenium browser automation and does not bypass access controls. If Google shows a verification page, the scraper stops and saves its checkpoint.

For full market coverage, split broad markets into smaller query batches. For example:

```text
exporters in noida
exporters in greater noida
exporters in ghaziabad
garment exporters in noida
handicraft exporters in noida
```

Then run:

```powershell
python scrape_maps.py --query-file queries.txt --max-results 0 --batch-size 25 --output output\market_exporters.csv
```

The checkpoint is written to `output\market_exporters.csv.state.json`. Restart the same command to resume without duplicating already processed leads.

When timestamped output is enabled, the checkpoint follows the generated filename, for example `output\market_exporters_20260428_174512.csv.state.json`.

## CLI

```powershell
python scrape_maps.py "exporters in noida"
python scrape_maps.py "exporters in noida" --max-results 0 --no-headless
python scrape_maps.py "exporters in noida" --skip-emails --fresh --output output\results.csv
python scrape_maps.py --query-file queries.txt --format jsonl --output output\results.jsonl
```

Options:

- `--max-results 0` means scroll until Google Maps shows no more visible results.
- `--batch-size 25` writes output and checkpoint every 25 new leads.
- `--resume` is enabled by default; use `--fresh` to ignore previous state.
- `--timestamp-output` is enabled by default, so every run creates a new dated file.
- `--exact-output` uses the exact `--output` path, useful only when resuming a specific older file.
- `--no-headless` opens a visible browser, useful for consent screens or debugging.
- `--skip-emails` skips website crawling and is much faster.
- `--browser chrome` or `--browser edge`.
- `--polite-delay-min` and `--polite-delay-max` control respectful delays between actions.
- By default each run uses a fresh browser profile to avoid Chrome `DevToolsActivePort` startup crashes after interrupted runs. Use `--reuse-browser-profile` only when you intentionally want the same browser profile.

## Output Fields

CSV/JSONL columns:

- `search_query`
- `name`
- `category`
- `address`
- `phone`
- `website`
- `emails`
- `scraped_at`
