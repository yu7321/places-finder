"""Lightweight email scraper for place websites.

Follows each place's website, fetches the homepage plus a handful of
common contact/impressum URL patterns, and pulls an owner email address
out of the HTML. Language-agnostic — the contact-page path list covers
Italian, English, German, and Spanish conventions.
"""

import re
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .models import Place


EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# Common contact-page paths across Italian, English, German, Spanish sites.
CONTACT_PATHS = [
    "/contatti",
    "/contatti.html",
    "/contact",
    "/contact-us",
    "/kontakt",
    "/contacto",
    "/it/contatti",
    "/en/contact",
    "/de/kontakt",
]

# Email prefix preference — generic info-style addresses are most useful
PREFERRED_PREFIXES = [
    "info",
    "contact",
    "contatti",
    "contatto",
    "prenotazioni",
    "booking",
    "reservations",
    "kontakt",
    "hello",
    "ciao",
]

JUNK_DOMAINS = (
    "example.com",
    "domain.com",
    "email.com",
    "sentry.io",
    "wixpress.com",
    "godaddy.com",
)

JUNK_LOCAL_PARTS = ("noreply", "no-reply", "donotreply")


class EmailScraper:
    def __init__(self, timeout: int = 8, max_pages: int = 4):
        self.timeout = timeout
        self.max_pages = max_pages
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en;q=0.9,it;q=0.8,de;q=0.7",
            }
        )

    @staticmethod
    def _normalize(url: str) -> str:
        if not url:
            return ""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url.rstrip("/")

    def _fetch(self, url: str) -> str | None:
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                return None
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype and "xml" not in ctype:
                return None
            return resp.text
        except Exception:
            return None

    def _emails_from_html(self, html: str) -> list[str]:
        if not html:
            return []
        found: list[str] = []
        seen: set[str] = set()

        def add(email: str) -> None:
            email = email.strip().strip(".,;:")
            if not email or "@" not in email:
                return
            lower = email.lower()
            if lower in seen:
                return
            local, _, domain = lower.partition("@")
            if any(j in domain for j in JUNK_DOMAINS):
                return
            if any(local.startswith(j) for j in JUNK_LOCAL_PARTS):
                return
            if domain.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
                return
            seen.add(lower)
            found.append(email)

        soup = BeautifulSoup(html, "lxml")
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if href.lower().startswith("mailto:"):
                add(href[7:].split("?")[0])

        text = soup.get_text(separator=" ")
        for match in EMAIL_PATTERN.findall(text):
            add(match)

        return found

    def _find_contact_links(self, base_url: str, html: str) -> list[str]:
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")
        keywords = ("contatti", "contatto", "contact", "kontakt", "contacto")
        links: list[str] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = (a.get_text() or "").lower()
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            if not any(k in href.lower() or k in text for k in keywords):
                continue
            full = urljoin(base_url + "/", href)
            key = full.rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            links.append(full)
        return links

    @staticmethod
    def _pick_best(emails: Iterable[str], website_domain: str) -> str:
        emails = list(dict.fromkeys(emails))
        if not emails:
            return ""

        same_domain = [
            e for e in emails if website_domain and website_domain in e.lower()
        ]
        pool = same_domain or emails

        for prefix in PREFERRED_PREFIXES:
            for e in pool:
                if e.lower().split("@", 1)[0].startswith(prefix):
                    return e
        return pool[0]

    def find_email(self, website: str) -> str:
        base = self._normalize(website)
        if not base:
            return ""

        domain = urlparse(base).netloc.replace("www.", "").lower()
        collected: list[str] = []

        homepage_html = self._fetch(base)
        collected.extend(self._emails_from_html(homepage_html or ""))

        candidate_pages: list[str] = []
        if homepage_html:
            candidate_pages.extend(self._find_contact_links(base, homepage_html))
        for path in CONTACT_PATHS:
            candidate_pages.append(base + path)

        seen_pages: set[str] = {base.lower()}
        fetched = 0
        for page in candidate_pages:
            key = page.rstrip("/").lower()
            if key in seen_pages:
                continue
            seen_pages.add(key)
            if fetched >= self.max_pages:
                break
            html = self._fetch(page)
            fetched += 1
            if not html:
                continue
            collected.extend(self._emails_from_html(html))
            if any(
                e.lower().split("@", 1)[0].startswith(p)
                for e in collected
                for p in PREFERRED_PREFIXES
            ):
                break

        return self._pick_best(collected, domain)

    def enrich(
        self,
        items: list[Place],
        progress_callback=None,
    ) -> None:
        total = len(items)
        for i, item in enumerate(items, start=1):
            if progress_callback:
                progress_callback(i, total, item.name)
            if not item.website or item.email:
                continue
            try:
                item.email = self.find_email(item.website)
            except Exception as e:
                print(f"  ! email scrape failed for {item.website}: {e}")
