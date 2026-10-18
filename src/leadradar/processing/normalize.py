import hashlib
import ipaddress
import json
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from leadradar.models import Business, RawBusiness


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = unicodedata.normalize("NFKC", value)
    value = "".join(c for c in value if unicodedata.category(c) != "Cf")
    return " ".join(value.split()) or None


def normalize_website(value: str | None) -> tuple[str | None, str | None]:
    value = clean_text(value)
    if not value:
        return None, None
    if any(c.isspace() for c in value) or "\\" in value:
        raise ValueError("website contains whitespace or backslashes")
    if not re.match(r"^[a-zA-Z][a-zA-Z\d+.-]*:", value):
        value = "https:" + value if value.startswith("//") else "https://" + value
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("website must be an HTTP(S) URL with a hostname")
    if parts.username is not None or parts.password is not None:
        raise ValueError("website credentials are not accepted")
    host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if (
            "." not in host
            or len(host) > 253
            or any(
                not re.fullmatch(r"[a-z\d](?:[a-z\d-]{0,61}[a-z\d])?", label)
                for label in host.split(".")
            )
        ):
            raise ValueError("invalid website hostname") from None
    else:
        raise ValueError("IP addresses are not company domains")
    port = parts.port  # Invalid/out-of-range ports raise ValueError.
    authority = host
    if port and (parts.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
        authority += f":{port}"
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
        ]
    )
    website = urlunsplit((parts.scheme.lower(), authority, parts.path or "/", query, ""))
    return website, host.removeprefix("www.")


def normalize(raw: RawBusiness) -> Business:
    original = raw.model_dump()
    values = {k: clean_text(v) if isinstance(v, str) else v for k, v in original.items()}
    warnings = []
    try:
        website, domain = normalize_website(raw.website)
    except (ValueError, UnicodeError) as exc:
        website, domain = None, None
        warnings.append(f"invalid_website: {exc}")
    values["website"] = website
    values["domain"] = domain
    fields = (
        "business_name",
        "website",
        "category",
        "city",
        "region",
        "country",
        "phone",
        "address",
        "location_key",
    )
    provenance = {
        k: {
            "source": raw.source,
            "record_id": raw.source_record_id,
            "transformation": "normalized" if original[k] != values[k] else "source",
        }
        for k in fields
        if original.get(k) is not None
    }
    if domain:
        provenance["domain"] = {"from": "website", "transformation": "derived"}
    # Domain alone, company name alone, or a city alone must never merge businesses.
    location = values.get("location_key")
    if not location and values.get("address") and values.get("city") and values.get("country"):
        location = "|".join(values.get(k) or "" for k in ("address", "city", "region", "country"))
    if domain and location:
        signature = [
            raw.source_kind,
            "domain-name-location",
            domain,
            (values["business_name"] or "").casefold(),
            location.casefold(),
        ]
    else:
        signature = [raw.source_kind, "source-id", raw.source, raw.source_record_id]
    identity = hashlib.sha256(json.dumps(signature, ensure_ascii=False).encode()).hexdigest()
    partial = not all(values.get(k) for k in ("website", "category", "city", "country"))
    return Business(
        **values,
        identity_key=identity,
        original_values=original,
        field_provenance=provenance,
        warnings=warnings,
        validation_status="warning" if warnings else "partial" if partial else "valid",
    )
