import logging
import time
from contextlib import suppress
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from leadradar.config import USER_AGENT, RequestPolicy

log = logging.getLogger(__name__)


class CollectionError(RuntimeError):
    """An actionable, safe-to-display source failure."""


class HttpCollector:
    def __init__(
        self,
        policy: RequestPolicy,
        *,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.policy = policy
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=policy.timeout,
            follow_redirects=False,
        )
        self.owns_client = client is None
        self.sleep = sleep
        self.clock = clock
        self.last_request: float | None = None
        self.requests = 0
        self.warnings: list[str] = []

    def close(self):
        if self.owns_client:
            self.client.close()

    def wait_slot(self):
        if self.requests >= self.policy.max_requests:
            raise CollectionError(f"Request budget exhausted ({self.policy.max_requests})")
        if self.last_request is not None:
            self.sleep(max(0, self.policy.interval - (self.clock() - self.last_request)))
        self.last_request = self.clock()
        self.requests += 1

    def request(self, method: str, url: str, *, allow_missing=False, **kwargs) -> httpx.Response:
        for attempt in range(self.policy.max_attempts):
            self.wait_slot()
            retry_after = 0.0
            try:
                with self.client.stream(
                    method, url, timeout=self.policy.timeout, follow_redirects=False, **kwargs
                ) as streamed:
                    content = bytearray()
                    for chunk in streamed.iter_bytes():
                        content.extend(chunk)
                        if len(content) > self.policy.max_bytes:
                            raise CollectionError("Response exceeds configured byte limit")
                    # iter_bytes has already decompressed the body. Retaining Content-Encoding
                    # would ask httpx.Response to decompress it a second time.
                    decoded_headers = {
                        k: v
                        for k, v in streamed.headers.items()
                        if k.lower()
                        not in {"content-encoding", "content-length", "transfer-encoding"}
                    }
                    response = httpx.Response(
                        streamed.status_code,
                        headers=decoded_headers,
                        content=bytes(content),
                        request=streamed.request,
                    )
                code = response.status_code
                if code in {401, 403}:
                    raise CollectionError(f"HTTP {code}: access denied; collection stopped")
                if 300 <= code < 400:
                    raise CollectionError(
                        "Redirect refused; review and configure the final source URL"
                    )
                if code == 404 and allow_missing:
                    return response
                if code not in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                    return response
                reason = f"HTTP {code}"
                header = response.headers.get("retry-after")
                if header:
                    try:
                        retry_after = max(0.0, float(header))
                    except ValueError:
                        with suppress(ValueError, TypeError):
                            retry_after = max(
                                0.0,
                                (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds(),
                            )
                if retry_after > 60:
                    raise CollectionError(
                        f"Source requests a {retry_after:.0f}s cooldown; retry later"
                    )
            except (httpx.TransportError, httpx.DecodingError) as exc:
                reason = type(exc).__name__
            except httpx.HTTPStatusError as exc:
                raise CollectionError(
                    f"HTTP {exc.response.status_code}: source unavailable"
                ) from exc
            if attempt + 1 == self.policy.max_attempts:
                raise CollectionError(f"{reason}; exhausted {self.policy.max_attempts} attempts")
            delay = max(retry_after, min(60, self.policy.backoff * 2**attempt))
            warning = f"Recovered/retrying transient {reason} (attempt {attempt + 1})"
            self.warnings.append(warning)
            log.warning(
                "request_retry",
                extra={"details": {"attempt": attempt + 1, "reason": reason, "delay": delay}},
            )
            self.sleep(delay)
        raise AssertionError("unreachable")
