from typing import Any
from mcp.server.fastmcp import FastMCP
import urllib.parse
import xml.etree.ElementTree as ET
import httpx

mcp = FastMCP("literature research")

@mcp.tool()
async def search_arxiv(query: str) -> list[dict[str, Any]]:
    """
    Search for papers on arXiv.
    
    Args:
        query: The search query string.
    """
    try:
        # Format query for arXiv API
        encoded_query = urllib.parse.quote(query)
        url = f"https://export.arxiv.org/api/query?search_query=all:{encoded_query}&start=0&max_results=5"
        
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url, timeout=30.0)
            response.raise_for_status()
            
            # Parse XML response
            root = ET.fromstring(response.content)
            
            results = []
            for entry in root.findall('{http://www.w3.org/2005/Atom}entry'):
                title = entry.find('{http://www.w3.org/2005/Atom}title')
                summary = entry.find('{http://www.w3.org/2005/Atom}summary')
                published = entry.find('{http://www.w3.org/2005/Atom}published')
                authors = entry.findall('{http://www.w3.org/2005/Atom}author/{http://www.w3.org/2005/Atom}name')
                link = entry.find('{http://www.w3.org/2005/Atom}id')
                
                results.append({
                    "title": title.text.strip() if title is not None else "No title",
                    "url": link.text if link is not None else "No URL",
                    "authors": ", ".join([author.text for author in authors]),
                    "year": published.text[:4] if published is not None else "Unknown",
                    "abstract": summary.text.strip() if summary is not None else "No abstract",
                    "source": "arXiv"
                })
            
            if not results:
                return [{"message": "No results found on arXiv for this query."}]
                
            return results
            
    except Exception as e:
        return [{"error": f"Error searching arXiv: {str(e)}"}]


if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')