import asyncio


async def fetch_all(urls, fetch, limit=3):
    """Fetch every url with at most `limit` in flight; errors are returned in place."""
    gate = asyncio.Semaphore(limit)

    async def one(url):
        async with gate:
            return await fetch(url)

    return await asyncio.gather(*(one(url) for url in urls), return_exceptions=True)
