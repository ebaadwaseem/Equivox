"""Executor (role 4, not staffed yet). Basic version so the loop works end to end.
Code builds URLs; the model never writes them."""
from urllib.parse import quote_plus

from playwright.async_api import Page

from shared.protocol import Decision


async def execute(page: Page, d: Decision) -> str:
    sel = f'[data-vb-id="{d.target_id}"]' if d.target_id else None
    match d.intent:
        case "click" if sel:
            await page.locator(sel).first.click(timeout=3000)
        case "type" if sel:
            await page.locator(sel).first.fill(d.text or "", timeout=3000)
        case "scroll":
            await page.mouse.wheel(0, d.scroll_amount or 600)
        case "back":
            await page.go_back()
        case "forward":
            await page.go_forward()
        case "navigate" if d.text:
            url = d.text if d.text.startswith("http") else f"https://{d.text}"
            await page.goto(url)
        case "search" if d.text:
            await page.goto(f"https://duckduckgo.com/?q={quote_plus(d.text)}")
        case _:
            return f"skipped ({d.intent}, target={d.target_id})"
    return f"did {d.intent} {d.target_id or d.text or ''}".strip()


async def highlight(page: Page, target_id: str) -> None:
    """Flash an outline on the target so the audience sees what's about to happen."""
    await page.evaluate(
        """id => { const el = document.querySelector(`[data-vb-id="${id}"]`);
                  if (el) { el.style.outline = '3px solid #ff3b30';
                            setTimeout(() => el.style.outline = '', 800); } }""",
        target_id,
    )
