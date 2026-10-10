from typing import Any
from mcp.server.fastmcp import FastMCP
from datetime import datetime
import math
import re
import urllib.parse
import xml.etree.ElementTree as ET
import httpx

mcp = FastMCP("literature research")

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

# Number of candidates fetched from arXiv before local re-ranking
CANDIDATE_POOL = 50
SUMMARY_SENTENCES = 2
SUMMARY_MAX_WORDS = 70

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "in",
    "is", "it", "its", "of", "on", "or", "that", "the", "to", "was", "were",
    "with", "we", "our", "this", "these", "those", "which", "using", "use",
    "based", "via", "into", "about", "over", "how", "what", "can", "new",
}


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens without stopwords."""
    words = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def build_query(terms: list[str], operator: str) -> str:
    """Build an arXiv search_query that matches the terms in title or abstract."""
    parts = [f"(ti:{t} OR abs:{t})" for t in terms]
    return f" {operator} ".join(parts)


async def fetch_candidates(client: httpx.AsyncClient, search_query: str) -> list[dict[str, Any]]:
    """Fetch and parse candidate papers from the arXiv API."""
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": CANDIDATE_POOL,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    response = await client.get(url, timeout=30.0)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    papers = []
    for entry in root.findall(f"{ATOM}entry"):
        title = entry.find(f"{ATOM}title")
        summary = entry.find(f"{ATOM}summary")
        published = entry.find(f"{ATOM}published")
        link = entry.find(f"{ATOM}id")
        authors = [a.text for a in entry.findall(f"{ATOM}author/{ATOM}name") if a.text]
        pdf = entry.find(f"{ATOM}link[@title='pdf']")
        primary = entry.find(f"{ARXIV_NS}primary_category")

        papers.append({
            "title": " ".join(title.text.split()) if title is not None and title.text else "No title",
            "url": link.text if link is not None else "No URL",
            "pdf_url": pdf.get("href") if pdf is not None else None,
            "authors": authors,
            "published": published.text if published is not None else None,
            "category": primary.get("term") if primary is not None else None,
            "abstract": " ".join(summary.text.split()) if summary is not None and summary.text else "",
        })
    return papers


def rank_papers(papers: list[dict[str, Any]], query: str, query_terms: list[str]) -> list[dict[str, Any]]:
    """Re-rank papers with BM25 over title + abstract, plus phrase and recency bonuses."""
    k1, b = 1.5, 0.75
    title_weight = 3  # title matches count more than abstract matches

    docs = []
    for p in papers:
        tokens = tokenize(p["title"]) * title_weight + tokenize(p["abstract"])
        docs.append(tokens)

    n = len(docs)
    avg_len = sum(len(d) for d in docs) / n if n else 1
    unique_terms = set(query_terms)
    doc_freq = {t: sum(1 for d in docs if t in d) for t in unique_terms}
    phrase = query.lower().strip()
    current_year = datetime.now().year

    for paper, tokens in zip(papers, docs):
        score = 0.0
        for term in unique_terms:
            tf = tokens.count(term)
            if not tf:
                continue
            idf = math.log(1 + (n - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(tokens) / avg_len))

        # Coverage: reward papers that mention all query terms
        coverage = sum(1 for t in unique_terms if t in tokens) / len(unique_terms)
        score *= 0.5 + 0.5 * coverage

        # Exact phrase bonus
        if len(query_terms) > 1:
            if phrase in paper["title"].lower():
                score *= 1.5
            elif phrase in paper["abstract"].lower():
                score *= 1.2

        # Mild recency bonus (up to +10% for papers from the last 5 years)
        year = int(paper["published"][:4]) if paper["published"] else current_year - 10
        score *= 1 + 0.02 * max(0, 5 - (current_year - year))

        paper["relevance_score"] = round(score, 3)

    return sorted(papers, key=lambda p: p["relevance_score"], reverse=True)


def summarize_abstract(abstract: str, query_terms: list[str]) -> str:
    """Extractive summary: pick the most informative sentences of the abstract."""
    if not abstract:
        return "No abstract available."

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", abstract) if s.strip()]
    if len(sentences) <= SUMMARY_SENTENCES:
        chosen = sentences
    else:
        cue_words = ("we propose", "we present", "we introduce", "we show", "we develop",
                     "this paper", "in this work", "results", "outperform", "achieve")
        terms = set(query_terms)
        scored = []
        for i, sentence in enumerate(sentences):
            lower = sentence.lower()
            score = sum(1 for t in set(tokenize(sentence)) if t in terms)
            score += 1.5 * sum(1 for c in cue_words if c in lower)
            if i == 0:
                score += 1  # first sentence usually states the topic
            scored.append((score, i))
        top = sorted(scored, key=lambda x: (-x[0], x[1]))[:SUMMARY_SENTENCES]
        chosen = [sentences[i] for _, i in sorted(top, key=lambda x: x[1])]

    words = " ".join(chosen).split()
    summary = " ".join(words[:SUMMARY_MAX_WORDS])
    return summary + (" ..." if len(words) > SUMMARY_MAX_WORDS else "")


def format_authors(authors: list[str]) -> str:
    if not authors:
        return "Unknown"
    if len(authors) > 3:
        return ", ".join(authors[:3]) + " et al."
    return ", ".join(authors)


@mcp.tool()
async def search_arxiv(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """
    Find the most relevant arXiv papers on a topic, each with a short summary of its abstract.

    Fetches a larger candidate pool from arXiv, re-ranks it by relevance to the
    query (BM25 on title and abstract) and returns the top papers.

    Args:
        query: The research topic, e.g. "retrieval augmented generation".
        max_results: Number of papers to return (default 5, max 20).
    """
    max_results = max(1, min(max_results, 20))
    query_terms = tokenize(query)
    if not query_terms:
        return [{"error": "Please provide a more specific search topic."}]

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            # Strict search first (all terms), fall back to any term if too few hits
            papers = await fetch_candidates(client, build_query(query_terms, "AND"))
            if len(papers) < max_results and len(query_terms) > 1:
                seen = {p["url"] for p in papers}
                extra = await fetch_candidates(client, build_query(query_terms, "OR"))
                papers += [p for p in extra if p["url"] not in seen]

        if not papers:
            return [{"message": f"No results found on arXiv for '{query}'."}]

        results = []
        for rank, paper in enumerate(rank_papers(papers, query, query_terms)[:max_results], start=1):
            results.append({
                "rank": rank,
                "title": paper["title"],
                "authors": format_authors(paper["authors"]),
                "year": paper["published"][:4] if paper["published"] else "Unknown",
                "category": paper["category"],
                "summary": summarize_abstract(paper["abstract"], query_terms),
                "url": paper["url"],
                "pdf_url": paper["pdf_url"],
                "relevance_score": paper["relevance_score"],
                "source": "arXiv",
            })
        return results

    except Exception as e:
        return [{"error": f"Error searching arXiv: {str(e)}"}]


if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')
