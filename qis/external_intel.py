from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import html
import urllib.request
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class Headline:
    source: str
    title: str
    link: str


@dataclass(frozen=True)
class ExternalIntel:
    label: str
    score: float
    headlines: list[Headline]
    reason: str
    fetched_at: str


class ExternalIntelAnalyzer:
    FEEDS = {
        "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "Cointelegraph": "https://cointelegraph.com/rss",
        "Decrypt": "https://decrypt.co/feed",
    }
    POSITIVE = (
        "etf inflow",
        "approval",
        "approved",
        "institutional",
        "adoption",
        "record inflows",
        "rate cut",
        "easing",
        "partnership",
        "treasury",
        "accumulates",
    )
    NEGATIVE = (
        "hack",
        "exploit",
        "lawsuit",
        "sec sues",
        "outflow",
        "ban",
        "crackdown",
        "liquidation",
        "rate hike",
        "sanction",
        "bankruptcy",
        "fraud",
    )

    def analyze(self, limit_per_feed: int = 8) -> ExternalIntel:
        headlines: list[Headline] = []
        for source, url in self.FEEDS.items():
            headlines.extend(self._fetch_feed(source, url, limit_per_feed))
        score = self._score(headlines)
        if score >= 0.18:
            label = "constructive"
        elif score <= -0.18:
            label = "defensive"
        else:
            label = "mixed"
        reason = self._reason(headlines)
        return ExternalIntel(
            label=label,
            score=score,
            headlines=headlines[: limit_per_feed * len(self.FEEDS)],
            reason=reason,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    def _fetch_feed(self, source: str, url: str, limit: int) -> list[Headline]:
        request = urllib.request.Request(url, headers={"User-Agent": "qis-external-intel/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read()
        except Exception:
            return []
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return []
        items = []
        for item in root.findall(".//item")[:limit]:
            title = self._text(item, "title")
            link = self._text(item, "link")
            if title:
                items.append(Headline(source, html.unescape(title.strip()), link.strip()))
        return items

    def _score(self, headlines: list[Headline]) -> float:
        if not headlines:
            return 0.0
        total = 0.0
        for headline in headlines:
            title = headline.title.lower()
            total += sum(1 for word in self.POSITIVE if word in title)
            total -= sum(1 for word in self.NEGATIVE if word in title)
        return max(-1.0, min(1.0, total / max(6, len(headlines) * 0.35)))

    def _reason(self, headlines: list[Headline]) -> str:
        if not headlines:
            return "external news unavailable"
        positive_hits = []
        negative_hits = []
        for headline in headlines:
            title = headline.title.lower()
            if any(word in title for word in self.POSITIVE):
                positive_hits.append(headline.title)
            if any(word in title for word in self.NEGATIVE):
                negative_hits.append(headline.title)
        bits = []
        if positive_hits:
            bits.append(f"positive={len(positive_hits)}")
        if negative_hits:
            bits.append(f"negative={len(negative_hits)}")
        if not bits:
            bits.append("no strong keywords")
        return "; ".join(bits)

    @staticmethod
    def _text(item: ET.Element, tag: str) -> str:
        child = item.find(tag)
        return child.text if child is not None and child.text else ""
