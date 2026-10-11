from typing import Any
from mcp.server.fastmcp import FastMCP
import asyncio
import os
import httpx

from ranking import CANDIDATE_POOL, build_results, clamp_max_results, tokenize

mcp = FastMCP("semantic scholar research")

S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,abstract,tldr,authors,year,venue,url,openAccessPdf,citationCount,fieldsOfStudy,externalIds"
# Optional: a free API key (https://www.semanticscholar.org/product/api) raises the rate limit
S2_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
MAX_RETRIES = 3


async def fetch_candidates(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    """Fetch and normalize candidate papers from the Semantic Scholar API."""
    params = {"query": query, "limit": CANDIDATE_POOL, "fields": S2_FIELDS}
    headers = {"x-api-key": S2_API_KEY} if S2_API_KEY else {}

    # Without an API key requests share a public rate limit, so retry on HTTP 429
    for attempt in range(MAX_RETRIES):
        response = await client.get(S2_API, params=params, headers=headers, timeout=30.0)
        if response.status_code != 429:
            break
        await asyncio.sleep(2 ** attempt)
    response.raise_for_status()

    papers = []
    for item in response.json().get("data") or []:
        abstract = item.get("abstract") or (item.get("tldr") or {}).get("text") or ""
        external_ids = item.get("externalIds") or {}
        fields = item.get("fieldsOfStudy") or []

        papers.append({
            "title": " ".join((item.get("title") or "No title").split()),
            "url": item.get("url") or "No URL",
            "pdf_url": (item.get("openAccessPdf") or {}).get("url") or None,
            "authors": [a["name"] for a in item.get("authors") or [] if a.get("name")],
            "year": item.get("year"),
            "category": fields[0] if fields else None,
            "abstract": " ".join(abstract.split()),
            "extra": {
                "venue": item.get("venue") or "Unknown",
                "citation_count": item.get("citationCount") or 0,
                "doi": external_ids.get("DOI"),
            },
        })
    return papers


@mcp.tool()
async def search_semantic_scholar(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """
    Find the most relevant Semantic Scholar papers on a topic, each with a short summary of its abstract.

    Fetches a larger candidate pool from Semantic Scholar, re-ranks it by relevance
    to the query (BM25 on title and abstract) and returns the top papers including
    venue and citation count.

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
            papers = await fetch_candidates(client, query)

        return build_results(papers, query, query_terms, max_results, source="Semantic Scholar")

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return [{"error": "Semantic Scholar rate limit reached. Please try again in a minute "
                              "or set SEMANTIC_SCHOLAR_API_KEY."}]
        return [{"error": f"Error searching Semantic Scholar: {str(e)}"}]
    except Exception as e:
        return [{"error": f"Error searching Semantic Scholar: {str(e)}"}]


if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')
