#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "requests",
#   "beautifulsoup4",
#   "ebooklib",
#   "lxml",
# ]
# ///
"""
syosetu2epub — scrape a novel from ncode.syosetu.com and export as Kindle-ready EPUB.

Usage:
  uv run syosetu2epub.py --setup                   # first-time credential setup
  uv run syosetu2epub.py n4395il                   # scrape and save locally
  uv run syosetu2epub.py https://ncode.syosetu.com/n4395il/
  uv run syosetu2epub.py n4395il --chapters 1-20
  uv run syosetu2epub.py n4395il --send-to your@kindle.com
  uv run syosetu2epub.py n4395il --send-to your@kindle.com \\
      --smtp-user you@gmail.com --smtp-pass 'apppassword'  # override config
"""

import argparse
import getpass
import json
import re
import smtplib
import sys
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

CONFIG_PATH = Path("~/.config/syosetu2epub/config.json").expanduser()
CACHE_DIR   = Path("~/.cache/syosetu2epub").expanduser()

import requests
from bs4 import BeautifulSoup, Tag
from ebooklib import epub

BASE_URL = "https://ncode.syosetu.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; syosetu2epub/1.0; personal use)"}
DELAY = 1.0  # seconds between requests — be polite to the server


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def run_setup() -> None:
    """Interactive first-time (or re-) configuration wizard."""
    existing = load_config()
    print("=== syosetu2epub setup ===")
    print(f"Config will be saved to: {CONFIG_PATH}\n")

    def prompt(label: str, key: str, default: str = "", secret: bool = False) -> str:
        cur = existing.get(key, default)
        hint = f" [{cur}]" if cur and not secret else (" [current hidden]" if cur and secret else "")
        if secret:
            val = getpass.getpass(f"{label}{hint}: ").strip()
        else:
            val = input(f"{label}{hint}: ").strip()
        return val or cur

    cfg = {
        "smtp_user":    prompt("Gmail address (sender)", "smtp_user"),
        "smtp_pass":    prompt("App password",           "smtp_pass", secret=True),
        "smtp_host":    prompt("SMTP host",              "smtp_host", default="smtp.gmail.com"),
        "smtp_port":    int(prompt("SMTP port",          "smtp_port", default="587") or 587),
        "kindle_email": prompt("Default Kindle email (optional, press Enter to skip)",
                               "kindle_email"),
    }
    # Drop empty optional fields
    if not cfg["kindle_email"]:
        cfg.pop("kindle_email")

    save_config(cfg)
    print(f"\nSaved to {CONFIG_PATH}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_code(raw: str) -> str:
    """Return the bare novel code from a URL or bare code string."""
    raw = raw.strip().rstrip("/")
    m = re.search(r"ncode\.syosetu\.com/([^/]+)", raw)
    return m.group(1).lower() if m else raw.lower()


def get(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    time.sleep(DELAY)
    return BeautifulSoup(resp.text, "lxml")


def tag_inner_html(tag: Tag) -> str:
    """Return sanitised inner HTML, keeping only <p> and <br/> tags."""
    # Remove id/class attrs from all children
    for child in tag.find_all(True):
        child.attrs = {}
    # Render to string and strip the outer tag
    html = tag.decode_contents()
    return html


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _novel_cache(code: str) -> Path:
    d = CACHE_DIR / code
    d.mkdir(parents=True, exist_ok=True)
    return d


def _toc_cache_path(code: str) -> Path:
    return _novel_cache(code) / "toc.json"


def _chapter_cache_path(code: str, ch_num: int) -> Path:
    return _novel_cache(code) / f"chapter_{ch_num:04d}.html"


def cache_info(code: str) -> None:
    """Print a summary of what's cached for a novel code."""
    d = CACHE_DIR / code
    if not d.exists():
        print(f"No cache for {code}")
        return
    toc_file = d / "toc.json"
    chapters = sorted(d.glob("chapter_*.html"))
    if toc_file.exists():
        toc = json.loads(toc_file.read_text())
        print(f"Cached: {toc['title']}  ({len(chapters)} chapters)")
    else:
        print(f"Cached: {len(chapters)} chapters (no TOC yet)")
    print(f"  Location: {d}")


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def fetch_toc(code: str, refresh: bool = False) -> dict:
    """
    Returns:
      title, author, description,
      arcs: [{name, chapters: [{title, url}]}],
      single: bool  (短編 — no chapter list)
    """
    cache_path = _toc_cache_path(code)
    if not refresh and cache_path.exists():
        print(f"TOC: using cache ({cache_path})")
        return json.loads(cache_path.read_text())

    url = f"{BASE_URL}/{code}/"
    print(f"Fetching TOC: {url}")
    soup = get(url)

    title_tag = soup.select_one("h1.p-novel__title")
    title = title_tag.get_text(strip=True) if title_tag else code

    author_tag = soup.select_one(".p-novel__author a") or soup.select_one(".p-novel__author")
    author = author_tag.get_text(strip=True) if author_tag else "Unknown"

    desc_tag = soup.select_one("#novel_ex")
    description = desc_tag.get_text("\n", strip=True) if desc_tag else ""

    eplist = soup.select_one("div.p-eplist")
    if eplist is None:
        # 短編 — single chapter, content lives on this page
        return {
            "title": title, "author": author, "description": description,
            "arcs": [{"name": None, "chapters": [{"title": title, "url": url}]}],
            "single": True,
        }

    # Walk the eplist children to build arc-grouped chapter list
    arcs: list[dict] = []
    current_arc: dict | None = None

    for div in eplist.find_all("div", recursive=False):
        cls = " ".join(div.get("class", []))
        if "p-eplist__chapter-title" in cls:
            current_arc = {"name": div.get_text(strip=True), "chapters": []}
            arcs.append(current_arc)
        elif "p-eplist__sublist" in cls:
            a = div.select_one("a")
            if a:
                ch = {"title": a.get_text(strip=True), "url": BASE_URL + a["href"]}
                if current_arc is None:
                    current_arc = {"name": None, "chapters": []}
                    arcs.append(current_arc)
                current_arc["chapters"].append(ch)

    toc = {
        "title": title, "author": author, "description": description,
        "arcs": arcs, "single": False,
    }
    cache_path.write_text(json.dumps(toc, ensure_ascii=False, indent=2))
    return toc


def fetch_chapter_body(code: str, ch_num: int, url: str,
                       single: bool = False, refresh: bool = False) -> str:
    """Return sanitised HTML content of one chapter, using cache when available."""
    cache_path = _chapter_cache_path(code, ch_num)
    if not refresh and cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    soup = get(url)

    body_div = soup.select_one("div.p-novel__body")
    if body_div is None:
        # Fallback for older page layout
        body_div = soup.select_one("div#novel_honbun")

    if body_div is None:
        return "<p>(本文が見つかりませんでした)</p>"

    # Collect the text sections (preface, main body, afterword)
    sections = body_div.select("div.p-novel__text") or [body_div]

    parts = []
    for sec in sections:
        # Drop ruby annotations cleanly: keep base text, drop furigana
        for ruby in sec.find_all("ruby"):
            base = "".join(t.get_text() for t in ruby.find_all("rb")) or ruby.get_text()
            ruby.replace_with(base)
        # Strip all attrs from every tag
        for tag in sec.find_all(True):
            tag.attrs = {}
        # Keep only <p> and <br> in inner HTML
        html = sec.decode_contents()
        parts.append(html)

    result = "\n".join(parts)
    cache_path.write_text(result, encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# EPUB assembly
# ---------------------------------------------------------------------------

EPUB_CSS = """\
body {
  font-family: serif;
  line-height: 1.8;
  margin: 1em 2em;
}
h1 { font-size: 1.5em; text-align: center; margin: 1em 0 0.3em; }
h2 { font-size: 1.2em; text-align: center; margin: 2em 0 1em; }
h3 { font-size: 1.05em; text-align: center; color: #555; margin: 1.5em 0 0.5em; }
p  { margin: 0.35em 0; text-indent: 1em; }
.description { font-style: italic; margin: 1.5em 0 2em; }
.arc-title { font-size: 1.1em; font-weight: bold; text-align: center;
             border-bottom: 1px solid #ccc; padding-bottom: 0.4em; margin: 2em 0 1em; }
"""


def _xhtml(title: str, body: str) -> str:
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<!DOCTYPE html>"
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">'
        f"<head><meta charset='utf-8'/><title>{title}</title>"
        "<link rel='stylesheet' href='style.css'/></head>"
        f"<body>{body}</body></html>"
    )


def build_epub(toc: dict, arc_bodies: list[list[str]], out_path: Path) -> None:
    """
    toc:       result of fetch_toc (possibly chapter-filtered)
    arc_bodies: list of lists — arc_bodies[i][j] is the HTML body of
                toc['arcs'][i]['chapters'][j]
    """
    book = epub.EpubBook()
    safe_title = toc["title"]
    book.set_identifier(f"syosetu-{toc['title']}")
    book.set_title(safe_title)
    book.set_language("ja")
    book.add_author(toc["author"])

    css_item = epub.EpubItem(
        uid="style", file_name="style.css",
        media_type="text/css", content=EPUB_CSS.encode(),
    )
    book.add_item(css_item)

    # Info / cover page
    desc_html = toc["description"].replace("\n", "<br/>")
    info_content = _xhtml(
        safe_title,
        f"<h1>{safe_title}</h1>"
        f"<p><strong>{toc['author']}</strong></p>"
        f"<p class='description'>{desc_html}</p>",
    )
    info_page = epub.EpubHtml(title="About", file_name="info.xhtml", lang="ja")
    info_page.content = info_content.encode("utf-8")
    info_page.add_item(css_item)
    book.add_item(info_page)

    spine = ["nav", info_page]
    toc_entries: list = []
    ch_idx = 1

    for arc, bodies in zip(toc["arcs"], arc_bodies):
        arc_epub_chapters: list[epub.EpubHtml] = []

        for ch_meta, body in zip(arc["chapters"], bodies):
            fname = f"chapter_{ch_idx:04d}.xhtml"
            ch_title = ch_meta["title"]
            content = _xhtml(
                ch_title,
                f"<h2>{ch_title}</h2>\n{body}",
            )
            ch_item = epub.EpubHtml(title=ch_title, file_name=fname, lang="ja")
            ch_item.content = content.encode("utf-8")
            ch_item.add_item(css_item)
            book.add_item(ch_item)
            spine.append(ch_item)
            arc_epub_chapters.append(ch_item)
            ch_idx += 1

        if arc["name"]:
            # Nested TOC: (Section, [children]) tuple
            toc_entries.append((epub.Section(arc["name"]), arc_epub_chapters))
        else:
            toc_entries.extend(arc_epub_chapters)

    book.toc = toc_entries
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    epub.write_epub(str(out_path), book)
    size_kb = out_path.stat().st_size // 1024
    print(f"\nSaved: {out_path}  ({size_kb} KB, {ch_idx - 1} chapters)")


# ---------------------------------------------------------------------------
# Email to Kindle
# ---------------------------------------------------------------------------

def send_to_kindle(
    epub_path: Path,
    kindle_addr: str,
    smtp_user: str,
    smtp_pass: str,
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
) -> None:
    print(f"\nSending {epub_path.name} to {kindle_addr} via {smtp_host}:{smtp_port} …")

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = kindle_addr
    # Subject "convert" tells Amazon to convert the file; omit it for EPUB which
    # Kindle reads natively — use blank or the book title.
    msg["Subject"] = epub_path.stem

    msg.attach(MIMEText("Sent by syosetu2epub.", "plain", "utf-8"))

    with open(epub_path, "rb") as fh:
        part = MIMEBase("application", "epub+zip")
        part.set_payload(fh.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=epub_path.name)
        msg.attach(part)

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, kindle_addr, msg.as_string())

    print("Email sent successfully.")
    print("Note: Add your Gmail address to your Kindle's Approved Personal Document Email List")
    print("      at amazon.com → Account → Manage Your Content and Devices → Preferences.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _check_smtp(smtp_user: str | None, smtp_pass: str | None) -> None:
    missing = [k for k, v in [("smtp_user", smtp_user), ("smtp_pass", smtp_pass)] if not v]
    if missing:
        sys.exit(
            f"Missing credentials: {', '.join(missing)}\n"
            f"Run:  uv run syosetu2epub.py --setup"
        )


def parse_range(spec: str, total: int) -> tuple[int, int]:
    """'1-10' → (0, 10); '5' → (4, 5); all 1-based, returns 0-based slice."""
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", spec.strip())
    if not m:
        sys.exit(f"--chapters must be '5' or '1-10', got: {spec!r}")
    start = int(m.group(1)) - 1
    end = int(m.group(2)) if m.group(2) else int(m.group(1))
    if start < 0 or end > total or start >= end:
        sys.exit(f"Chapter range {spec!r} is out of bounds (novel has {total} chapters).")
    return start, end


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape a syosetu.com novel and export as Kindle-ready EPUB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "novel", nargs="?",
        help="Novel code (n4395il) or full URL",
    )
    parser.add_argument(
        "--setup", action="store_true",
        help=f"Run interactive credential setup (saved to {CONFIG_PATH})",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output .epub path (default: <title>.epub)",
    )
    parser.add_argument(
        "--chapters", "-c",
        metavar="RANGE",
        help="Subset of chapters to fetch, e.g. '1-10' or '5' (default: all)",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Ignore local cache and re-download everything from the site",
    )
    parser.add_argument(
        "--resend", metavar="EPUB_PATH",
        help="Skip scraping; email an existing .epub file directly",
    )

    kg = parser.add_argument_group("Send to Kindle")
    kg.add_argument("--send-to", metavar="KINDLE_EMAIL",
                    help="Kindle address (overrides config's kindle_email)")
    kg.add_argument("--smtp-user", metavar="EMAIL",
                    help="Sender Gmail (overrides config)")
    kg.add_argument("--smtp-pass", metavar="PASSWORD",
                    help="SMTP app password (overrides config)")
    kg.add_argument("--smtp-host", metavar="HOST",
                    help="SMTP host (overrides config, default smtp.gmail.com)")
    kg.add_argument("--smtp-port", type=int,
                    help="SMTP port (overrides config, default 587)")

    args = parser.parse_args()

    # --setup runs standalone and exits
    if args.setup:
        run_setup()
        return

    # Merge config under CLI flags (CLI always wins)
    cfg = load_config()
    smtp_user = args.smtp_user or cfg.get("smtp_user")
    smtp_pass = args.smtp_pass or cfg.get("smtp_pass")
    smtp_host = args.smtp_host or cfg.get("smtp_host", "smtp.gmail.com")
    smtp_port = args.smtp_port or cfg.get("smtp_port", 587)
    send_to   = args.send_to  or cfg.get("kindle_email")

    # --resend: skip scraping, just email an existing file
    if args.resend:
        epub_path = Path(args.resend)
        if not epub_path.exists():
            sys.exit(f"File not found: {epub_path}")
        if not send_to:
            sys.exit("--resend requires --send-to or a kindle_email in config (run --setup)")
        _check_smtp(smtp_user, smtp_pass)
        send_to_kindle(epub_path, send_to, smtp_user, smtp_pass, smtp_host, smtp_port)
        return

    if not args.novel:
        parser.print_help()
        sys.exit(0)

    code = extract_code(args.novel)
    print(f"Novel code: {code}")

    toc = fetch_toc(code, refresh=args.refresh)
    print(f"Title:    {toc['title']}")
    print(f"Author:   {toc['author']}")
    total_chapters = sum(len(a["chapters"]) for a in toc["arcs"])
    print(f"Chapters: {total_chapters}  ({len(toc['arcs'])} arc(s))")

    # Flatten chapter list; ch_num is the 1-based global index used as cache key
    all_chapters = [(arc_i, ch_num, ch)
                    for arc_i, arc in enumerate(toc["arcs"])
                    for ch_num, ch in enumerate(
                        (c for a in toc["arcs"][:arc_i] for c in a["chapters"]),  # offset
                        start=sum(len(a["chapters"]) for a in toc["arcs"][:arc_i]) + 1
                    )
                    for ch in [ch]]  # unpack

    # Simpler flat enumeration (arc_i, global_ch_num, ch_meta)
    all_chapters = []
    global_num = 1
    for arc_i, arc in enumerate(toc["arcs"]):
        for ch in arc["chapters"]:
            all_chapters.append((arc_i, global_num, ch))
            global_num += 1

    if args.chapters:
        start, end = parse_range(args.chapters, total_chapters)
        selected = all_chapters[start:end]
        print(f"Fetching chapters {start + 1}–{end} ({len(selected)} total)")

        # Rebuild arcs with only selected chapters
        arc_map: dict[int, list] = {}
        for arc_i, ch_num, ch in selected:
            arc_map.setdefault(arc_i, []).append((ch_num, ch))
        toc["arcs"] = [
            {"name": toc["arcs"][arc_i]["name"],
             "chapters": [c for _, c in chs]}
            for arc_i, chs in sorted(arc_map.items())
        ]
        # Keep ch_num aligned for cache paths
        selected_for_fetch = [(arc_i, ch_num, ch) for arc_i, ch_num, ch in selected]
    else:
        selected_for_fetch = all_chapters

    # Fetch all chapter bodies, grouped by arc
    arc_bodies: list[list[str]] = []
    prev_arc_i = -1
    cur_bodies: list[str] = []

    for arc_i, ch_num, ch in selected_for_fetch:
        if arc_i != prev_arc_i:
            if cur_bodies:
                arc_bodies.append(cur_bodies)
            cur_bodies = []
            prev_arc_i = arc_i
        cached = _chapter_cache_path(code, ch_num).exists() and not args.refresh
        label = "(cached)" if cached else ""
        print(f"  [{arc_i+1}] {ch['title']} {label}")
        cur_bodies.append(fetch_chapter_body(
            code, ch_num, ch["url"],
            single=toc["single"], refresh=args.refresh,
        ))

    if cur_bodies:
        arc_bodies.append(cur_bodies)

    # Build EPUB
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", toc["title"])
    out_path = Path(args.output) if args.output else Path(f"{safe_name}.epub")
    build_epub(toc, arc_bodies, out_path)

    # Optionally email
    if send_to:
        _check_smtp(smtp_user, smtp_pass)
        send_to_kindle(out_path, send_to, smtp_user, smtp_pass, smtp_host, smtp_port)


if __name__ == "__main__":
    main()
