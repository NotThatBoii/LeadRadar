# Source review and adapter configuration

## OpenStreetMap / public Overpass

Reviewed **2026-10-18**, before implementing the adapter:

- https://dev.overpass-api.de/overpass-doc/en/preface/commons.html
- https://www.openstreetmap.org/copyright
- https://opendatacommons.org/licenses/odbl/1-0/

The public instance operator describes small occasional requests, load shedding, per-user slots,
and limits. It warns against using the shared instance as the backend of a general application.
LeadRadar's portfolio demonstration is a bounded, occasional query. It is not permission for a
production multi-user lead service. Do not evade operator limits or treat published safety margins
as a target. Review the policy again before sustained use; arrange a permitted instance/dataset.

The adapter sends one POST to `https://overpass-api.de/api/interpreter`, with at most three attempts,
25-second server runtime, 32 MiB declared server memory, a local bounding box <=0.5 degrees per side,
a selected business key/value, named records only, and bounded output. Client responses are capped
at 10 MB. There is no tile service, Nominatim geocoder, HTML scraping of openstreetmap.org, or
per-listing website fetch. Robots directives concern the HTML adapters; the API uses its operator's
API guidance. HTTP 200 Overpass error remarks are treated as failures, including incomplete results.

`name`, website/contact website, business category, `addr:*`, phone/contact phone, OSM object identity,
and position are mapped. Arbitrary OSM tags, emails, personal names, and social profiles are not
copied. Public business phone fields are included as source-provided; their accuracy/ownership is
not independently verified. There is no guarantee that a record is a legal company or unique entity.

The exporter carries attribution and a direct ODbL URL per OSM record. Software MIT does not
relicense the data. Retain attribution, identify ODbL, and review obligations when publicly sharing
an extracted/adapted database. Runtime leads are deliberately excluded from Git.

## Controlled directory

The packaged pages are owned synthetic test content using reserved `.example` and `.invalid`
domains. They exercise real HTML parsing and pagination with no external network. Five appearances
yield four unique businesses; one repeat and intentionally imperfect fields are included.

## Other public directories

Read the actual site's terms and robots directives first. Supply a JSON file with an accurate review
and explicit permission; setting a boolean does not grant permission. No third-party live HTML
directory has been pre-approved by this project. This example is a template, not a live target:

```json
{
  "name": "approved-directory",
  "start_url": "https://your-permitted-directory.example/businesses/page1",
  "policy_url": "https://your-permitted-directory.example/terms",
  "permission_note": "Replace with the actual permission and scope you reviewed",
  "reviewed_on": "2026-10-18",
  "automated_collection_permitted": true,
  "card_selector": ".business",
  "next_selector": "a[rel='next']",
  "ready_selector": ".business",
  "fields": {
    "business_name": ".name",
    "website": "a.website",
    "category": ".category",
    "city": ".city",
    "region": ".region",
    "country": ".country",
    "phone": ".phone",
    "address": ".address"
  }
}
```

Each card needs a stable `data-id` or `id`. Field selectors are relative to the card; website uses
`href`, other fields use text. Pagination uses `href` and must remain on the same origin. Credentials
in URLs are refused. Do not add selectors for private/person-level data. This adapter supports public
GET pages only and does not use browser cookies, authenticated sessions, forms, or CAPTCHA bypass.

```powershell
.\.venv\Scripts\leadradar.exe collect --source directory --directory-config approved.json --limit 100
.\.venv\Scripts\leadradar.exe collect --source directory-js --directory-config approved.json --limit 100
```

Choose `directory-js` only when the approved page genuinely renders business cards with JavaScript.
Install Chromium first. Browser resource requests obey the same budget/rate/robots checks and
origin restriction. Cross-origin CDN dependencies may prevent rendering; do not bypass restrictions.
