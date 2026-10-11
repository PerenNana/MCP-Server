from typing import Any
from mcp.server.fastmcp import FastMCP
import urllib.parse
import xml.etree.ElementTree as ET
import httpx

from ranking import CANDIDATE_POOL, build_results, clamp_max_results, tokenize

mcp = FastMCP("literature research")

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


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
            "year": int(published.text[:4]) if published is not None and published.text else None,
            "category": primary.get("term") if primary is not None else None,
            "abstract": " ".join(summary.text.split()) if summary is not None and summary.text else "",
        })
    return papers


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
    max_results = clamp_max_results(max_results)
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

        return build_results(papers, query, query_terms, max_results, source="arXiv")

    except Exception as e:
        return [{"error": f"Error searching arXiv: {str(e)}"}]


if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')
