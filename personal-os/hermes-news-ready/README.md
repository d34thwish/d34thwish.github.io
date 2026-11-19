# Hermes News

Automated daily briefing for `d34thwish/personal-os`. The pipeline is deterministic collection, Groq-assisted review, strict validation, atomic JSON write, then GitHub Pages deployment.

## Required secret

`GROQ_API_KEY`: create a key in the GroqCloud console. Store it at **Settings → Secrets and variables → Actions → New repository secret**. Never commit it.

## Schedule

`.github/workflows/daily-news.yml` runs at `0 11 * * *`: 11:00 UTC / 06:00 Peru (America/Lima). Edit the cron line to change it.

## Repository layout

- `news.html`: public frontend.
- `data/news.json`: last validated dataset.
- `scripts/hermes_news.py`: collector, AI review, validation and atomic persistence.
- `scripts/build_site.py`: creates a clean `_site` artifact while preserving root HTML and common asset folders.
- `config/news_feeds.json`: sources and model.
- `.github/workflows/daily-news.yml`: test, update, commit and deploy.

## First deployment

In GitHub, open **Settings → Pages → Build and deployment → Source** and select **GitHub Actions**. Then open **Actions → Hermes News daily → Run workflow → Run workflow**.

## Local maintenance

Python 3.11 is used. Install with `python -m pip install -r requirements.txt`, test with `python -m unittest discover -s tests -v`, and run with `GROQ_API_KEY=... python scripts/hermes_news.py`.

## Failure behavior

A total scrape failure, missing secret, invalid/truncated AI response, schema violation, or test failure exits non-zero before changing `data/news.json` or deploying. Individual feed failures are recorded while successful sources continue. No new articles produces no commit and no deployment. Existing records remain until the configured 30-day retention threshold, and pruning occurs only after a successful reviewed batch.

## Troubleshooting

- `GROQ_API_KEY is not configured`: add the Actions repository secret with that exact name.
- Pages deployment rejected: select **GitHub Actions** as the Pages source and confirm workflow permissions are not restricted under **Settings → Actions → General → Workflow permissions**.
- Feed returns no items: inspect the failed source in the Actions log and update `config/news_feeds.json`; the other sources and previous dataset remain protected.
- AI rate limit/timeout: the script retries four times with bounded exponential backoff, then fails without publishing.
- Site is unchanged: inspect the `Detect meaningful changes` step. A successful no-change run intentionally skips commit and deployment.
