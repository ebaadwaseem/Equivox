"""Executor (role 4, not staffed yet). Basic version so the loop works end to end.
Code builds URLs; the model never writes them."""
import asyncio
import os
import re
from urllib.parse import quote_plus, urlsplit

from playwright.async_api import BrowserContext, Page

from shared.protocol import Decision

_HIGH_CONTRAST_STYLE_ID = "vb-high-contrast"


def navigation_url(text: str) -> str:
    """Validate before touching the browser; search queries never become bare URLs."""
    text = text.strip()
    if not text or any(c.isspace() for c in text):
        raise ValueError("Expected a web address; use search for a query")
    url = text if "://" in text else f"https://{text}"
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("Expected an HTTP or HTTPS address")
    if host != "localhost" and "." not in host and ":" not in host:
        raise ValueError("Expected a domain name")
    # Accessing port validates malformed ports as well.
    parsed.port
    return url


def target_locator(page, target_id: str):
    """Frame-qualified IDs select the same observed frame; locators pierce open shadow roots."""
    scope = page
    if target_id.startswith("f") and ":" in target_id:
        frame, target_id = target_id.split(":", 1)
        scope = page.frames[int(frame[1:])]
    if not re.fullmatch(r"e\d+", target_id):
        raise ValueError("Invalid observed target ID")
    return scope.locator(f'[data-vb-id="{target_id}"]').first


async def select_value(page, locator, text: str) -> str:
    tag = await locator.evaluate("el => el.tagName.toLowerCase()")
    if await locator.get_attribute("role") == "option":
        await locator.click(timeout=3000)
        return f"selected {text}"
    if tag == "select":
        options = await locator.evaluate("el => Array.from(el.options).filter(o => !o.disabled && !o.parentElement.disabled).map(o => ({label:o.label, value:o.value, index:o.index}))")
        key = text.strip().casefold()
        matches = [o for o in options if key in {o["label"].strip().casefold(), o["value"].casefold()}]
        if not matches:
            matches = [o for o in options if key and key in o["label"].casefold()]
        if len(matches) != 1:
            return f'no match or ambiguous option "{text}"'
        await locator.select_option(index=matches[0]["index"], timeout=3000)
        return f'selected {matches[0]["label"]}'
    # ARIA combobox/listbox controls usually render their options in a portal.
    if await locator.get_attribute("aria-expanded") != "true":
        await locator.click(timeout=3000)
    option = page.get_by_role("option", name=re.compile(rf"^\s*{re.escape(text)}\s*$", re.I))
    try:
        await option.first.wait_for(state="visible", timeout=2000)
    except Exception:
        return f'no match or ambiguous option "{text}"'
    if await option.count() != 1:
        return f'no match or ambiguous option "{text}"'
    await option.click(timeout=3000)
    return f"selected {text}"


async def execute(page: Page, d: Decision) -> str:
    sel = target_locator(page, d.target_id) if d.target_id else None
    match d.intent:
        case "click" if sel:
            if await sel.evaluate("el => el.tagName === 'SELECT'"):
                # Native OS popups cannot be inspected as DOM. Present their real
                # enabled options in Vox instead, using select_option to choose.
                await sel.focus()
                return "opened native dropdown"
            await sel.click(timeout=3000)
        case "type" if sel:
            # Some widgets put the observed role on a wrapper around the input.
            if not await sel.evaluate("el => el.matches('input,textarea') || el.isContentEditable"):
                sel = sel.locator('input,textarea,[contenteditable="true"]').first
            await sel.fill(d.text or "", timeout=3000)
        case "select" if sel:
            scope = page.frames[int(d.target_id.split(":", 1)[0][1:])] if d.target_id.startswith("f") else page
            return await select_value(scope, sel, d.text or "")
        case "reload":
            await page.reload(wait_until="domcontentloaded")
        case "clear":
            control = sel or await focused_editable(page)
            if control is None:
                return "could not find a focused text field; click a field first"
            await control.fill("", timeout=3000)
            return "Cleared the text field"
        case "key" if d.text:
            allowed = {"Enter", "Backspace", "Delete", "Tab", "Escape", "Space", "ArrowUp", "ArrowDown",
                       "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", "ControlOrMeta+A", "ControlOrMeta+C", "ControlOrMeta+V"}
            if d.text not in allowed and not re.fullmatch(r"[a-z0-9]", d.text):
                return "skipped unsupported key"
            if d.text in {"Backspace", "Delete", "ControlOrMeta+A", "ControlOrMeta+C", "ControlOrMeta+V"} or len(d.text) == 1:
                control = sel or await focused_editable(page)
                if control is None:
                    return "could not find a focused text field; click a field first"
                await control.focus()
            for _ in range(max(1, min(d.repeat_count, 20))):
                await page.keyboard.press(d.text)
            return f"Pressed {d.text}"
        case "route" if d.text:
            await page.goto(f"https://www.google.com/maps/dir/?api=1&destination={quote_plus(d.text)}", wait_until="domcontentloaded")
            return f"Directions to {d.text}"
        case "map_move" if d.text:
            return await pan_map(page, d.text)
        case "scroll":
            return await scroll(page, d.scroll_amount, d.scroll_to)
        case "back":
            await page.go_back(wait_until="domcontentloaded")
        case "forward":
            await page.go_forward(wait_until="domcontentloaded")
        case "navigate" if d.text:
            await page.goto(navigation_url(d.text), wait_until="domcontentloaded")
        case "search" if d.text:
            # the plain duckduckgo.com/?q= form doesn't render results without JS; the
            # static /html/ endpoint does and is faster to load.
            if is_map_url(page.url):
                url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(d.text)}"
            else:
                url = f"https://duckduckgo.com/html/?q={quote_plus(d.text)}"
            await page.goto(url, wait_until="domcontentloaded")
        case "find" if d.text:
            return await find_text(page, d.text)
        case "accessibility" if d.text:
            return await apply_accessibility(page, d.text)
        case _:
            return f"skipped ({d.intent}, target={d.target_id})"
    return f"did {d.intent} {d.target_id or d.text or ''}".strip()


