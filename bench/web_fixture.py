"""A stand-in web for research tasks, inside the agent's sandbox (no network).

The task's pages (/web/pages.json: url, title, published, text) are served
under their own host names, which the sandbox maps to 127.0.0.1 (run.py adds
the hosts), on port 8081; a search engine on 8080 answers in DuckDuckGo's
HTML result format (what web_tools.mjs parses), ranking the pages for the
query. So the product's own web_research runs unchanged against a fixed web
with planted traps (forum posts, content farms, old news, conflicting sources).

    python bench/web_fixture.py /web
"""

from __future__ import annotations

import html
import json
import math
import re
import sys
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def terms(text: str) -> list[str]:
    lower = text.lower()
    out = re.findall(r"[a-z0-9]+", lower)
    for run in re.findall(r"[㐀-鿿]+", lower):
        out += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return out


def rank(query: str, pages: list[dict], limit: int = 8) -> list[dict]:
    docs = [Counter(terms(p["title"] + " " + p["text"])) for p in pages]
    average = sum(sum(d.values()) for d in docs) / max(1, len(docs))
    scored = []
    for page, doc in zip(pages, docs, strict=True):
        length = sum(doc.values())
        score = 0.0
        for term in set(terms(query)):
            if doc[term]:
                frequency = sum(1 for d in docs if d[term])
                idf = math.log(1 + (len(docs) - frequency + 0.5) / (frequency + 0.5))
                score += idf * doc[term] * 2.2 / (doc[term] + 1.2 * (0.25 + 0.75 * length / average))
        if score > 0:
            scored.append((score, page))
    return [page for _, page in sorted(scored, key=lambda item: -item[0])[:limit]]


def page_html(page: dict) -> str:
    meta = (f'<meta property="article:published_time" content="{page["published"]}">'
            if page.get("published") else "")
    body = "".join(f"<p>{html.escape(line)}</p>" for line in page["text"].split("\n") if line.strip())
    return f"<html><head><title>{html.escape(page['title'])}</title>{meta}</head><body>{body}</body></html>"


def main() -> None:
    pages = json.loads((Path(sys.argv[1]) / "pages.json").read_text(encoding="utf-8"))
    by_url = {p["url"]: p for p in pages}

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            if self.server.server_address[1] == 8080:
                query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
                results = "".join(
                    f'<div class="result"><a class="result__a" href="{html.escape(p["url"])}">'
                    f'{html.escape(p["title"])}</a><a class="result__snippet">{html.escape(p["text"][:120])}</a></div>'
                    for p in rank(query, pages))
                return self.reply(200, f"<html><body>{results}</body></html>")
            url = f"http://{self.headers.get('Host', '')}{self.path}"
            page = by_url.get(url)
            return self.reply(200, page_html(page)) if page else self.reply(404, "not found")

        def log_message(self, *args: object) -> None:
            pass

    servers = [ThreadingHTTPServer(("127.0.0.1", port), Handler) for port in (8080, 8081)]
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    print("web fixture ready", flush=True)
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
