#!/usr/bin/env python3
"""Fetch a public URL and extract article-like text for the card skill."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}
CONTAINER_TAGS = {"article", "main"}
BOILERPLATE_PATTERNS = [
    re.compile(r"^subscribe\b", re.I),
    re.compile(r"^sign in\b", re.I),
    re.compile(r"^log in\b", re.I),
    re.compile(r"^share\b", re.I),
    re.compile(r"^copyright\b", re.I),
    re.compile(r"^all rights reserved\b", re.I),
]
ARTICLE_END_PATTERNS = [
    re.compile(r"^test your knowledge$", re.I),
    re.compile(r"^quick reference$", re.I),
    re.compile(r"^comments$", re.I),
    re.compile(r"^login to join", re.I),
    re.compile(r"^questions$", re.I),
    re.compile(r"^related articles$", re.I),
    re.compile(r"^next:", re.I),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch a URL and extract article-like text as JSON.")
    parser.add_argument("url", help="Public http(s) URL to fetch.")
    parser.add_argument("--out", type=Path, help="Optional path to write the extracted JSON.")
    parser.add_argument("--max-chars", type=int, default=24000, help="Maximum extracted text characters.")
    parser.add_argument("--min-chars", type=int, default=600, help="Minimum text characters required.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Fetch timeout in seconds.")
    parser.add_argument("--max-bytes", type=int, default=5_000_000, help="Maximum response bytes to read.")
    return parser.parse_args()


def validate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only absolute http(s) URLs are supported")
    return urllib.parse.urlunparse(parsed)


def fetch_html(url: str, timeout: float, max_bytes: int) -> tuple[str, str, str | None]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; card-skill-url-ingest/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type")
            final_url = response.geturl()
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        detail = error.read(800).decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"URL fetch failed: {error.reason}") from error

    if len(raw) > max_bytes:
        raise RuntimeError(f"Response is larger than --max-bytes ({max_bytes})")
    encoding = "utf-8"
    if content_type:
        match = re.search(r"charset=([^;\s]+)", content_type, re.I)
        if match:
            encoding = match.group(1).strip('"')
    return raw.decode(encoding, errors="replace"), final_url, content_type


def normalize_text(text: str, max_chars: int | None = None) -> str:
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines: list[str] = []
    previous = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if line == previous:
            continue
        if any(pattern.search(line) for pattern in BOILERPLATE_PATTERNS):
            continue
        lines.append(line)
        previous = line
    cleaned = "\n".join(lines).strip()
    if max_chars and len(cleaned) > max_chars:
        clipped = cleaned[:max_chars].rsplit("\n", 1)[0].strip()
        return clipped or cleaned[:max_chars].strip()
    return cleaned


def trim_to_article_text(text: str) -> str:
    """Drop obvious navigation before and footers after the article body."""
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return text

    start = 0
    for index, line in enumerate(lines):
        looks_like_paragraph = len(line) >= 90 and bool(re.search(r"[.!?。！？]$", line))
        if looks_like_paragraph:
            start = index
            break

    end = len(lines)
    for index in range(start, len(lines)):
        line = lines[index]
        if any(pattern.search(line) for pattern in ARTICLE_END_PATTERNS):
            end = index
            break

    trimmed = "\n\n".join(lines[start:end]).strip()
    return trimmed if len(trimmed) >= 400 else text


class ArticleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.body_depth = 0
        self.container_stack: list[dict[str, Any]] = []
        self.candidates: list[dict[str, str]] = []
        self.body_parts: list[str] = []
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.h1_parts: list[str] = []
        self.in_title = False
        self.in_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "body":
            self.body_depth += 1
        if tag == "title":
            self.in_title = True
        if tag == "h1":
            self.in_h1 = True
        if tag == "meta":
            self._handle_meta(attr_map)
        if self.skip_depth:
            return
        if self._is_content_container(tag, attr_map):
            self.container_stack.append({"tag": tag, "parts": []})
        if tag in BLOCK_TAGS:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if tag == "title":
            self.in_title = False
        if tag == "h1":
            self.in_h1 = False
        if self.skip_depth:
            return
        if tag in BLOCK_TAGS:
            self._append("\n")
        if tag == "body" and self.body_depth:
            self.body_depth -= 1
        if self.container_stack and self.container_stack[-1]["tag"] == tag:
            parts = self.container_stack.pop()["parts"]
            text = normalize_text("".join(parts))
            if text:
                self.candidates.append({"method": f"html-{tag}", "text": text})

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
        if self.in_h1:
            self.h1_parts.append(data)
        if self.body_depth:
            self._append(data)

    def _append(self, value: str) -> None:
        self.body_parts.append(value)
        for candidate in self.container_stack:
            candidate["parts"].append(value)

    def _handle_meta(self, attrs: dict[str, str]) -> None:
        key = attrs.get("property") or attrs.get("name")
        content = attrs.get("content")
        if key and content:
            self.meta[key.lower()] = content.strip()

    def _is_content_container(self, tag: str, attrs: dict[str, str]) -> bool:
        role = attrs.get("role", "").lower()
        item_type = attrs.get("itemtype", "").lower()
        class_text = attrs.get("class", "").lower()
        if tag in CONTAINER_TAGS or role == "main":
            return True
        if "article" in item_type:
            return True
        if any(token in class_text for token in ["article", "post-content", "markdown", "prose"]):
            return True
        return False

    def best_text(self, max_chars: int) -> tuple[str, str]:
        body_text = normalize_text("".join(self.body_parts))
        candidates = [candidate for candidate in self.candidates if len(candidate["text"]) >= 200]
        if candidates:
            best = max(candidates, key=lambda candidate: len(candidate["text"]))
            return normalize_text(trim_to_article_text(best["text"]), max_chars), best["method"]
        return normalize_text(trim_to_article_text(body_text), max_chars), "html-body"

    def title(self) -> str | None:
        candidates = [
            self.meta.get("og:title"),
            self.meta.get("twitter:title"),
            normalize_text("".join(self.h1_parts)),
            normalize_text("".join(self.title_parts)),
        ]
        for candidate in candidates:
            if candidate:
                return candidate
        return None

    def description(self) -> str | None:
        for key in ("description", "og:description", "twitter:description"):
            if self.meta.get(key):
                return self.meta[key]
        return None


def extract(url: str, timeout: float, max_bytes: int, max_chars: int, min_chars: int) -> dict[str, Any]:
    html_text, final_url, content_type = fetch_html(url, timeout, max_bytes)
    parser = ArticleTextParser()
    parser.feed(html_text)
    text, method = parser.best_text(max_chars)
    if len(text) < min_chars:
        raise RuntimeError(
            f"Extracted text is too short ({len(text)} chars); page may be paywalled, JS-only, or not article-like",
        )
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    title = parser.title()
    result = {
        "url": url,
        "final_url": final_url,
        "title": title,
        "description": parser.description(),
        "content_type": content_type,
        "text": text,
        "char_count": len(text),
        "word_count": len(re.findall(r"\w+", text, flags=re.UNICODE)),
        "fetched_at": fetched_at,
        "source_notes": {
            "source_type": "url",
            "url": url,
            "final_url": final_url,
            "title": title,
            "fetched_at": fetched_at,
            "extraction_method": method,
            "content_char_count": len(text),
            "completion_mode": "researched",
        },
    }
    return result


def main() -> None:
    args = parse_args()
    try:
        url = validate_url(args.url)
        result = extract(url, args.timeout, args.max_bytes, args.max_chars, args.min_chars)
    except Exception as error:
        failure = {"url": args.url, "ok": False, "error": str(error)}
        print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1) from error

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
