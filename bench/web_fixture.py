"""A stand-in web for research tasks, inside the agent's sandbox (no network).

The task's pages (/web/pages.json: url, title, published, text) are served at
their own addresses, https as on the real web: every name resolves to the
sandbox's loopback (a DNS answer on 127.0.0.1:53, which run.py makes the
sandbox's resolver), and port 443 answers with a certificate for the name
asked, signed by the bench's own CA (run.py web_ca), which the sandbox's
Node, Python and PowerShell trust; port 80 serves the same pages. A page or
host the card does not have is a 404, the way a real site answers, never a
name that does not resolve: training data must not learn "the network is
down" from the sandbox (2026-10-09: `:8081` addresses and EAI_AGAIN in the
teacher's recordings). A search engine on 8080 answers in DuckDuckGo's HTML
result format (what web_tools.mjs parses), ranking the pages for the query.
So the product's own web_research runs unchanged against a fixed web with
planted traps (forum posts, content farms, old news, conflicting sources).
A page of type application/json is an API endpoint: served as JSON, matched
on its path and the query parameters it names (paging and format parameters
such as limit do not matter), refused (401) without its API's key
(page["key"]: header or query, name, value), and never a search hit. Like a
real API, a host with endpoints answers every other request there in JSON:
a missing parameter is a 400, a value it has no data for is a 404 that says
which value it has (so an agent that asks for today learns the data ends
yesterday instead of guessing), and an unknown path is a 404.

    python bench/web_fixture.py /web
"""

from __future__ import annotations

import html
import json
import math
import re
import socket
import ssl
import subprocess
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


# Parameters that shape an answer rather than choose it: any value gets the data.
SHAPING = {"limit", "page", "per_page", "page_size", "count", "size", "offset", "sort",
           "order", "format", "lang", "locale", "fields", "units", "tz", "timezone"}


def params(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def same_address(a: str, b: str) -> bool:
    """One address over http or https (a site serves both)."""
    return a.split("://", 1)[-1] == b.split("://", 1)[-1]


def find(pages: list[dict], url: str) -> dict | None:
    """The page at this address: exactly, or an API endpoint on the same path
    whose query parameters the request carries (in any order, with more, such
    as the key; paging and format values may differ)."""
    exact = next((p for p in pages if same_address(p["url"], url)), None)
    if exact:
        return exact
    asked, given = urlsplit(url), params(url)
    for page in pages:
        known = urlsplit(page["url"])
        if page.get("type") == "application/json" and (known.netloc, known.path) == (asked.netloc, asked.path) \
                and all(given.get(k) == v for k, v in params(page["url"]).items() if k not in SHAPING):
            return page
    return None


def api_error(pages: list[dict], url: str) -> tuple[int, dict, dict | None] | None:
    """What an API host answers to a request no endpoint matches: (status, JSON
    body, an endpoint of that host for its key), or None when the host has no
    endpoints (an ordinary site: a plain 404)."""
    asked, given = urlsplit(url), params(url)
    endpoints = [p for p in pages if p.get("type") == "application/json"
                 and urlsplit(p["url"]).netloc == asked.netloc]
    if not endpoints:
        return None
    same = [p for p in endpoints if urlsplit(p["url"]).path == asked.path]
    if not same:
        return 404, {"error": "not_found", "message": f"No endpoint GET {asked.path}."}, endpoints[0]
    wanted = {k: v for k, v in params(same[0]["url"]).items() if k not in SHAPING}
    missing = [k for k in wanted if k not in given]
    if missing:
        return 400, {"error": "missing_parameter",
                     "message": f"Missing required parameter: {', '.join(missing)}."}, same[0]
    wrong = [(k, given[k], v) for k, v in wanted.items() if given[k] != v]
    message = "; ".join(
        f"no data for {k}={asked_value}" + (f" (data is available for {k}={known})"
                                            if re.fullmatch(r"\d{4}-?\d{2}-?\d{2}", known) else "")
        for k, asked_value, known in wrong)
    return 404, {"error": "no_data", "message": message[0].upper() + message[1:] + "."}, same[0]


def page_html(page: dict) -> str:
    meta = (f'<meta property="article:published_time" content="{page["published"]}">'
            if page.get("published") else "")
    body = "".join(f"<p>{html.escape(line)}</p>" for line in page["text"].split("\n") if line.strip())
    return f"<html><head><title>{html.escape(page['title'])}</title>{meta}</head><body>{body}</body></html>"


def main() -> None:
    pages = json.loads((Path(sys.argv[1]) / "pages.json").read_text(encoding="utf-8"))
    readable = [p for p in pages if p.get("type") != "application/json"]  # what a search finds

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, body: str, kind: str = "text/html") -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server's name
            if self.server.server_address[1] == 8080:
                query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
                results = "".join(
                    f'<div class="result"><a class="result__a" href="{html.escape(p["url"])}">'
                    f'{html.escape(p["title"])}</a><a class="result__snippet">{html.escape(p["text"][:120])}</a></div>'
                    for p in rank(query, readable))
                return self.reply(200, f"<html><body>{results}</body></html>")
            host = re.sub(r":(80|443)$", "", self.headers.get("Host", ""))
            url = f"https://{host}{self.path}"
            page, error = find(pages, url), None
            if page is None:
                error = api_error(pages, url)
                if error is None:
                    return self.reply(404, "not found")
                page = error[2]
            if page.get("type") != "application/json":
                return self.reply(200, page_html(page))
            key = page.get("key")
            given = (self.headers.get(key["name"]) if key and key["in"] == "header"
                     else params(url).get(key["name"]) if key else None)
            if key and given != key["value"]:
                return self.reply(401, json.dumps({"error": "invalid or missing API key"}), "application/json")
            if error:
                return self.reply(error[0], json.dumps(error[1], ensure_ascii=False), "application/json")
            return self.reply(200, page["text"], "application/json")

        def log_message(self, *args: object) -> None:
            pass

    servers = [ThreadingHTTPServer(("127.0.0.1", port), Handler) for port in (8080, 80, 443)]
    servers[2].socket = Certificates(CA).context().wrap_socket(servers[2].socket, server_side=True)
    threading.Thread(target=answer_dns, daemon=True).start()
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    print("web fixture ready", flush=True)
    servers[0].serve_forever()


