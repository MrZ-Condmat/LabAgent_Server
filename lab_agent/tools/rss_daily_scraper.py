import hashlib
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from ..utils import timestamp_str, to_app_timezone


class RSSDailyScraper:
    """Fetch and normalize journal articles from RSS/Atom feeds."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.logger = logging.getLogger("tools.rss_daily_scraper")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36 LabAgent/0.1"
            ),
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        })

    def fetch_feed_articles(self, feed: Dict[str, str]) -> List[Dict[str, str]]:
        feed_url = feed["feed_url"]
        source_name = feed.get("name", feed_url)

        try:
            self.logger.info("Fetching RSS feed %s from %s", source_name, feed_url)
            response = self.session.get(feed_url, timeout=self.timeout)
            response.raise_for_status()
            parsed = feedparser.parse(response.content)

            if parsed.bozo:
                self.logger.warning("Feed parse warning for %s: %s", source_name, parsed.bozo_exception)

            articles = []
            seen_keys = set()
            for entry in parsed.entries:
                article = self._parse_entry(entry, feed)
                if not article or not article.get("title"):
                    continue

                dedupe_key = article.get("url") or article.get("id") or article.get("title")
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                articles.append(article)

            self.logger.info("Fetched %s articles from %s", len(articles), source_name)
            return articles
        except Exception as e:
            self.logger.error("Error fetching RSS feed %s: %s", feed_url, e)
            return []

    def _parse_entry(self, entry, feed: Dict[str, str]) -> Optional[Dict[str, str]]:
        title = self._clean_text(getattr(entry, "title", ""))
        url = self._entry_link(entry)
        abstract = self._entry_summary(entry)
        authors = self._entry_authors(entry)
        subjects = self._entry_subjects(entry, feed)
        article_id = getattr(entry, "id", "") or getattr(entry, "guid", "") or url

        if not title:
            return None

        if not article_id:
            digest = hashlib.sha1(f"{feed.get('name', '')}:{title}".encode("utf-8")).hexdigest()[:12]
            article_id = f"rss:{digest}"

        entry_datetime = self._entry_datetime(entry)

        return {
            "id": article_id,
            "title": title,
            "authors": authors,
            "abstract": abstract or "No abstract available",
            "subjects": subjects,
            "url": url,
            "pdf_url": "",
            "source": feed.get("name", ""),
            "journal": feed.get("name", ""),
            "feed_url": feed.get("feed_url", ""),
            "published_date": self._entry_date(entry, entry_datetime),
            "published_date_iso": to_app_timezone(entry_datetime).date().isoformat() if entry_datetime else "",
            "published_datetime": to_app_timezone(entry_datetime).isoformat() if entry_datetime else "",
            "fetched_date": timestamp_str(),
        }

    def _entry_link(self, entry) -> str:
        link = getattr(entry, "link", "")
        if link:
            return link

        for item in getattr(entry, "links", []) or []:
            if item.get("rel") in ("alternate", None) and item.get("href"):
                return item["href"]
        return ""

    def _entry_summary(self, entry) -> str:
        summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
        if not summary and getattr(entry, "content", None):
            first_content = entry.content[0]
            summary = first_content.get("value", "") if isinstance(first_content, dict) else ""
        return self._clean_text(summary)

    def _entry_authors(self, entry) -> str:
        authors = []
        for author in getattr(entry, "authors", []) or []:
            if isinstance(author, dict):
                name = author.get("name", "")
            else:
                name = getattr(author, "name", "")
            if name:
                authors.append(self._clean_text(name))

        if not authors and getattr(entry, "author", ""):
            authors.append(self._clean_text(entry.author))

        return ", ".join(authors)

    def _entry_subjects(self, entry, feed: Dict[str, str]) -> str:
        tags = []
        for tag in getattr(entry, "tags", []) or []:
            if isinstance(tag, dict):
                term = tag.get("term", "")
            else:
                term = getattr(tag, "term", "")
            if term:
                tags.append(self._clean_text(term))

        if not tags and feed.get("name"):
            tags.append(feed["name"])

        return "; ".join(tags)

    def _entry_datetime(self, entry) -> Optional[datetime]:
        for attr in ("published", "updated", "created"):
            value = getattr(entry, attr, "")
            if value:
                try:
                    parsed = parsedate_to_datetime(str(value))
                    if parsed.tzinfo is None:
                        return parsed.replace(tzinfo=timezone.utc)
                    return parsed.astimezone(timezone.utc)
                except Exception:
                    pass

        for attr in ("published_parsed", "updated_parsed", "created_parsed"):
            value = getattr(entry, attr, None)
            if value:
                return datetime(*value[:6], tzinfo=timezone.utc)

        return None

    def _entry_date(self, entry, entry_datetime: Optional[datetime] = None) -> str:
        for attr in ("published", "updated", "created"):
            value = getattr(entry, attr, "")
            if value:
                return self._clean_text(value)
        if entry_datetime:
            return to_app_timezone(entry_datetime).date().isoformat()
        return ""

    def _clean_text(self, value: str) -> str:
        if not value:
            return ""
        text = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
        return re.sub(r"\s+", " ", text).strip()

    def close(self):
        self.session.close()
