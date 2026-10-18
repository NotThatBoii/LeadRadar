import httpx
import pytest

from leadradar.collectors.http import HttpCollector
from leadradar.config import DirectoryConfig, RequestPolicy
from leadradar.sources.directory import DirectorySource


@pytest.mark.browser
def test_javascript_adapter_renders_and_paginates_without_external_network():
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        second = request.url.path == "/page2"
        html = """<!doctype html><html><body><main id="records"></main><script>
            document.querySelector('#records').innerHTML =
            '<article class="business" data-id="ID">' +
            '<h2 class="name">SYNTHETIC JS Business</h2></article>NEXT';
            </script></body></html>""".replace("ID", "two" if second else "one").replace(
            "NEXT", "" if second else '<a rel="next" href="/page2">Next</a>'
        )
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    http = HttpCollector(
        RequestPolicy(interval=0), client=httpx.Client(transport=httpx.MockTransport(handle))
    )
    config = DirectoryConfig(
        name="js-test",
        start_url="https://fixture.example/page1",
        policy_url="https://fixture.example/terms",
        permission_note="Owned local test fixture",
        reviewed_on="2026-10-18",
        automated_collection_permitted=True,
    )
    records = list(DirectorySource(http, config, javascript=True).collect(10))
    assert [record.source_record_id for record in records] == ["one", "two"]
    assert paths == ["/robots.txt", "/page1", "/page2"]
