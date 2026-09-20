import html
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


class InternetService:
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def wiki(self, query: str) -> dict[str, Any]:
        try:
            response = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query", "prop": "extracts|links", "exintro": "true",
                    "pllimit": 10, "format": "json", "redirects": 1, "titles": query,
                },
                headers={"User-Agent": "EAGV3-Assignment4/1.0"},
                timeout=self.timeout,
            )
            pages = response.json()["query"]["pages"]
            page = next(iter(pages.values()))
            return {
                "summary": html.unescape(page.get("extract", "No summary found."))[:1000],
                "links": [html.unescape(link["title"]) for link in page.get("links", [])],
            }
        except (KeyError, IndexError, requests.RequestException, ValueError):
            return {}

    def news(self, query: str) -> list[str]:
        try:
            response = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": f"{query} news"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=self.timeout,
            )
            snippets = []
            for part in response.text.split('class="result__snippet"')[1:6]:
                content = part.split("</a>")[0]
                content = content[content.find(">") + 1:] if ">" in content else content
                clean = html.unescape(re.sub(r"<[^>]+>", "", content)).strip()
                if clean:
                    snippets.append(clean)
            return snippets
        except requests.RequestException:
            return []

    def lookup_entity(self, query: str) -> dict[str, Any]:
        wiki = self.wiki(query)
        summary = re.sub(r"<[^>]+>", "", wiki.get("summary", "")).strip()
        return {
            "query": query,
            "summary": summary,
            "related": wiki.get("links", [])[:5],
            "news": self.news(query)[:5],
        }


class MarketService:
    KNOWN_TICKERS = {
        "google": "GOOGL", "alphabet": "GOOGL", "microsoft": "MSFT",
        "apple": "AAPL", "tesla": "TSLA", "amazon": "AMZN",
        "nvidia": "NVDA", "meta": "META", "srm": "SRM",
    }

    def __init__(self, internet: InternetService, llm_client: Any = None, model: str = "gemini-2.5-flash"):
        self.internet = internet
        self.llm_client = llm_client
        self.model = model

    def resolve_ticker(self, request: str) -> str:
        normalized = request.lower().strip()
        for name, ticker in self.KNOWN_TICKERS.items():
            if name in normalized:
                return ticker
        if self.llm_client is None:
            return "NONE"
        try:
            prompt = f"Return only the Yahoo Finance ticker for {request}. Return NONE if unknown."
            return self.llm_client.models.generate_content(model=self.model, contents=prompt).text.strip().upper()
        except Exception:
            return "NONE"

    def performance(self, ticker: str, days: int = 30) -> dict[str, Any]:
        days = max(1, min(days, 3650))
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        try:
            response = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params={"period1": int(start.timestamp()), "period2": int(end.timestamp()), "interval": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=self.internet.timeout,
            )
            result = response.json()["chart"]["result"][0]
            prices = [price for price in result["indicators"]["quote"][0]["close"] if price is not None]
            if not prices:
                return {}
            first, last = prices[0], prices[-1]
            return {
                "ticker": ticker, "days": days,
                "price": result["meta"].get("regularMarketPrice", last),
                "currency": result["meta"].get("currency", "USD"),
                "sparkline": prices, "start_price": first, "end_price": last,
                "change": last - first,
                "change_percent": ((last - first) / first * 100) if first else 0,
            }
        except (KeyError, IndexError, requests.RequestException, ValueError):
            return {}
