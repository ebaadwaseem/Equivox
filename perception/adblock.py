"""Ad/tracker host list, shared by two unrelated things that both need to recognize
an ad/tracker request: perception's frame filter (never scan an ad iframe for
elements) and the optional network-level request blocking (VB_BLOCK_ADS, default on).
One file so the list is easy to tweak in one place.
"""
from urllib.parse import urlsplit

AD_TRACKER_HOSTS = (
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "adservice.google.com", "amazon-adsystem.com", "adnxs.com", "taboola.com",
    "outbrain.com", "criteo.com", "pubmatic.com", "rubiconproject.com", "openx.net",
    "moatads.com", "scorecardresearch.com", "quantserve.com",
)
# host -> path prefixes: only block THESE specific paths on an otherwise-legitimate
# domain -- facebook.com itself is not an ad host, but its /plugins/ widgets are.
AD_TRACKER_HOST_PATHS = {
    "facebook.com": ("/plugins",),
}

# requests worth even checking against the host list -- a stylesheet/font/websocket
# from an ad host isn't worth the risk of blocking something load-bearing for a
# feature this narrow; document covers an ad iframe navigating itself.
AD_BLOCK_RESOURCE_TYPES = {"script", "image", "xhr", "fetch", "document"}


def is_ad_or_tracker(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if any(host == h or host.endswith("." + h) for h in AD_TRACKER_HOSTS):
        return True
    for h, paths in AD_TRACKER_HOST_PATHS.items():
        if host == h or host.endswith("." + h):
            path = urlsplit(url).path
            if any(path.startswith(p) for p in paths):
                return True
    return False


async def route_handler(route) -> None:
    """Registered via context.route("**/*", ...) when VB_BLOCK_ADS is on (default).
    Never blocks the top-level page's own document, regardless of host -- only
    subframe documents and the narrow resource types above are even considered."""
    request = route.request
    try:
        if request.resource_type == "document" and request.frame.parent_frame is None:
            await route.continue_()
            return
        if request.resource_type in AD_BLOCK_RESOURCE_TYPES and is_ad_or_tracker(request.url):
            await route.abort()
            return
        await route.continue_()
    except Exception:
        # the route may already be handled, or the page navigated away mid-flight --
        # never let a blocking decision crash the browser's request pipeline.
        pass
