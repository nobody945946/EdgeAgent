import asyncio


async def fetch_all(urls, fetch, limit=3):
    """Fetch every url with `fetch` (an async function)."""
    return await asyncio.gather(*(fetch(url) for url in urls))
