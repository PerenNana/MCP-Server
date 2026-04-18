from mcp.server.fastmcp import FastMCP

import feedparser, httpx, asyncio

# Initialize the MCP server
mcp = FastMCP("news aggregator")

RSS_FEEDS = [
    ("tagesschau", "https://www.tagesschau.de/xml/rss2"),
    ("DW world", "https://rss.dw.com/xml/rss-en-world"),
    ("BBC News", "https://feeds.bbci.co.uk/news/rss.xml"),
    ("heise online", "https://www.heise.de/rss/heise-atom.xml"),
    ("Spiegel Online", "https://www.spiegel.de/schlagzeilen/tops/index.rss"), 
    ("New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"),
]

# Tool: News Headline Fetcher
@mcp.tool()
async def get_headlines(topic: str) -> list[dict]:
    """
    Search for news headlines across multiple sources based on a specific topic.
    
    This tool searches through RSS feeds from major news sources including Tagesschau, 
    Heise Online, Deutsche Welle, and BBC Technology to find relevant news articles.
    Results are filtered to show only articles containing the specified topic.
    
    Args:
        topic (str): The search topic or keyword to filter news articles.
                    Examples: "AI", "climate change", "technology", "politics"

    Returns:
        list[dict]: A list of news articles (max 10), each containing:
                   - title: Article headline
                   - link: URL to the full article
                   Returns empty message if no relevant articles found.
                   
    Example:
        >>> get_headlines("artificial intelligence")
        [{"title": "AI breakthrough...", "link": "https://..."}]
    """
    results = await get_news_from_all_feeds_async(topic)
    if not results:
        return [{"message": "No news found for this topic."}]
    return results

async def async_fetch_rss(url: str) -> feedparser.FeedParserDict:
    """
    Asynchronously fetch and parse RSS feed data from a given URL.
    
    Uses httpx for async HTTP requests with proper timeout handling,
    then parses the XML content using feedparser.
    
    Args:
        url (str): The RSS feed URL to fetch
        
    Returns:
        feedparser.FeedParserDict: Parsed RSS feed data structure
        
    Raises:
        httpx.HTTPError: If HTTP request fails
        httpx.TimeoutException: If request exceeds 15 second timeout
    """
    async with httpx.AsyncClient(follow_redirects=True) as client:  # Enable redirects
        try:
            response = await client.get(url, timeout=15.0)
            response.raise_for_status()
            data = response.text
        except httpx.HTTPError as e:
            # Handle HTTP errors gracefully
            print(f"HTTP Error fetching {url}: {e}")
            return feedparser.FeedParserDict()  # Return empty feed
        except Exception as e:
            # Handle other errors
            print(f"Error fetching {url}: {e}")
            return feedparser.FeedParserDict()
    # feedparser can parse from string data
    return feedparser.parse(data)

async def fetch_headlines_from_feed_async(feed_url: str, topic: str) -> list[dict]:
    """
    Fetch and filter news headlines from a single RSS feed source.
    
    Downloads RSS feed content, parses entries, and filters articles that contain
    the specified topic in either the title or description. Limits results to
    maximum 5 articles per feed for performance.
    
    Args:
        feed_url (str): The RSS feed URL to fetch from
        topic (str): Search keyword to filter articles (case-insensitive)

    Returns:
        list[dict]: Filtered news headlines, each containing:
                   - title: Article headline text
                   - link: Direct URL to the full article
                   Maximum 5 results per feed.
                   
    Note:
        Search is performed on both article title and description fields.
        Matching is case-insensitive for better coverage.
    """
    try:
        feed = await async_fetch_rss(feed_url)
        headlines = []
        
        # Check if feed was successfully parsed
        if not hasattr(feed, 'entries') or not feed.entries:
            return []  # Return empty list for failed feeds
            
        for entry in feed.entries:
            # filter entries by topic
            content = (entry.title + " " + entry.get("description", "")).lower()
            if topic.lower() in content:
                headlines.append({
                    "title": entry.title, 
                    "link": entry.link
                })
            if len(headlines) >= 5:
                break
        return headlines
    except Exception as e:
        print(f"Error processing feed {feed_url}: {e}")
        return []  # Return empty list on any error

async def get_news_from_all_feeds_async(topic: str) -> list[dict]:
    """
    Concurrently fetch and aggregate news from all configured RSS sources.
    
    Performs parallel requests to all RSS feeds simultaneously using asyncio.gather
    for optimal performance. Combines results from multiple sources and limits
    total output to prevent overwhelming responses.
    
    Configured Sources:
    - Tagesschau (German public news)
    - Heise Online (German tech news) 
    - Deutsche Welle World (International news)
    - BBC Technology (UK tech news)
    
    Args:
        topic (str): Search keyword to filter across all sources

    Returns:
        list[dict]: Aggregated news headlines from all sources (max 10 total):
                   - title: Article headline
                   - link: URL to full article
                   Results ordered by feed priority and limited for performance.
                   
    Performance:
        Concurrent execution typically completes in 2-5 seconds depending
        on network conditions and feed response times.
    """
    tasks = [
        fetch_headlines_from_feed_async(url, topic)
        for _, url in RSS_FEEDS
    ]
    results_per_feed = await asyncio.gather(*tasks)
    results = []
    for feed_results in results_per_feed:
        results.extend(feed_results)
        if len(results) >= 10:
            break
    return results


if __name__ == "__main__":
    # Run the MCP server with stdio transport and communicate with the client
    mcp.run(transport='stdio')