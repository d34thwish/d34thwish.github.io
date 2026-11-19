# Source inspection notes

The uploaded file named `news.py` is not a Python scraper. It is a GitHub Actions YAML workflow that references an absent `scripts/hermes_news.py`, Groq, `GROQ_API_KEY`, `config/news_feeds.json`, digest files and generated output. Therefore the original scraper implementation, exact AI prompt/schema, and source extraction rules were not present to preserve.

The uploaded `news.html` is a static generated briefing containing RPP, BBC and Al Jazeera stories, client-side search, region/topic filters, sorting and theme persistence. Those useful interactions were retained in the redesigned data-driven frontend.
