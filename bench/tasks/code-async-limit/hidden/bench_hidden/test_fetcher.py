import asyncio

from fetcher import fetch_all


def test_limit_order_and_errors():
    active = 0
    peak = 0

    async def fetch(url):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01 * (len(url) % 3))
        active -= 1
        if url == "bad":
            raise RuntimeError("boom")
        return url.upper()

    urls = ["a", "bb", "bad", "ccc", "d", "ee", "f"]
    result = asyncio.run(fetch_all(urls, fetch, limit=2))
    assert peak <= 2
    assert [r if isinstance(r, str) else "ERR" for r in result] == ["A", "BB", "ERR", "CCC", "D", "EE", "F"]
    assert isinstance(result[2], RuntimeError)


def test_default_limit():
    active = 0
    peak = 0

    async def fetch(url):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1
        return url

    assert asyncio.run(fetch_all(list("abcdefgh"), fetch)) == list("abcdefgh")
    assert peak == 3