CA = Path("/opt/bench/webca")  # run.py web_ca: ca.pem and ca.key, read-only


class Certificates:
    """A certificate for whatever name a client asks for, signed by the bench's
    CA with the image's own openssl, made once per name."""

    def __init__(self, ca: Path) -> None:
        self.ca, self.made, self.lock = ca, {}, threading.Lock()
        self.folder = Path("/tmp/webca")
        self.folder.mkdir(exist_ok=True)

    def for_name(self, name: str) -> ssl.SSLContext:
        with self.lock:
            if name not in self.made:
                key, cert = self.folder / f"{name}.key", self.folder / f"{name}.pem"
                subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                                "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "30", "-subj", f"/CN={name}",
                                "-addext", f"subjectAltName=DNS:{name}", "-addext", "basicConstraints=critical,CA:FALSE",
                                "-addext", "extendedKeyUsage=serverAuth",
                                "-CA", str(self.ca / "ca.pem"), "-CAkey", str(self.ca / "ca.key")],
                               check=True, capture_output=True)
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(cert, key)
                self.made[name] = context
            return self.made[name]

    def context(self) -> ssl.SSLContext:
        base = self.for_name("localhost")

        def choose(connection: ssl.SSLObject, name: str | None, _: ssl.SSLContext) -> None:
            if name:
                connection.context = self.for_name(name.lower())

        base.sni_callback = choose
        return base


def answer_dns() -> None:
    """Every name is this sandbox (A 127.0.0.1); no IPv6 address, so clients
    use that one."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 53))
    while True:
        query, client = sock.recvfrom(512)
        if len(query) < 12:
            continue
        end = 12
        while end < len(query) and query[end]:
            end += query[end] + 1
        qtype = int.from_bytes(query[end + 1:end + 3], "big")
        question = query[12:end + 5]
        answers = 1 if qtype == 1 else 0
        reply = query[:2] + b"\x81\x80" + b"\x00\x01" + answers.to_bytes(2, "big") + b"\x00\x00\x00\x00" + question
        if answers:
            reply += b"\xc0\x0c\x00\x01\x00\x01" + (60).to_bytes(4, "big") + b"\x00\x04" + bytes([127, 0, 0, 1])
        sock.sendto(reply, client)


if __name__ == "__main__":
    main()
