import pytest
from pydantic import ValidationError

from leadradar.processing.normalize import clean_text, normalize, normalize_website


@pytest.mark.parametrize(
    ("input", "website", "domain"),
    [
        (" Example.COM ", "https://example.com/", "example.com"),
        (
            "https://www.EXAMPLE.com:443/a?utm_source=x&q=y#top",
            "https://www.example.com/a?q=y",
            "example.com",
        ),
        ("http://example.com:8080/shop/", "http://example.com:8080/shop/", "example.com"),
        ("https://münchen.example", "https://xn--mnchen-3ya.example/", "xn--mnchen-3ya.example"),
        ("//www.example.com", "https://www.example.com/", "example.com"),
        (None, None, None),
    ],
)
def test_website_normalization(input, website, domain):
    assert normalize_website(input) == (website, domain)


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "mailto:a@example.com",
        "https://",
        "https://user:pass@example.com",
        "https://bad host.com",
        "https://-bad.example",
        "https://example.com:99999",
        "http://127.0.0.1",
        "https://example.com\\evil",
        "localhost",
    ],
)
def test_invalid_website_rejected(value):
    with pytest.raises(ValueError):
        normalize_website(value)


def test_missing_and_unicode_text():
    assert clean_text(" \nＡcme\u200b   Café \t") == "Acme Café"
    assert clean_text(" \t") is None
    assert clean_text(None) is None


def test_provenance_and_normalized_values(raw):
    business = normalize(raw)
    assert business.business_name == "Demo Café"
    assert business.original_values["business_name"] == raw.business_name
    assert business.field_provenance["business_name"]["transformation"] == "normalized"
    assert business.field_provenance["domain"] == {"from": "website", "transformation": "derived"}
    assert business.validation_status == "valid"


def test_invalid_url_is_warning_and_original_retained(raw):
    business = normalize(raw.model_copy(update={"website": "javascript:alert(1)"}))
    assert business.website is None and business.domain is None
    assert business.validation_status == "warning"
    assert business.original_values["website"] == "javascript:alert(1)"


def test_missing_fields_are_not_invented(raw):
    business = normalize(raw.model_copy(update={"website": None, "country": None}))
    assert business.country is None and business.website is None
    assert business.validation_status == "partial"


@pytest.mark.parametrize("name", [None, "", " \t"])
def test_missing_name_is_invalid(raw, name):
    with pytest.raises(ValidationError):
        normalize(raw.model_copy(update={"business_name": name}))


def test_same_domain_name_and_address_match(raw):
    a = normalize(raw)
    b = normalize(
        raw.model_copy(
            update={
                "source": "other",
                "source_record_id": "999",
                "website": "http://example.com/path",
            }
        )
    )
    assert a.identity_key == b.identity_key


@pytest.mark.parametrize(
    "changes",
    [
        {"business_name": "Other Company"},
        {"address": "2 Example Street"},
        {"source_kind": "live"},
        {"country": "YY"},
    ],
)
def test_shared_domains_do_not_merge_unrelated_businesses(raw, changes):
    assert normalize(raw).identity_key != normalize(raw.model_copy(update=changes)).identity_key


def test_missing_precise_location_uses_source_identity(raw):
    a = raw.model_copy(update={"address": None})
    b = a.model_copy(update={"source_record_id": "2"})
    assert normalize(a).identity_key != normalize(b).identity_key