def is_map_url(url):
    parsed = urlsplit(url)
    return bool(re.search(r"(^|\.)google\.(?:com|ca|co\.uk)$", parsed.hostname or "")) and parsed.path.startswith("/maps")


async def click_visual(page, point):
    x, y = point
    clear = await page.evaluate("""([x,y]) => {
        const el=document.elementFromPoint(x,y);
        return x>0 && y>0 && x<innerWidth && y<innerHeight && el && el.id!=='vox-host';
    }""", [x,y])
    if not clear:
        return "could not click that target because it is covered or no longer visible"
    await page.mouse.click(x,y)
    return "Clicked the selected visible target"


async def focused_editable(page):
    for frame in page.frames:
        target = await frame.evaluate("""() => {
            let el = document.activeElement;
            while (el?.shadowRoot?.activeElement) el = el.shadowRoot.activeElement;
            if (!el || !(el.matches('input:not([type=hidden]),textarea') || el.isContentEditable) || el.readOnly || el.disabled) return null;
            if (el.getRootNode().host?.id === 'vox-host') return null;
            window.__vbNext = window.__vbNext || 1;
            el.dataset.vbId ||= 'e' + String(window.__vbNext++).padStart(2,'0');
            return el.dataset.vbId;
        }""")
        if target:
            return frame.locator(f'[data-vb-id="{target}"]').first
    return None


async def pan_map(page, direction):
    # Google Maps exposes a focusable map region even though its content is canvas.
    region = page.get_by_role("region", name=re.compile(r"map", re.I)).first
    if not await region.count():
        region = page.locator('[aria-label="Map"], [role="application"][aria-label*="map" i]').first
    if not await region.count():
        return "could not find a map region"
    keys = {"left": "ArrowLeft", "west": "ArrowLeft", "right": "ArrowRight", "east": "ArrowRight",
            "up": "ArrowUp", "north": "ArrowUp", "down": "ArrowDown", "south": "ArrowDown"}
    if direction not in keys:
        return "skipped unknown map direction"
    # Focus the map without clicking an arbitrary place marker.
    await region.evaluate("el => { if (!el.hasAttribute('tabindex')) el.tabIndex=0; el.focus(); }")
    await page.keyboard.press(keys[direction])
    return f"Moved the map {direction}"


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
    await target_locator(page, target_id).evaluate(
        """el => { const old = el.style.outline; el.style.outline = '3px solid #ff3b30';
                  setTimeout(() => el.style.outline = old, 800); }""", timeout=1000,
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
    full_url = navigation_url(url or os.getenv("VB_NEW_TAB_URL", "https://www.google.com/"))
    page = await ctx.new_page()
    try:
        await page.goto(full_url, wait_until="domcontentloaded")
        await page.bring_to_front()
    except Exception:
        await page.close()
        raise
    return page


async def close_tab(ctx: BrowserContext, page: Page) -> Page:
    """Closes `page`; returns whichever tab should become active next."""
    pages = [p for p in ctx.pages if not p.is_closed()]
    idx = pages.index(page)
    # Keep one usable tab when the user closes the last one.
    if len(pages) == 1:
        replacement = await new_tab(ctx)
        await page.close()
        return replacement
    await page.close()
    remaining = ctx.pages
    if not remaining:
        raise RuntimeError("no tabs left")
    result = remaining[min(idx, len(remaining) - 1)]
    await result.bring_to_front()
    return result


async def switch_tab(ctx: BrowserContext, current: Page, query: str) -> Page:
    """"next"/"previous", or a substring to match against open tabs' titles/URLs."""
    pages = [p for p in ctx.pages if not p.is_closed()]
    if not pages:
        raise ValueError("There are no open tabs")
    idx = pages.index(current) if current in pages else 0
    q = (query or "next").strip().lower()
    result = None
    if q == "next":
        result = pages[(idx + 1) % len(pages)]
    elif q in ("previous", "prev"):
        result = pages[(idx - 1) % len(pages)]
    elif q == "first":
        result = pages[0]
    elif q.isdigit():
        if not 1 <= int(q) <= len(pages):
            raise ValueError(f"There are only {len(pages)} open tabs")
        result = pages[int(q) - 1]
    else:
        titles = await asyncio.gather(*(p.title() for p in pages), return_exceptions=True)
        exact = [p for p, title in zip(pages, titles) if q in {str(title).lower(), (urlsplit(p.url).hostname or '').removeprefix('www.').lower()}]
        matches = exact or [p for p, title in zip(pages, titles) if q in str(title).lower() or q in p.url.lower()]
        other = [p for p in matches if p is not current]
        matches = other or matches
        if len(matches) != 1:
            raise ValueError("More than one tab matches; say switch to tab 1 or tab 2" if matches else f"No tab matches {query}")
        result = matches[0]
    await result.bring_to_front()
    return result
