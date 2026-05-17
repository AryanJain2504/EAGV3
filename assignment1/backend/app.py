from __future__ import annotations

import json
import os
import re
from collections import Counter
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


STOPWORDS = {
    "a",
    "about",
    "after",
    "all",
    "also",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "before",
    "but",
    "by",
    "can",
    "for",
    "from",
    "has",
    "have",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "out",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "to",
    "up",
    "was",
    "we",
    "with",
    "you",
    "your",
}


class LinkItem(BaseModel):
    text: str = ""
    url: str = ""


class AnalyzeRequest(BaseModel):
    title: str = ""
    url: str = ""
    text: str = ""
    selection: str = ""
    links: List[LinkItem] = Field(default_factory=list)
    apiKey: Optional[str] = None


class AnalysisResult(BaseModel):
    title: str
    url: str
    summary: List[str]
    relatedContent: List[str]
    pageIntent: str
    keyTerms: List[str]
    linkCategories: Dict[str, List[Dict[str, str]]]
    selectionUsed: bool
    source: str


app = FastAPI(title="PagePilot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def split_sentences(text: str) -> List[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    return re.split(r"(?<=[.!?])\s+", cleaned)


def words(text: str) -> List[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9'-]+", text.lower())
    return [token for token in tokens if token not in STOPWORDS and len(token) > 2]


def summarize_locally(text: str, title: str) -> List[str]:
    sentences = split_sentences(text)
    if not sentences:
        return [f"This page appears to be about {title}."] if title else ["No readable text was captured."]

    counts = Counter(words(text))
    scored = []
    for sentence in sentences:
        sentence_words = words(sentence)
        if not sentence_words:
            continue
        score = sum(counts[word] for word in sentence_words) / (len(sentence_words) ** 0.5)
        scored.append((score, sentence.strip()))

    summary: List[str] = []
    for _, sentence in sorted(scored, reverse=True):
        if sentence not in summary:
            summary.append(sentence)
        if len(summary) == 3:
            break

    if len(summary) < 3:
        for sentence in sentences:
            clean = sentence.strip()
            if clean and clean not in summary:
                summary.append(clean)
            if len(summary) == 3:
                break

    return summary[:3]


def classify_link(link: LinkItem, current_host: str) -> str:
    label = f"{link.text} {link.url}".lower()
    host = urlparse(link.url).netloc.lower()

    if not link.url:
        return "other"
    if host and host == current_host:
        return "internal"
    if any(token in label for token in ("docs", "documentation", "api", "guide", "help")):
        return "docs"
    if any(token in label for token in ("support", "faq", "contact")):
        return "support"
    if any(token in label for token in ("twitter", "x.com", "linkedin", "youtube", "instagram", "facebook")):
        return "social"
    if any(token in label for token in ("buy", "cart", "checkout", "product", "pricing", "shop")):
        return "shopping"
    if any(token in label for token in ("download", ".zip", ".pdf", ".csv", ".doc")):
        return "download"
    return "external"


def group_links(links: List[LinkItem], page_url: str) -> Dict[str, List[Dict[str, str]]]:
    current_host = urlparse(page_url).netloc.lower()
    categories: Dict[str, List[Dict[str, str]]] = {}
    for link in links:
        category = classify_link(link, current_host)
        categories.setdefault(category, []).append({"text": link.text, "url": link.url})
    return {name: items for name, items in categories.items() if items}


def page_intent(title: str, text: str, link_categories: Dict[str, List[Dict[str, str]]]) -> str:
    blob = f"{title} {text}".lower()
    if any(token in blob for token in ("login", "sign in", "account")):
        return "authentication"
    if any(token in blob for token in ("docs", "documentation", "api")):
        return "documentation"
    if any(token in blob for token in ("cart", "checkout", "buy", "pricing")):
        return "shopping"
    if any(token in blob for token in ("news", "article", "blog", "post")):
        return "reading"
    if any(token in blob for token in ("support", "help", "faq")):
        return "support"
    if link_categories.get("internal"):
        return "workflow"
    return "research"


def key_terms(text: str) -> List[str]:
    counts = Counter(words(text))
    return [term for term, _ in counts.most_common(8)]


def pick_related_content(title: str, url: str, text: str, intent: str, terms: List[str]) -> List[str]:
    domain = urlparse(url).netloc.replace("www.", "")
    topic = terms[0] if terms else (title or "this topic")
    suggestions = [
        f"Search the web for more on {topic} from {domain or 'the current site'}.",
        f"Look for a shorter guide or overview about {topic} to compare with this page.",
    ]
    if intent == "documentation":
        suggestions.append(f"Find the official docs or a quickstart related to {topic}.")
    elif intent == "shopping":
        suggestions.append(f"Compare {topic} with reviews, pricing, or alternatives before deciding.")
    elif intent == "authentication":
        suggestions.append(f"Check the help center or account settings for {domain or 'this site'}.")
    else:
        suggestions.append(f"Explore one practical example or tutorial using {topic}.")
    return suggestions[:3]


def build_prompt(title: str, url: str, text: str, selection: str, link_categories: Dict[str, List[Dict[str, str]]]) -> str:
    compact_links = json.dumps(link_categories, ensure_ascii=False)
    source_text = selection or text
    return (
        'Analyze this webpage and respond ONLY with a valid JSON object. No other text.\n\n'
        f'Title: {title}\n'
        f'URL: {url}\n'
        f'Text: {source_text[:2000]}\n'
        f'Links by category: {compact_links}\n\n'
        'Return this exact JSON structure:\n'
        '{\n'
        '  "summary": ["bullet 1", "bullet 2", "bullet 3"],\n'
        '  "relatedContent": ["suggestion 1", "suggestion 2", "suggestion 3"],\n'
        '  "pageIntent": "one-word-intent",\n'
        '  "keyTerms": ["term1", "term2", "term3"]\n'
        '}\n\n'
        'Make summaries concise. Make suggestions practical and relevant to the site.'
    )


def call_gemini(api_key: str, prompt: str) -> Optional[Dict[str, List[str]]]:
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    try:
        response = requests.post(
            endpoint,
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": api_key,
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1000},
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        print(f"Gemini raw response length: {len(text)}")
        text = text.strip()
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
        print(f"After cleanup length: {len(text)}")
        parsed = json.loads(text)
        return {
            "summary": parsed.get("summary", []),
            "relatedContent": parsed.get("relatedContent", []),
            "pageIntent": parsed.get("pageIntent", "research"),
            "keyTerms": parsed.get("keyTerms", []),
        }
    except Exception as e:
        print(f"Gemini API error: {e}")
        return None


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalysisResult)
def analyze(request: AnalyzeRequest) -> Dict[str, object]:
    link_categories = group_links(request.links, request.url)
    fallback_summary = summarize_locally(request.selection or request.text, request.title)
    fallback_terms = key_terms(request.selection or request.text)
    fallback_intent = page_intent(request.title, request.text, link_categories)
    fallback_related = pick_related_content(request.title, request.url, request.text, fallback_intent, fallback_terms)

    api_key = request.apiKey or os.getenv("GEMINI_API_KEY")
    if api_key:
        prompt = build_prompt(request.title, request.url, request.text, request.selection, link_categories)
        gemini = call_gemini(api_key, prompt)
        if gemini:
            return {
                "title": request.title,
                "url": request.url,
                "summary": gemini.get("summary") or fallback_summary,
                "relatedContent": gemini.get("relatedContent") or fallback_related,
                "pageIntent": gemini.get("pageIntent") or fallback_intent,
                "keyTerms": gemini.get("keyTerms") or fallback_terms,
                "linkCategories": link_categories,
                "selectionUsed": bool(request.selection),
                "source": "gemini",
            }

    return {
        "title": request.title,
        "url": request.url,
        "summary": fallback_summary,
        "relatedContent": fallback_related,
        "pageIntent": fallback_intent,
        "keyTerms": fallback_terms,
        "linkCategories": link_categories,
        "selectionUsed": bool(request.selection),
        "source": "fallback",
    }
