from collections.abc import Callable, Iterator
from importlib.resources import files
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from leadradar.collectors.http import CollectionError, HttpCollector
from leadradar.config import USER_AGENT, DirectoryConfig
from leadradar.models import RawBusiness


def origin(url: str) -> tuple:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port


class RobotsGuard:
    def __init__(self, http: HttpCollector, start_url: str):
        self.http = http
        self.allowed_origin = origin(start_url)
        self.parser: RobotFileParser | None = None

    def check(self, url: str):
        if origin(url) != self.allowed_origin or urlsplit(url).username is not None:
            raise CollectionError("Cross-origin or credential-bearing request refused")
        if self.parser is None:
            parts = urlsplit(url)
            robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
            response = self.http.request("GET", robots_url, allow_missing=True)
            parser = RobotFileParser()
            parser.parse([] if response.status_code == 404 else response.text.splitlines())
            self.parser = parser
            delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*") or 0
            rate = parser.request_rate(USER_AGENT) or parser.request_rate("*")
            interval = max(delay, rate.seconds / rate.requests if rate and rate.requests else 0)
            if interval > 60:
                raise CollectionError(
                    "robots.txt requests a delay over 60s; collect manually later"
                )
            self.http.policy.interval = max(self.http.policy.interval, interval)
        if not self.parser.can_fetch(USER_AGENT, url):
            raise CollectionError("robots.txt disallows this path; collection stopped")


def parse_page(
    html: str,
    url: str,
    *,
    source: str,
    source_kind: str,
    card_selector=".business",
    next_selector='a[rel="next"]',
    fields: dict[str, str] | None = None,
) -> tuple[list[RawBusiness], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    fields = fields or {
        "business_name": ".name",
        "website": "a.website",
        "category": ".category",
        "city": ".city",
        "region": ".region",
        "country": ".country",
        "phone": ".phone",
        "address": ".address",
    }
    records = []
    for card in soup.select(card_selector):
        data = {}
        for field, selector in fields.items():
            item = card.select_one(selector)
            value = (
                (item.get("href") if field == "website" else item.get_text(" ", strip=True))
                if item
                else None
            )
            if field == "website" and value and value.startswith(("/", "./", "../")):
                value = urljoin(url, value)
            data[field] = value
        stable_id = card.get("data-id") or card.get("id")
        if not stable_id:
            raise CollectionError("Directory card lacks data-id/id; a stable identity is required")
        records.append(
            RawBusiness(
                **data,
                source=source,
                source_kind=source_kind,
                source_record_id=str(stable_id),
                source_url=url + "#" + str(stable_id),
            )
        )
    next_tag = soup.select_one(next_selector)
    next_url = urljoin(url, next_tag["href"]) if next_tag and next_tag.get("href") else None
    return records, next_url


class DirectorySource:
    def __init__(self, http: HttpCollector, config: DirectoryConfig, *, javascript=False):
        self.http, self.config, self.javascript = http, config, javascript
        self.name = f"directory:{config.name}"
        self.guard = RobotsGuard(http, str(config.start_url))

    def collect(self, limit: int) -> Iterator[RawBusiness]:
        url, visited, count = str(self.config.start_url), set(), 0
        while url and count < limit:
            url = urldefrag(url)[0]
            if url in visited:
                raise CollectionError("Pagination cycle detected")
            visited.add(url)
            self.guard.check(url)
            if self.javascript:
                html = render_page(self.http, self.guard, url, self.config.ready_selector)
            else:
                html = self.http.request("GET", url).text
            records, url = parse_page(
                html,
                url,
                source=self.name,
                source_kind="live",
                card_selector=self.config.card_selector,
                next_selector=self.config.next_selector,
                fields=self.config.fields,
            )
            if not records:
                raise CollectionError("No business cards matched; check selectors or blocking")
            for record in records:
                yield record
                count += 1
                if count >= limit:
                    break


def render_page(http: HttpCollector, guard: RobotsGuard, url: str, ready: str) -> str:
    """All browser network requests pass through the same robots/rate/budget checks."""
    from playwright.sync_api import Error, sync_playwright

    failures = []

    def route_request(route):
        request = route.request
        if request.resource_type not in {"document", "script", "xhr", "fetch"}:
            route.abort()
            return
        try:
            if request.method != "GET":
                raise CollectionError("Browser adapter permits public GET requests only")
            guard.check(request.url)
            response = http.request("GET", request.url)
            route.fulfill(
                status=response.status_code,
                body=response.content,
                headers={"content-type": response.headers.get("content-type", "text/html")},
            )
        except CollectionError as exc:
            failures.append(str(exc))
            route.abort()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chromium")
            try:
                context = browser.new_context(user_agent=USER_AGENT, service_workers="block")
                context.route("**/*", route_request)
                # Disable socket channels outside the bounded HTTP transport.
                context.route_web_socket("**/*", lambda route: route.close())
                page = context.new_page()
                page.goto(url, timeout=http.policy.timeout * 1000, wait_until="domcontentloaded")
                page.wait_for_selector(ready, state="attached", timeout=http.policy.timeout * 1000)
                if failures:
                    raise CollectionError(failures[0])
                return page.content()
            finally:
                browser.close()
    except Error as exc:
        message = (
            failures[0]
            if failures
            else (
                "Browser rendering failed; check selector and run: "
                "python -m playwright install chromium"
            )
        )
        raise CollectionError(message) from exc


class FixtureSource:
    """Packaged multi-page synthetic directory; never visits an external website."""

    name = "fixture"

    def __init__(self, *, loader: Callable[[str], str] | None = None):
        self.loader = loader or self.load

    @staticmethod
    def load(url: str) -> str:
        name = Path(urlsplit(url).path).name
        if name not in {"page1.html", "page2.html"}:
            raise CollectionError("Fixture pagination escaped the controlled directory")
        return files("leadradar.fixtures").joinpath(name).read_text(encoding="utf-8")

    def collect(self, limit: int) -> Iterator[RawBusiness]:
        url, visited, count = "https://fixture.invalid/page1.html", set(), 0
        while url and count < limit:
            if url in visited:
                raise CollectionError("Fixture pagination cycle")
            visited.add(url)
            records, url = parse_page(
                self.loader(url), url, source=self.name, source_kind="fixture"
            )
            for record in records:
                yield record
                count += 1
                if count >= limit:
                    break
