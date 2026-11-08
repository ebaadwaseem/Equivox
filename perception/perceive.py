"""PERSON 2: perception & parsing.

Goal: turn the live page into a compact PageState the model can choose from.
Ideas: try the accessibility tree (page.accessibility / CDP Accessibility.getFullAXTree),
shortlist <=24 elements (viewport + words matching the transcript), add a low-res screenshot.
"""
import base64
from pathlib import Path

from playwright.async_api import Page

from shared.protocol import Element, PageState

OBSERVER_JS = (Path(__file__).parent / "observer.js").read_text()


async def get_page_state(page: Page, with_screenshot: bool = False) -> PageState:
    raw = await page.evaluate(OBSERVER_JS)
    elements = [Element(**e) for e in raw]
    shot = None
    if with_screenshot:
        png = await page.screenshot(type="jpeg", quality=40, scale="css")
        shot = base64.b64encode(png).decode()
    return PageState(url=page.url, title=await page.title(), elements=elements, screenshot_b64=shot)


def shortlist(state: PageState, transcript: str, limit: int = 24) -> list[Element]:
    """Elements whose names share words with what was said come first, then the ones in the viewport."""
    words = {w for w in transcript.lower().split() if len(w) > 2}

    def score(e: Element) -> tuple:
        overlap = len(words & set(e.name.lower().split()))
        return (-overlap, not e.in_viewport)

    return sorted(state.elements, key=score)[:limit]


def to_prompt_text(elements: list[Element]) -> str:
    """One line per element: what the model actually sees."""
    return "\n".join(f'{e.id} {e.role} "{e.name}" @({e.x},{e.y})' for e in elements)
