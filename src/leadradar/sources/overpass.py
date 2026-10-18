import math
import re
from collections.abc import Iterator

from leadradar.collectors.http import CollectionError, HttpCollector
from leadradar.config import OSM_ATTRIBUTION, OSM_LICENSE, OVERPASS_URL
from leadradar.models import RawBusiness


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    try:
        south, west, north, east = map(float, value.split(","))
    except ValueError as exc:
        raise ValueError("bbox must be south,west,north,east") from exc
    if not all(math.isfinite(v) for v in (south, west, north, east)) or not (
        -90 <= south < north <= 90 and -180 <= west < east <= 180
    ):
        raise ValueError("bbox coordinates are out of range or inverted")
    if north - south > 0.5 or east - west > 0.5:
        raise ValueError("Use a local bbox no larger than 0.5 degrees per side")
    return south, west, north, east


class OverpassSource:
    name = "overpass"

    def __init__(self, http: HttpCollector, bbox: str, tag: str = "amenity=restaurant"):
        self.http = http
        self.bbox = parse_bbox(bbox)
        if not re.fullmatch(r"(?:amenity|shop|craft|office|tourism)=[a-z0-9_]+", tag):
            raise ValueError("tag must be one business key=value, e.g. amenity=restaurant")
        self.key, self.value = tag.split("=")

    def query(self, limit: int) -> str:
        bounds = ",".join(str(v) for v in self.bbox)
        return (
            f"[out:json][timeout:25][maxsize:33554432];"
            f'nwr["{self.key}"="{self.value}"]["name"]({bounds});out tags center {limit};'
        )

    def collect(self, limit: int) -> Iterator[RawBusiness]:
        response = self.http.request("POST", OVERPASS_URL, data={"data": self.query(limit)})
        try:
            payload = response.json()
        except ValueError as exc:
            raise CollectionError("Overpass returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
            raise CollectionError("Overpass response has no elements list")
        if payload.get("remark"):
            # Overpass may return HTTP 200 with a timeout remark and incomplete data.
            raise CollectionError(
                f"Overpass rejected/incompletely executed query: {payload['remark']}"
            )
        for element in payload["elements"][:limit]:
            if not isinstance(element, dict):
                raise CollectionError("Overpass element is not an object")
            tags = element.get("tags", {})
            kind, record_id = element.get("type"), element.get("id")
            if kind not in {"node", "way", "relation"} or not isinstance(record_id, int):
                raise CollectionError("Overpass element has an invalid identity")
            if not isinstance(tags, dict) or not all(isinstance(v, str) for v in tags.values()):
                raise CollectionError("Overpass element has invalid tags")
            center = element.get("center", element)
            lat, lon = center.get("lat"), center.get("lon")
            location = (
                f"{lat:.5f},{lon:.5f}"
                if isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                else None
            )
            address = " ".join(
                filter(None, [tags.get("addr:housenumber"), tags.get("addr:street")])
            )
            yield RawBusiness(
                business_name=tags.get("name"),
                website=tags.get("website") or tags.get("contact:website"),
                category=tags.get(self.key),
                city=tags.get("addr:city"),
                region=tags.get("addr:state") or tags.get("addr:province"),
                country=tags.get("addr:country"),
                phone=tags.get("contact:phone") or tags.get("phone"),
                address=address or None,
                location_key=location,
                source=self.name,
                source_record_id=f"{kind}/{record_id}",
                source_url=f"https://www.openstreetmap.org/{kind}/{record_id}",
                attribution=OSM_ATTRIBUTION,
                license_url=OSM_LICENSE,
            )
