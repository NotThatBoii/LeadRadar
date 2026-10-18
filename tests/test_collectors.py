import gzip

import httpx
import pytest

from leadradar.collectors.http import CollectionError, HttpCollector
from leadradar.collectors.runner import collect
from leadradar.config import DirectoryConfig, RequestPolicy
from leadradar.sources.directory import DirectorySource, FixtureSource, parse_page
from leadradar.sources.overpass import OverpassSource, parse_bbox


def transport(handler, **policy):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpCollector(
        RequestPolicy(interval=0, backoff=0, **policy), client=client, sleep=lambda _: None
    )


def directory_config(**updates):
    return DirectoryConfig(
        name="controlled-test",
        start_url="https://directory.example/page1.html",
        policy_url="https://directory.example/terms",
        permission_note="Controlled test fixture",
        reviewed_on="2026-10-18",
        automated_collection_permitted=True,
        **updates,
    )


def test_fixture_actual_multi_page_parsing():
    records = list(FixtureSource().collect(100))
    assert len(records) == 5
    assert len({record.source_record_id for record in records}) == 4
    assert all(record.source_kind == "fixture" for record in records)
    assert records[0].business_name == "Sample   Copper Café"
    assert records[-1].website == "javascript:alert(1)"


def test_fixture_limit_stops_pagination():
    calls = []

    def load(url):
        calls.append(url)
        return FixtureSource.load(url)

    assert len(list(FixtureSource(loader=load).collect(1))) == 1
    assert len(calls) == 1


def test_card_requires_stable_id():
    with pytest.raises(CollectionError, match="stable identity"):
        parse_page(
            '<article class="business"><b class="name">Name</b></article>',
            "https://example.com",
            source="test",
            source_kind="fixture",
        )


def test_static_directory_robots_pagination_and_limits():
    urls = []

    def handle(request):
        urls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\nCrawl-delay: 3")
        return httpx.Response(200, text=FixtureSource.load(str(request.url)))

    http = transport(handle)
    records = list(DirectorySource(http, directory_config()).collect(100))
    assert len(records) == 5
    assert len(urls) == 3 and urls[0].endswith("robots.txt")
    assert http.policy.interval == 3


def test_robots_denial_prevents_page_request():
    http = transport(lambda _: httpx.Response(200, text="User-agent: *\nDisallow: /"))
    with pytest.raises(CollectionError, match="robots.txt"):
        list(DirectorySource(http, directory_config()).collect(10))
    assert http.requests == 1


def test_robots_unavailable_fails_closed():
    http = transport(lambda _: httpx.Response(503))
    with pytest.raises(CollectionError, match="exhausted"):
        list(DirectorySource(http, directory_config()).collect(10))


def test_robots_404_allows_public_page():
    http = transport(
        lambda request: (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, text=FixtureSource.load(str(request.url)))
        )
    )
    assert len(list(DirectorySource(http, directory_config()).collect(1))) == 1


