"""
Shared search logic for all literature sources.

Each source fetches a pool of candidate papers and normalizes them to a dict with:
    title, url, pdf_url, authors (list[str]), year (int | None), category, abstract
and optionally "extra" (dict of source-specific fields added to the output).

`build_results` then re-ranks the candidates (BM25 on title + abstract) and
returns the top papers with a short extractive summary of each abstract.
"""
from typing import Any
from datetime import datetime
import math
import re

# Number of candidates fetched from a source before local re-ranking
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
    """Lowercase word tokens without stopwords (hyphenated words are split, e.g. "retrieval-augmented")."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def clamp_max_results(max_results: int) -> int:
    return max(1, min(max_results, 20))


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
        year = paper["year"] or current_year - 10
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


def build_results(papers: list[dict[str, Any]], query: str, query_terms: list[str],
                  max_results: int, source: str) -> list[dict[str, Any]]:
    """Rank the candidates and format the top papers for the MCP client."""
    if not papers:
        return [{"message": f"No results found on {source} for '{query}'."}]

    results = []
    for rank, paper in enumerate(rank_papers(papers, query, query_terms)[:max_results], start=1):
        results.append({
            "rank": rank,
            "title": paper["title"],
            "authors": format_authors(paper["authors"]),
            "year": paper["year"] or "Unknown",
            "category": paper["category"],
            **paper.get("extra", {}),
            "summary": summarize_abstract(paper["abstract"], query_terms),
            "url": paper["url"],
            "pdf_url": paper["pdf_url"],
            "relevance_score": paper["relevance_score"],
            "source": source,
        })
    return results
