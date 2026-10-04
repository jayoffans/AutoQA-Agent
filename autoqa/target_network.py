"""Public-target policy with DNS pinning at connection time, including browser resource requests."""
import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .security import origin


class TargetResolutionError(ValueError):
    """DNS availability failure, distinct from a policy rejection."""


class ResponseTooLargeError(ValueError):
    """The decoded response exceeded its byte budget."""


def resolve_target(url, allow_private=False):
    origin(url)
    parsed = urlsplit(url)
    if parsed.fragment or parsed.hostname.lower() in {"localhost", "metadata.google.internal"}:
        if not allow_private:
            raise ValueError("Private/metadata targets are disabled")
    if parsed.port and parsed.port not in {80, 443, 8080, 8443} and not allow_private:
        raise ValueError("Target port is not permitted")
    try:
        addresses = {str(ipaddress.ip_address(item[4][0])) for item in socket.getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except (OSError, ValueError):
        raise TargetResolutionError("Target DNS resolution failed") from None
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses) and not allow_private:
        raise ValueError("Private, loopback, link-local and metadata IP addresses are disabled")
    # Metadata is forbidden even for explicitly opted-in self-hosted private deployments.
    if "169.254.169.254" in addresses or "fd00:ec2::254" in addresses:
        raise ValueError("Cloud metadata endpoints are forbidden")
    return sorted(addresses)[0]


class PublicPolicy:
    def __init__(self, settings, target=None):
        self.settings, self.target = settings, origin(target) if target else None

    def validate(self, url, resource=False):
        if not resource and self.target and origin(url) != self.target:
            raise ValueError("Navigation must stay on the project target origin")
        resolve_target(url, self.settings.allow_private_targets)

    def is_demo(self, url):
        return False


class PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self, allow_private=False):
        self.allow_private = allow_private
        self.inner = httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        ip = await asyncio.to_thread(resolve_target, str(request.url), self.allow_private)
        hostname = request.url.host
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode()
        pinned = httpx.Request(request.method, request.url.copy_with(host=ip), headers=headers,
                               stream=request.stream, extensions={**request.extensions, "sni_hostname": hostname})
        response = await self.inner.handle_async_request(pinned)
        return response

    async def aclose(self):
        await self.inner.aclose()


@dataclass
class ResponseObservation:
    """Payload-free progress. Never retain a request, response, header map or exception."""
    phase: str = "request_validation"
    exception_category: str | None = None
    response_headers_observed: bool = False
    observed_http_status: int | None = None
    encoding: str | None = None
    decoded_bytes: int = 0


def safe_diagnostics(value):
    if not isinstance(value, dict):
        return None
    phases = {"request_validation", "dns", "request", "response_read", "response_rebuild", "assertion", "completed"}
    categories = {"timeout", "dns", "policy", "connection", "tls", "decoding", "protocol", "read", "write",
                  "response_size", "response_complexity", "transport", "internal"}
    status, size = value.get("observed_http_status"), value.get("decoded_bytes")
    phase, category, encoding = value.get("phase"), value.get("exception_category"), value.get("encoding")
    return {"phase": phase if isinstance(phase, str) and phase in phases else None,
            "exception_category": category if isinstance(category, str) and category in categories else None,
            "response_headers_observed": value.get("response_headers_observed") is True,
            "observed_http_status": status if type(status) is int and 100 <= status <= 599 else None,
            "encoding": encoding if isinstance(encoding, str) and encoding in {"identity", "gzip", "deflate", "br", "zstd", "other"} else None,
            "decoded_bytes": min(max(size, 0), 2_000_001) if type(size) is int else None}


async def fetch_limited(client, method, url, *, max_bytes=2_000_000, observation=None, **kwargs):
    async with client.stream(method, url, **kwargs) as response:
        if observation is not None:
            observation.phase = "response_read"
            observation.response_headers_observed = True
            observation.observed_http_status = response.status_code
            encoding = response.headers.get("content-encoding", "identity").strip().lower()
            observation.encoding = encoding if encoding in {"identity", "gzip", "deflate", "br", "zstd"} else "other"
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if observation is not None:
                observation.decoded_bytes = min(size, max_bytes + 1)
            if size > max_bytes:
                raise ResponseTooLargeError("Remote response exceeds the size limit")
            chunks.append(chunk)
        if observation is not None:
            observation.phase = "response_rebuild"
        # aiter_bytes already decoded the representation; its wire headers are now stale.
        headers = [(key, value) for key, value in response.headers.multi_items()
                   if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
        return httpx.Response(response.status_code, headers=headers,
                              content=b"".join(chunks), request=response.request)
