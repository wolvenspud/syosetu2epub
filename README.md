# syosetu2epub

Scrape novels from [ncode.syosetu.com](https://ncode.syosetu.com) and convert them to Kindle-ready EPUB files, with optional direct email delivery to your Kindle.

## Requirements

- [uv](https://docs.astral.sh/uv/) — handles Python dependencies automatically on first run

## Usage

```bash
# First-time setup (saves SMTP credentials for future runs)
uv run syosetu2epub.py --setup

# Scrape a novel and save as EPUB
uv run syosetu2epub.py n4395il
uv run syosetu2epub.py https://ncode.syosetu.com/n4395il/

# Scrape and send directly to Kindle
uv run syosetu2epub.py n4395il --send-to your@kindle.com

# Fetch only a range of chapters
uv run syosetu2epub.py n4395il --chapters 1-20

# Resend an already-built EPUB without re-scraping
uv run syosetu2epub.py --resend "novel.epub"

# Force re-download (ignore local cache)
uv run syosetu2epub.py n4395il --refresh
```

## Features

- **EPUB output** — reflowable format, adapts to Kindle screen size
- **Arc-grouped table of contents** — chapter arcs (章) appear as sections in the Kindle TOC
- **Local cache** — scraped chapters are saved to `~/.cache/syosetu2epub/` so subsequent runs don't re-hit the server
- **Config file** — credentials stored once at `~/.config/syosetu2epub/config.json` via `--setup`; never hardcoded

## Kindle email setup

Amazon only accepts attachments from approved addresses. Before sending:

1. Go to **amazon.com → Account → Manage Your Content and Devices → Preferences → Personal Document Settings**
2. Add your Gmail address to the **Approved Personal Document Email List**
3. Note your `@kindle.com` address on the same page

For Gmail, use an [App Password](https://myaccount.google.com/apppasswords) rather than your account password.
