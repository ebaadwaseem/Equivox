"""Executor (role 4, not staffed yet). Basic version so the loop works end to end.
Code builds URLs; the model never writes them."""
import re
from urllib.parse import quote_plus

from playwright.async_api import BrowserContext, Page

from shared.protocol import Decision

_HIGH_CONTRAST_STYLE_ID = "vb-high-contrast"


async def execute(page: Page, d: Decision) -> str:
    sel = f'[data-vb-id="{d.target_id}"]' if d.target_id else None
    match d.intent:
        case "click" if sel:
            await page.locator(sel).first.click(timeout=3000)
        case "type" if sel:
            await page.locator(sel).first.fill(d.text or "", timeout=3000)
        case "select" if sel:
            await page.locator(sel).first.select_option(label=d.text or "", timeout=3000)
        case "scroll":
            return await scroll(page, d.scroll_amount, d.scroll_to)
        case "back":
            await page.go_back()
        case "forward":
            await page.go_forward()
        case "navigate" if d.text:
            url = d.text if d.text.startswith("http") else f"https://{d.text}"
            await page.goto(url)
        case "search" if d.text:
            # the plain duckduckgo.com/?q= form doesn't render results without JS; the
            # static /html/ endpoint does and is faster to load.
            await page.goto(f"https://duckduckgo.com/html/?q={quote_plus(d.text)}")
        case "find" if d.text:
            return await find_text(page, d.text)
        case "accessibility" if d.text:
            return await apply_accessibility(page, d.text)
        case _:
            return f"skipped ({d.intent}, target={d.target_id})"
    return f"did {d.intent} {d.target_id or d.text or ''}".strip()


_SCROLL_JS = """
([fraction, scrollTo, behavior]) => {
  const HOST_ID = 'vox-host';

  function findContainer() {
    const cx = innerWidth / 2, cy = innerHeight / 2;
    const stack = document.elementsFromPoint(cx, cy);
    let start = null;
    for (const el of stack) {
      if (el.id === HOST_ID || (el.closest && el.closest('#' + HOST_ID))) continue;
      start = el;
      break;
    }
    if (start) {
      let el = start;
      while (el && el !== document.documentElement) {
        const style = getComputedStyle(el);
        if (el.scrollHeight > el.clientHeight + 10 && (style.overflowY === 'auto' || style.overflowY === 'scroll')) {
          return el;
        }
        el = el.parentElement;
      }
    }
    return document.scrollingElement || document.documentElement;
  }

  const container = findContainer();
  const maxScroll = Math.max(0, container.scrollHeight - container.clientHeight);
  const before = container.scrollTop;

  let target, direction;
  if (scrollTo === 'top') { target = 0; direction = 'up'; }
  else if (scrollTo === 'bottom') { target = maxScroll; direction = 'down'; }
  else {
    direction = fraction < 0 ? 'up' : 'down';
    target = before + fraction * container.clientHeight;
  }
  target = Math.max(0, Math.min(maxScroll, target));

  if (Math.abs(target - before) < 1) {
    // nothing will move -- report it instantly instead of waiting on a scroll that
    // isn't going to happen
    return { moved: false, direction, atTop: before <= 1, atBottom: before >= maxScroll - 1 };
  }

  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      const after = container.scrollTop;
      resolve({
        moved: Math.abs(after - before) > 1,
        direction,
        atTop: after <= 1,
        atBottom: after >= maxScroll - 1,
      });
    };
    window.addEventListener('scrollend', finish, { once: true });
    if (container !== document.scrollingElement && container.addEventListener) {
      container.addEventListener('scrollend', finish, { once: true });
    }
    // fallback if scrollend never fires at all -- generous on purpose: a short
    // ~500ms single-viewport scroll finishes (and fires scrollend) well before
    // this anyway, but a large jump (e.g. "to the bottom" on a long page) can take
    // close to a second to animate smoothly, and firing early would report the
    // scroll's MID-ANIMATION position as final.
    setTimeout(finish, 2500);
    if (typeof container.scrollTo === 'function') {
      container.scrollTo({ top: target, behavior });
    } else {
      container.scrollTop = target;
      finish();
    }
  });
}
"""


async def scroll(page: Page, fraction: float, scroll_to: str | None) -> str:
    """Finds the right scroll container (an inner scrollable element under the
    viewport centre if one exists -- Gmail, Docs, Discord and similar app layouts
    scroll an inner div, not the window -- else the document), scrolls it smoothly
    by a fraction of ITS OWN visible height (or straight to the top/bottom), and
    waits for the scroll to actually finish before returning, so perception's next
    snapshot sees the page in its post-scroll state.
    """
    try:
        reduced_motion = await page.evaluate("() => matchMedia('(prefers-reduced-motion: reduce)').matches")
    except Exception:
        reduced_motion = False
    behavior = "instant" if reduced_motion else "smooth"
    result = await page.evaluate(_SCROLL_JS, [fraction, scroll_to, behavior])

    if scroll_to == "top":
        return "Top of page"
    if scroll_to == "bottom":
        return "Bottom of page"
    if not result["moved"]:
        return "Already at the top" if result["direction"] == "up" else "Already at the bottom"
    return f"Scrolling {result['direction']}"


