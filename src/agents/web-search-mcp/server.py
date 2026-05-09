"""Custom MCP server exposing a domain-restricted web search tool.

Runs on Azure Container Apps and is registered with the Foundry toolbox as
an `MCPTool`. Uses the open-source `ddgs` library (DuckDuckGo) as the search
backend — no API key required.

Configuration (env vars):
    ALLOWED_DOMAINS   Comma-separated list of domains (e.g. "kayak.com,booking.com").
                      Empty = no restriction.
    MAX_RESULTS       Cap on results returned per query (default 8).
    PORT              HTTP port (default 8000).
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from ddgs import DDGS
from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("web-search-mcp")


def _parse_domains(raw: str) -> list[str]:
    return [d.strip().lower().lstrip(".") for d in raw.split(",") if d.strip()]


_ALLOWED_DOMAINS = _parse_domains(os.environ.get("ALLOWED_DOMAINS", ""))
_MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "8"))
_PORT = int(os.environ.get("PORT", "80"))


def _domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _matches_allowed(url: str) -> bool:
    if not _ALLOWED_DOMAINS:
        return True
    host = _domain_of(url)
    return any(host == d or host.endswith("." + d) for d in _ALLOWED_DOMAINS)


mcp = FastMCP(
    name="web-search-mcp",
    host="0.0.0.0",
    port=_PORT,
    streamable_http_path="/mcp",
)


@mcp.tool(
    name="web_search",
    description=(
        "Search the public web and return ranked results restricted to the "
        "server's configured allowed domains. Use this for any query that "
        "requires factual or up-to-date information."
    ),
)
def web_search(query: str) -> list[dict]:
    """Run a domain-filtered DuckDuckGo search and return results."""
    log.info("web_search query=%r allowed=%s", query, _ALLOWED_DOMAINS or "*")

    # Push the domain filter into the query as a `site:` OR clause for better
    # recall before we post-filter the response.
    if _ALLOWED_DOMAINS:
        site_clause = " OR ".join(f"site:{d}" for d in _ALLOWED_DOMAINS)
        effective_query = f"({site_clause}) {query}"
    else:
        effective_query = query

    results: list[dict] = []
    with DDGS() as client:
        for hit in client.text(effective_query, max_results=_MAX_RESULTS * 3):
            url = hit.get("href") or hit.get("url") or ""
            if not url or not _matches_allowed(url):
                continue
            results.append({
                "title": hit.get("title") or "",
                "url": url,
                "snippet": hit.get("body") or "",
            })
            if len(results) >= _MAX_RESULTS:
                break

    log.info("web_search returning %d results", len(results))
    return results


if __name__ == "__main__":
    log.info("Starting web-search-mcp on :%d (allowed_domains=%s)", _PORT, _ALLOWED_DOMAINS or "*")
    mcp.run(transport="streamable-http")
