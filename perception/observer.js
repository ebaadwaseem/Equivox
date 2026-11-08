// PERSON 2: injected into the page. Returns interactable elements with stable ids.
// Improve: better name extraction, iframes, shadow DOM, dedupe, ranking.
() => {
  const SELECTOR = 'a[href], button, input, textarea, select, [role=button], [role=link], ' +
                   '[role=tab], [role=menuitem], [role=checkbox], [onclick], [contenteditable=true]';
  window.__vbNext = window.__vbNext || 1;
  const out = [];
  for (const el of document.querySelectorAll(SELECTOR)) {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || style.visibility === 'hidden' || style.display === 'none') continue;

    if (!el.dataset.vbId) el.dataset.vbId = 'e' + String(window.__vbNext++).padStart(2, '0');

    const name = (el.getAttribute('aria-label') || el.innerText || el.value ||
                  el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') || '')
                  .trim().replace(/\s+/g, ' ').slice(0, 80);
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') ||
      (tag === 'a' ? 'link' : (tag === 'input' || tag === 'textarea') ? 'textbox' : tag);

    out.push({
      id: el.dataset.vbId, role, name,
      x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
      in_viewport: r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth,
    });
  }
  return out;
}
