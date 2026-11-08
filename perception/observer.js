// PERSON 2: injected into the page. Returns interactable elements with stable ids.
() => {
  const SELECTOR = 'a[href], button, input, textarea, select, [role=button], [role=link], ' +
                   '[role=tab], [role=menuitem], [role=checkbox], [role=radio], [onclick], [contenteditable=true]';
  const ROLE_PRIORITY = ['textbox', 'checkbox', 'radio', 'button', 'select', 'tab', 'menuitem', 'link'];
  window.__vbNext = window.__vbNext || 1;

  const clean = (s) => (s || '').trim().replace(/\s+/g, ' ').slice(0, 80);

  function isVisible(el, style) {
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (parseFloat(style.opacity) === 0) return false;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    return true;
  }

  function isCovered(el, r) {
    // only check elements actually on screen; off-screen ones can't be covered by an overlay check anyway
    if (!(r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth)) return false;
    const cx = Math.min(Math.max(r.x + r.width / 2, 0), innerWidth - 1);
    const cy = Math.min(Math.max(r.y + r.height / 2, 0), innerHeight - 1);
    const top = document.elementFromPoint(cx, cy);
    if (!top) return false;
    // the voice-assistant overlay sits on top visually but must never block real clicks
    if (top.id === 'vox-host') return false;
    return !(top === el || el.contains(top) || top.contains(el));
  }

  function roleOf(el, tag) {
    const ariaRole = el.getAttribute('role');
    if (ariaRole) return ariaRole;
    if (tag === 'a') return 'link';
    if (tag === 'select') return 'select';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'button') return 'button';
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (['submit', 'button', 'reset', 'image'].includes(type)) return 'button';
      return 'textbox';
    }
    return tag;
  }

  function labelFromAncestorLabel(el) {
    const wrapping = el.closest('label');
    if (!wrapping) return '';
    const clone = wrapping.cloneNode(true);
    clone.querySelectorAll('input,textarea,select').forEach((n) => n.remove());
    return clean(clone.innerText);
  }

  function computeName(el, tag, type) {
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) return clean(ariaLabel);

    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const txt = labelledby.split(/\s+/)
        .map((id) => { const n = document.getElementById(id); return n ? n.innerText : ''; })
        .join(' ');
      if (clean(txt)) return clean(txt);
    }

    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl) { const txt = clean(lbl.innerText); if (txt) return txt; }
    }

    const wrapLabel = labelFromAncestorLabel(el);
    if (wrapLabel) return wrapLabel;

    if (tag === 'button' || ['submit', 'button', 'reset'].includes(type)) {
      const txt = clean(el.innerText) || clean(el.value);
      if (txt) return txt;
    }

    if (tag === 'a') {
      const txt = clean(el.innerText);
      if (txt) return txt;
      const img = el.querySelector('img[alt]');
      if (img && clean(img.alt)) return clean(img.alt);
    }

    const innerTxt = clean(el.innerText);
    if (innerTxt) return innerTxt;

    const placeholder = el.getAttribute('placeholder');
    if (placeholder) return clean(placeholder);

    const title = el.getAttribute('title');
    if (title) return clean(title);

    const alt = el.getAttribute('alt');
    if (alt) return clean(alt);

    if (el.querySelector) {
      const img2 = el.querySelector('img[alt]');
      if (img2 && clean(img2.alt)) return clean(img2.alt);
    }

    return '';
  }

  function hrefDomain(el) {
    if (!el.href) return null;
    try {
      const u = new URL(el.href, location.href);
      return u.hostname.replace(/^www\./, '');
    } catch { return null; }
  }

  function isHomepageLink(el) {
    if (!el.href) return false;
    try {
      const u = new URL(el.href, location.href);
      return u.hostname === location.hostname && (u.pathname === '/' || u.pathname === '');
    } catch { return false; }
  }

  function valueOf(el, tag, type) {
    if (tag === 'select') {
      const opt = el.options[el.selectedIndex];
      return opt ? clean(opt.text) : null;
    }
    if (tag === 'textarea' || (tag === 'input' && !['checkbox', 'radio', 'submit', 'button', 'reset'].includes(type))) {
      return el.value != null ? String(el.value).slice(0, 80) : null;
    }
    return null;
  }

  function iou(a, b) {
    const x1 = Math.max(a.x, b.x), y1 = Math.max(a.y, b.y);
    const x2 = Math.min(a.x + a.w, b.x + b.w), y2 = Math.min(a.y + a.h, b.y + b.h);
    const iw = Math.max(0, x2 - x1), ih = Math.max(0, y2 - y1);
    const inter = iw * ih;
    if (inter === 0) return 0;
    const union = a.w * a.h + b.w * b.h - inter;
    return union > 0 ? inter / union : 0;
  }

  const accepted = []; // [{el, rect, data}]
  const hrefSeen = new Set(); // same-destination SIBLING links (not nested) also collapse to one:
                               // search-result tiles commonly wrap the title, a thumbnail, the
                               // URL text, and the snippet in separate <a> tags to the same href.

  for (const el of document.querySelectorAll(SELECTOR)) {
    // the overlay lives in a Shadow DOM so document.querySelectorAll can never actually
    // see inside it, but skip defensively in case that ever changes.
    if (el.id === 'vox-host' || (el.closest && el.closest('#vox-host'))) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const style = getComputedStyle(el);
    if (!isVisible(el, style)) continue;
    if (isCovered(el, r)) continue;

    const tag = el.tagName.toLowerCase();
    const type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : null;
    const role = roleOf(el, tag);
    const rect = { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };

    if (role === 'link' && el.href) {
      if (hrefSeen.has(el.href)) continue; // keep the first sibling (usually the title link)
      hrefSeen.add(el.href);
    }

    // dedupe nested duplicates (e.g. <a><button>Same</button></a>): same-ish box, ancestor/descendant
    let dupIndex = -1;
    for (let i = 0; i < accepted.length; i++) {
      const other = accepted[i];
      if (iou(rect, other.rect) > 0.85 && (el.contains(other.el) || other.el.contains(el))) {
        dupIndex = i;
        break;
      }
    }

    if (dupIndex !== -1) {
      const existing = accepted[dupIndex];
      const existingPriority = ROLE_PRIORITY.indexOf(existing.data.role);
      const newPriority = ROLE_PRIORITY.indexOf(role);
      const existingRank = existingPriority === -1 ? ROLE_PRIORITY.length : existingPriority;
      const newRank = newPriority === -1 ? ROLE_PRIORITY.length : newPriority;
      if (newRank >= existingRank) continue; // keep the existing (more specific or equal) one
      // new element wins: replace, reuse the existing id for stability
      accepted[dupIndex] = { el, rect, data: buildData(el, tag, type, role, rect, existing.data.id) };
      continue;
    }

    accepted.push({ el, rect, data: buildData(el, tag, type, role, rect, null) });
  }

  function buildData(el, tag, type, role, rect, reuseId) {
    if (!el.dataset.vbId) el.dataset.vbId = reuseId || ('e' + String(window.__vbNext++).padStart(2, '0'));
    const name = computeName(el, tag, type);
    return {
      id: el.dataset.vbId, role, name,
      x: rect.x, y: rect.y, w: rect.w, h: rect.h,
      in_viewport: rect.y + rect.h > 0 && rect.y < innerHeight && rect.x + rect.w > 0 && rect.x < innerWidth,
      input_type: tag === 'select' ? 'select' : tag === 'textarea' ? 'textarea' : type,
      value: valueOf(el, tag, type),
      checked: (role === 'checkbox' || role === 'radio') ? !!el.checked : null,
      href_domain: role === 'link' ? hrefDomain(el) : null,
      is_homepage_link: role === 'link' ? isHomepageLink(el) : false,
      active: el === document.activeElement,
      options: tag === 'select' ? Array.from(el.options).slice(0, 20).map((o) => clean(o.text)) : [],
      // semantic landmarks catch well-built pages; the class/id patterns catch the common
      // case of a plain <div class="header">/"navbar" with no real HTML5 semantics (e.g. DDG)
      in_chrome: !!el.closest(
        'header, nav, footer, [role="navigation"], [role="banner"], [role="contentinfo"], ' +
        '[class*="header" i], [class*="navbar" i], [class*="masthead" i], [class*="footer" i], ' +
        '[id*="header" i], [id*="navbar" i], [id*="footer" i]'
      ),
    };
  }

  return accepted.map((a) => a.data);
}