async def highlight(page: Page, target_id: str) -> None:
    """Flash an outline on the target so the audience sees what's about to happen."""
    await page.evaluate(
        """id => { const el = document.querySelector(`[data-vb-id="${id}"]`);
                  if (el) { el.style.outline = '3px solid #ff3b30';
                            setTimeout(() => el.style.outline = '', 800); } }""",
        target_id,
    )


async def find_text(page: Page, text: str) -> str:
    """Voice Ctrl+F: scroll to and highlight the first match of `text` anywhere on the
    page -- including plain text that was never one of observer.js's interactive elements.
    """
    pattern = re.compile(re.escape(text), re.I)
    try:
        count = await page.get_by_text(pattern).count()
        if count == 0:
            return f'no match for "{text}" on this page'
        locator = page.get_by_text(pattern).first
        await locator.scroll_into_view_if_needed(timeout=3000)
        await locator.evaluate(
            """el => { el.style.outline = '3px solid #ff3b30'; el.style.backgroundColor = '#fff3cd';
                      setTimeout(() => { el.style.outline = ''; el.style.backgroundColor = ''; }, 1500); }"""
        )
        return f'found "{text}" ({count} match{"es" if count != 1 else ""})'
    except Exception as e:
        return f'could not find "{text}": {e}'


async def apply_accessibility(page: Page, action: str) -> str:
    """zoom_in/zoom_out/reset adjust document zoom; high_contrast_on/off toggles an
    injected invert-filter stylesheet. All via plain CSS/JS, no extra dependencies."""
    if action == "zoom_in":
        await page.evaluate(
            "() => { const z = parseFloat(document.documentElement.style.zoom || '1'); "
            "document.documentElement.style.zoom = String(Math.min(z + 0.2, 3)); }"
        )
    elif action == "zoom_out":
        await page.evaluate(
            "() => { const z = parseFloat(document.documentElement.style.zoom || '1'); "
            "document.documentElement.style.zoom = String(Math.max(z - 0.2, 0.5)); }"
        )
    elif action == "reset":
        await page.evaluate(
            f"""() => {{ document.documentElement.style.zoom = '1';
                       const s = document.getElementById('{_HIGH_CONTRAST_STYLE_ID}'); if (s) s.remove(); }}"""
        )
    elif action == "high_contrast_on":
        await page.evaluate(
            f"""() => {{
                if (document.getElementById('{_HIGH_CONTRAST_STYLE_ID}')) return;
                const style = document.createElement('style');
                style.id = '{_HIGH_CONTRAST_STYLE_ID}';
                style.textContent = `html {{ filter: invert(1) hue-rotate(180deg) !important; background: #fff !important; }}
                    img, video, picture, canvas, svg {{ filter: invert(1) hue-rotate(180deg) !important; }}`;
                document.head.appendChild(style);
            }}"""
        )
    elif action == "high_contrast_off":
        await page.evaluate(
            f"() => {{ const s = document.getElementById('{_HIGH_CONTRAST_STYLE_ID}'); if (s) s.remove(); }}"
        )
    else:
        return f"unknown accessibility action: {action}"
    return f"applied {action}"


async def new_tab(ctx: BrowserContext, url: str | None = None) -> Page:
    page = await ctx.new_page()
    if url:
        full_url = url if url.startswith("http") else f"https://{url}"
        await page.goto(full_url)
    else:
        await page.goto("about:blank")
    return page


async def close_tab(ctx: BrowserContext, page: Page) -> Page:
    """Closes `page`; returns whichever tab should become active next."""
    pages = ctx.pages
    idx = pages.index(page)
    await page.close()
    remaining = ctx.pages
    if not remaining:
        raise RuntimeError("no tabs left")
    return remaining[min(idx, len(remaining) - 1)]


async def switch_tab(ctx: BrowserContext, current: Page, query: str) -> Page:
    """"next"/"previous", or a substring to match against open tabs' titles/URLs."""
    pages = ctx.pages
    if len(pages) <= 1:
        return current
    idx = pages.index(current)
    q = (query or "next").lower()
    if q == "next":
        return pages[(idx + 1) % len(pages)]
    if q in ("previous", "prev"):
        return pages[(idx - 1) % len(pages)]
    for p in pages:
        if q in (p.url or "").lower():
            return p
        try:
            if q in (await p.title() or "").lower():
                return p
        except Exception:
            continue
    return current  # no match, stay put
