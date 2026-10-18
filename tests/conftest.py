import httpx
import pytest

from leadradar.models import RawBusiness
from leadradar.storage.database import Database


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Unit tests cannot use the network; provide httpx.MockTransport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


@pytest.fixture
def raw():
    return RawBusiness(
        business_name="  Demo   Café  ",
        website="HTTPS://WWW.Example.COM/?utm_source=x",
        category="cafe",
        city="Demo City",
        region="Demo Region",
        country="XX",
        address="1 Example Street",
        source="test",
        source_record_id="1",
        source_url="https://directory.example/1",
        source_kind="fixture",
    )


@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "test.db") as database:
        yield database