def test_cross_origin_pagination_is_refused():
    def handle(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        page = FixtureSource.load(str(request.url)).replace(
            'href="page2.html"', 'href="https://evil.example/"'
        )
        return httpx.Response(200, text=page)

    http = transport(handle)
    with pytest.raises(CollectionError, match="Cross-origin"):
        list(DirectorySource(http, directory_config()).collect(100))
    assert http.requests == 2


def test_pagination_cycle_is_reported():
    http = transport(
        lambda r: (
            httpx.Response(404)
            if r.url.path == "/robots.txt"
            else httpx.Response(
                200,
                text=FixtureSource.load(str(r.url)).replace(
                    'href="page2.html"', 'href="page1.html"'
                ),
            )
        )
    )
    with pytest.raises(CollectionError, match="cycle"):
        list(DirectorySource(http, directory_config()).collect(100))


def test_no_matching_cards_is_diagnostic():
    http = transport(lambda _: httpx.Response(200, text="<h1>Challenge or changed layout</h1>"))
    with pytest.raises(CollectionError, match="No business cards"):
        list(DirectorySource(http, directory_config()).collect(1))


def test_retry_recovers_transient_failure():
    calls, delays = [], []

    def handle(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200, text="OK")

    http = transport(handle)
    http.policy.backoff = 2
    http.sleep = delays.append
    assert http.request("GET", "https://example.com").text == "OK"
    assert http.requests == 2 and 2 in delays
    assert len(http.warnings) == 1


def test_timeout_retries_are_bounded():
    def timeout(request):
        raise httpx.ReadTimeout("simulated timeout", request=request)

    http = transport(timeout, max_attempts=2)
    with pytest.raises(CollectionError, match="ReadTimeout; exhausted 2"):
        http.request("GET", "https://example.com")
    assert http.requests == 2


@pytest.mark.parametrize("code", [401, 403, 404, 302])
def test_permanent_failures_are_not_retried(code):
    http = transport(lambda _: httpx.Response(code))
    with pytest.raises(CollectionError):
        http.request("GET", "https://example.com")
    assert http.requests == 1


def test_rate_limit_honors_retry_after():
    delays, count = [], 0

    def handle(_):
        nonlocal count
        count += 1
        return (
            httpx.Response(429, headers={"Retry-After": "7"}) if count == 1 else httpx.Response(200)
        )

    http = transport(handle)
    http.sleep = delays.append
    http.request("GET", "https://example.com")
    assert 7 in delays


def test_long_cooldown_stops_without_retry():
    http = transport(lambda _: httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(CollectionError, match="retry later"):
        http.request("GET", "https://example.com")
    assert http.requests == 1


def test_request_budget_includes_retries():
    http = transport(lambda _: httpx.Response(503), max_requests=1)
    with pytest.raises(CollectionError, match="budget"):
        http.request("GET", "https://example.com")


def test_response_size_is_bounded():
    http = transport(lambda _: httpx.Response(200, content=b"x" * 2048), max_bytes=1024)
    with pytest.raises(CollectionError, match="byte limit"):
        http.request("GET", "https://example.com")


def test_compressed_response_is_decoded_exactly_once():
    http = transport(
        lambda _: httpx.Response(
            200, content=gzip.compress(b'{"elements": []}'), headers={"Content-Encoding": "gzip"}
        )
    )
    response = http.request("GET", "https://example.com")
    assert response.json() == {"elements": []}
    assert "content-encoding" not in response.headers


def test_rate_interval_applies_to_each_request():
    delays = []
    http = transport(lambda _: httpx.Response(200))
    http.policy.interval = 5
    http.clock = lambda: 10
    http.sleep = delays.append
    http.request("GET", "https://example.com")
    http.request("GET", "https://example.com")
    assert delays == [5]


def test_overpass_builds_bounded_query_and_maps_public_fields():
    def handle(request):
        assert request.method == "POST" and b"maxsize" in request.content
        return httpx.Response(
            200,
            json={
                "elements": [
                    {
                        "type": "node",
                        "id": 123,
                        "lat": 14.5,
                        "lon": 121,
                        "tags": {
                            "name": "Test Cafe",
                            "amenity": "cafe",
                            "website": "cafe.example",
                            "addr:city": "Demo",
                        },
                    }
                ]
            },
        )

    source = OverpassSource(transport(handle), "14.4,120.9,14.6,121.1", "amenity=cafe")
    assert "out tags center 100" in source.query(100)
    record = list(source.collect(100))[0]
    assert record.source_record_id == "node/123"
    assert record.location_key == "14.50000,121.00000"
    assert record.country is None and record.phone is None
    assert record.attribution == "© OpenStreetMap contributors"


@pytest.mark.parametrize("bbox", ["x", "0,0,2,2", "90,0,91,1", "1,1,0,0", "nan,0,1,1"])
def test_overpass_rejects_unsafe_bounds(bbox):
    with pytest.raises(ValueError):
        parse_bbox(bbox)


def test_query_injection_rejected():
    with pytest.raises(ValueError, match="key=value"):
        OverpassSource(
            transport(lambda _: httpx.Response(200)), "14.4,120.9,14.6,121.1", 'amenity=cafe"];out;'
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"elements": [], "remark": "runtime error: timeout"},
        {"elements": [{"type": "fake", "id": 1}]},
    ],
)
def test_overpass_incomplete_or_invalid_response_fails(payload):
    source = OverpassSource(
        transport(lambda _: httpx.Response(200, json=payload)), "14.4,120.9,14.6,121.1"
    )
    with pytest.raises(CollectionError):
        list(source.collect(100))


def test_partial_records_survive_source_failure(db, raw):
    class Broken:
        name = "broken-test"

        def collect(self, limit):
            yield raw
            raise CollectionError("simulated unavailable second page")

    report = collect(Broken(), db, 10)
    assert report.status == "partial" and report.new == 1
    assert len(db.records(kind="fixture")) == 1
    assert db.stats()["recent_runs"][0]["status"] == "partial"


def test_graceful_interruption(db, raw):
    class Interrupted:
        name = "interrupted-test"

        def collect(self, limit):
            yield raw
            raise KeyboardInterrupt

    report = collect(Interrupted(), db, 10)
    assert report.status == "interrupted" and report.ended_at is not None
    assert report.new == 1
