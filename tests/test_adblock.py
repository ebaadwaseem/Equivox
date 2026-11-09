"""perception/adblock.py: the shared ad/tracker host list, used by both the
perception frame filter (skip ad iframes when snapshotting) and the optional
network-level request blocking (VB_BLOCK_ADS)."""
import asyncio

from playwright.async_api import async_playwright

from perception.adblock import is_ad_or_tracker, route_handler


def test_known_ad_hosts_are_flagged():
    assert is_ad_or_tracker("https://doubleclick.net/ad")
    assert is_ad_or_tracker("https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js")
    assert is_ad_or_tracker("https://securepubads.g.doubleclick.net/gampad/ads")
    assert is_ad_or_tracker("https://ib.adnxs.com/bid")
    assert is_ad_or_tracker("https://cdn.taboola.com/libtrc/x.js")


def test_subdomains_of_ad_hosts_are_flagged():
    assert is_ad_or_tracker("https://cdn.ads.amazon-adsystem.com/x")
    assert is_ad_or_tracker("https://widgets.outbrain.com/widget.js")


def test_unrelated_hosts_are_not_flagged():
    assert not is_ad_or_tracker("https://en.wikipedia.org/wiki/Golisopod")
    assert not is_ad_or_tracker("https://news.ycombinator.com/")
    assert not is_ad_or_tracker("https://www.youtube.com/results?search_query=x")


def test_facebook_plugins_path_is_flagged_but_facebook_itself_is_not():
    assert is_ad_or_tracker("https://www.facebook.com/plugins/like.php?href=x")
    assert not is_ad_or_tracker("https://www.facebook.com/someuser")


def test_malformed_url_does_not_raise():
    assert is_ad_or_tracker("not a url at all") is False
    assert is_ad_or_tracker("") is False


# ---- route_handler: the actual context.route("**/*", ...) callback -------------------

AD_SCRIPT_URL = "https://doubleclick.net/test-blocked-script.js"
MAIN_URL = "https://main-site.example.com/"


async def _load_route_handler_test() -> tuple[str, list[str]]:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context()
        await ctx.route("**/*", route_handler)
        await ctx.route(MAIN_URL, lambda r: asyncio.ensure_future(r.fulfill(
            body=f'<script src="{AD_SCRIPT_URL}"></script><h1>Main Page</h1>', content_type="text/html")))
        page = await ctx.new_page()
        failed: list[str] = []
        page.on("requestfailed", lambda req: failed.append(req.url))
        await page.goto(MAIN_URL)
        await page.wait_for_timeout(300)
        title_text = await page.inner_text("h1")
        await browser.close()
        return title_text, failed


def test_route_handler_aborts_ad_script_but_allows_main_document():
    title_text, failed = asyncio.run(_load_route_handler_test())
    assert title_text == "Main Page"  # the main document is never blocked
    assert any(AD_SCRIPT_URL in url for url in failed)  # the ad script was aborted
