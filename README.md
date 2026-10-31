# syosetu2epub

Scrape novels from [ncode.syosetu.com](https://ncode.syosetu.com) and convert them to Kindle-ready EPUB files, with optional direct email delivery to your Kindle.

## Requirements

- [uv](https://docs.astral.sh/uv/) — handles Python dependencies automatically on first run

## Usage

```bash
# First-time setup (saves SMTP credentials for future runs)
uv run syosetu2epub.py --setup

# Scrape and send to Kindle (default behaviour)
uv run syosetu2epub.py n4395il
uv run syosetu2epub.py https://ncode.syosetu.com/n4395il/

# Save EPUB locally only (skip sending)
uv run syosetu2epub.py n4395il --local

# Override the configured Kindle address for this run
uv run syosetu2epub.py n4395il --send-to other@kindle.com

# Send to multiple Kindle addresses (repeat the flag or comma-separate)
# Usually unnecessary: see "One address is enough" below
uv run syosetu2epub.py n4395il --send-to phone@kindle.com --send-to tablet@kindle.com

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
- **Illustrations** — 挿絵 (usually hosted on mitemin.net) are downloaded and embedded in the EPUB; an image that can't be fetched becomes a `［挿絵］` placeholder instead of failing the build
- **Local cache** — scraped chapters and images are saved to `~/.cache/syosetu2epub/` so subsequent runs don't re-hit the server. Chapters cached before image support are refetched automatically
- **Config file** — credentials stored once at `~/.config/syosetu2epub/config.json` via `--setup`; never hardcoded

## Kindle email setup

Amazon only accepts attachments from approved addresses. Before sending:

1. Go to **amazon.com → Account → Manage Your Content and Devices → Preferences → Personal Document Settings**
2. Add your Gmail address to the **Approved Personal Document Email List**
3. Note your `@kindle.com` address on the same page

For Gmail, use an [App Password](https://myaccount.google.com/apppasswords) rather than your account password.

### One address is enough

Send to Kindle saves documents to your Kindle library, which syncs to every
device and app on the account. Sending to one `@kindle.com` address already
reaches all of them, and sending to several produces duplicate library entries.
