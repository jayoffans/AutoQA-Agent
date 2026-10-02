"""Public-target policy with DNS pinning at connection time, including browser resource requests."""
import asyncio
import ipaddress
import socket
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


async def fetch_limited(client, method, url, *, max_bytes=2_000_000, **kwargs):
    async with client.stream(method, url, **kwargs) as response:
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise ResponseTooLargeError("Remote response exceeds the size limit")
            chunks.append(chunk)
        return httpx.Response(response.status_code, headers=response.headers,
                              content=b"".join(chunks), request=response.request)
