<!-- version: 2 -->
# Web Search Tools

## WebSearch

Search the web.

| Param | Description |
|-------|-------------|
| query | Search query |
| num_results | Max results (default 5) |
| engine | google/bing/baidu/bing_cn (auto if empty) |

```json
{"query": "Home Assistant automation examples"}
```

Returns titles + snippets. Use UrlFetch for full page.

**Routing rules:**
- Write query in the same language the user used
- NOT for fetching specific URLs — use UrlFetch directly
- Do not use WebSearch for stock/fund quotes — use StockQuery

## UrlFetch

Fetch URL content (chunk 0). When the user provides an explicit URL/link, ALWAYS use UrlFetch directly — do NOT use WebSearch.

```json
{"url": "https://example.com/page"}
```

Returns doc_id + total_chunks for WebReadChunk.

## WebReadChunk

Read more chunks of fetched page.

```json
{"doc_id": "xxx", "position": 1}
```

## StockQuery

Query stock/fund quotes.

```json
{"codes": "TSLA,AAPL"}
{"codes": "600519,000858"}
{"codes": "00700"}
```

| Market | Code format | Examples |
|--------|-------------|----------|
| US | Ticker symbol | TSLA, AAPL, NVDA, MSFT |
| China A-shares | 6-digit | 600519, 000001, 600036 |
| Hong Kong | 5-digit | 00700, 09988 |
| Funds | 6-digit | Various |

Returns: price, change, change_percent, open, high, low, volume, P/E.

## Workflow

```
1. WebSearch query="..."
   → Get titles, snippets, URLs

2. UrlFetch url="interesting_url"
   → Get chunk 0, doc_id

3. WebReadChunk doc_id="xxx" position=1
   → Get more content
```
