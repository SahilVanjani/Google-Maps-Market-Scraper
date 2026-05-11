from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from html import unescape
from typing import Iterable
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


EMAIL_RE = re.compile(r"(?i)\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b")
OBFUSCATED_AT_RE = re.compile(r"\s*(?:\[at\]|\(at\)|\sat\s)\s*", re.IGNORECASE)
OBFUSCATED_DOT_RE = re.compile(r"\s*(?:\[dot\]|\(dot\)|\sdot\s)\s*", re.IGNORECASE)
CONTACT_HINTS = (
    "contact",
    "about",
    "support",
    "team",
    "enquiry",
    "inquiry",
    "quote",
    "reach",
)


@dataclass(frozen=True, slots=True)
class EmailFinderConfig:
    timeout: float = 10.0
    max_pages: int = 5
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    )


def find_emails_for_website(url: str, config: EmailFinderConfig | None = None) -> list[str]:
    if not url:
        return []

    config = config or EmailFinderConfig()
    start_url = normalize_url(url)
    if not start_url:
        return []

    session = requests.Session()
    session.headers.update({"User-Agent": config.user_agent})

    seen: set[str] = set()
    queue: deque[str] = deque([start_url])
    emails: set[str] = set()
    base_host = normalized_host(start_url)

    while queue and len(seen) < config.max_pages:
        page_url = queue.popleft()
        if page_url in seen:
            continue
        seen.add(page_url)

        try:
            response = session.get(page_url, timeout=config.timeout, allow_redirects=True)
            content_type = response.headers.get("content-type", "")
            if response.status_code >= 400 or "text/html" not in content_type.lower():
                continue
        except requests.RequestException:
            continue

        html = response.text
        emails.update(extract_emails(html))
        soup = BeautifulSoup(html, "html.parser")
        emails.update(extract_mailto_emails(soup))

        for link in discover_contact_links(soup, response.url, base_host):
            if link not in seen:
                queue.append(link)

    return sorted(emails)


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not urlparse(url).scheme:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))


def normalized_host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def extract_emails(text: str) -> set[str]:
    text = unescape(text)
    normalized = OBFUSCATED_DOT_RE.sub(".", OBFUSCATED_AT_RE.sub("@", text))
    return {email.lower().strip(".,;:") for email in EMAIL_RE.findall(normalized)}


def extract_mailto_emails(soup: BeautifulSoup) -> set[str]:
    emails: set[str] = set()
    for anchor in soup.select('a[href^="mailto:"]'):
        href = anchor.get("href", "")
        address = href.removeprefix("mailto:").split("?", 1)[0]
        emails.update(extract_emails(address))
    return emails


def discover_contact_links(soup: BeautifulSoup, current_url: str, base_host: str) -> Iterable[str]:
    for anchor in soup.select("a[href]"):
        href = anchor.get("href", "").strip()
        label = " ".join(anchor.get_text(" ", strip=True).lower().split())
        target = urljoin(current_url, href)
        parsed = urlparse(target)

        if parsed.scheme not in {"http", "https"}:
            continue
        if normalized_host(target) != base_host:
            continue

        haystack = f"{parsed.path.lower()} {label}"
        if not any(hint in haystack for hint in CONTACT_HINTS):
            continue

        yield urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))

