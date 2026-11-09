// PERSON 2: injected into the page. Returns interactable elements with stable ids.
() => {
  const SELECTOR = 'a[href], button, input, textarea, select, [role=button], [role=link], ' +
                   '[role=tab], [role=menuitem], [role=checkbox], [role=radio], [role=textbox], [role=searchbox], [role=combobox], [role=listbox], [role=option], [aria-label][tabindex], [onclick], [contenteditable]:not([contenteditable=false])';
  const ROLE_PRIORITY = ['textbox', 'checkbox', 'radio', 'button', 'select', 'tab', 'menuitem', 'link'];
  window.__vbNext = window.__vbNext || 1;

  const clean = (s) => (s || '').trim().replace(/\s+/g, ' ').slice(0, 140);

  function isVisible(el, style) {
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (parseFloat(style.opacity) === 0) return false;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    if (el.closest('[aria-hidden="true"],[inert]')) return false;
    return true;
  }

  function isCovered(el, r) {
    // only check elements actually on screen; off-screen ones can't be covered by an overlay check anyway
    if (!(r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth)) return false;
    const cx = Math.min(Math.max(r.x + r.width / 2, 0), innerWidth - 1);
    const cy = Math.min(Math.max(r.y + r.height / 2, 0), innerHeight - 1);
    const root = el.getRootNode();
    const top = (root.elementFromPoint ? root : document).elementFromPoint(cx, cy);
    if (!top) return false;
    // the voice-assistant overlay sits on top visually but must never block real clicks
    if (top.id === 'vox-host') return false;
    return !(top === el || el.contains(top) || top.contains(el));
  }

  function roleOf(el, tag) {
    const ariaRole = el.getAttribute('role');
    // the ARIA 1.2 EDITABLE combobox pattern (type-to-filter, e.g. YouTube's search
    // box) is a real <input>/<textarea> that you type into -- a textbox, not a
    // dropdown-trigger "select" -- which role=combobox otherwise correctly means for
    // a non-input widget (a <div>/<button> that just opens a popup of options).
    if (ariaRole === 'combobox' || ariaRole === 'listbox') {
      return (tag === 'input' || tag === 'textarea') ? 'textbox' : 'select';
    }
    if (ariaRole === 'searchbox' || el.isContentEditable) return 'textbox';
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
    if (el.hasAttribute('tabindex') && el.getAttribute('aria-label')) return 'button';
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
    if (ariaLabel && /[\p{L}\p{N}]/u.test(ariaLabel)) return clean(ariaLabel);
    const tooltip = el.getAttribute('data-tooltip') || el.getAttribute('data-tooltip-content') || el.getAttribute('title');
    if (tooltip) return clean(tooltip);

    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const txt = labelledby.split(/\s+/)
        .map((id) => { const n = el.getRootNode().getElementById(id); return n ? n.innerText : ''; })
        .join(' ');
      if (clean(txt)) return clean(txt);
    }

    if (el.id) {
      const lbl = el.getRootNode().querySelector(`label[for="${CSS.escape(el.id)}"]`);
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

    const innerTxt = tag === 'select' || el.isContentEditable ? '' : clean(el.innerText);
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

  // "open the first one"/"click the first result" must land on an actual result,
  // never the search engine's own logo/home link or a link back to this same
  // results page -- both of which sit earlier in DOM order than the real results
  // on DuckDuckGo's static HTML results page.
  function isOwnRootOrSearchPage(el) {
    if (!el.href) return false;
    try {
      const u = new URL(el.href, location.href);
      if (u.hostname !== location.hostname) return false;
      if (u.pathname === '/' || u.pathname === '') return true; // site root
      if (u.pathname === location.pathname && !u.search) return true; // e.g. DDG's /html/ logo link
      return false;
    } catch { return false; }
  }

  function isSearchResultLink(el) {
    if (el.tagName !== 'A' || isOwnRootOrSearchPage(el)) return false;
    if (el.matches('a.result__a')) return true; // DuckDuckGo HTML results
    if (el.querySelector('h3')) return true; // Google-style: <a><h3>Title</h3></a>
    // generic: a link inside a container actually labeled as a result (post-dedup,
    // sibling icon/url-text links to the same destination are already gone -- see
    // hrefSeen above -- so whichever link survived here needs no extra length check).
    return !!el.closest('[class*="result" i], [id*="result" i]');
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
    if (type === 'password') return null;
    if (el.isContentEditable) return clean(el.innerText);
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

  // Autocomplete popups may be ARIA grids (Maps), not role=option lists.
  // Follow the input's ownership rather than treating every table row as clickable.
  const suggestionRows = new Map();
  function collectSuggestions(root) {
    const owners = [...root.querySelectorAll('[aria-controls], [aria-owns]')];
    owners.sort((a, b) => Number(b === root.activeElement) - Number(a === root.activeElement));
    const popups = [];
    for (const owner of owners) {
      if (!owner.matches('input, textarea, [role=combobox], [aria-autocomplete]')) continue;
      for (const id of ((owner.getAttribute('aria-controls') || '') + ' ' + (owner.getAttribute('aria-owns') || '')).split(/\s+/)) {
        const popup = root.getElementById(id);
        if (popup && !popups.includes(popup)) popups.push(popup);
      }
    }
    // Standalone listboxes also expose options when focus has moved to an option.
    for (const popup of root.querySelectorAll('[role=listbox]')) {
      if (!popups.includes(popup)) popups.push(popup);
    }
    for (const popup of popups) {
      const r = popup.getBoundingClientRect();
      if (!r.width || !r.height || !isVisible(popup, getComputedStyle(popup))) continue;
      const rows = [...popup.querySelectorAll('[role=option], [role=row], [role=listitem]')]
        .filter(row => !row.parentElement.closest('[role=option], [role=row], [role=listitem]') ||
          !popup.contains(row.parentElement.closest('[role=option], [role=row], [role=listitem]')));
      const visible = rows.filter(row => {
        const box = row.getBoundingClientRect();
        return box.width && box.height && box.bottom > 0 && box.top < innerHeight &&
          isVisible(row, getComputedStyle(row)) && !isCovered(row, box);
      });
      if (!visible.length) continue;
      if (!popup.dataset.vbId) popup.dataset.vbId = 'e' + String(window.__vbNext++).padStart(2, '0');
      visible.forEach(row => suggestionRows.set(row, popup.dataset.vbId));
      break; // only the active/first visible autocomplete popup gets numbering
    }
  }

  const accepted = []; // [{el, rect, data}]
  const hrefSeen = new Set(); // same-destination SIBLING links (not nested) also collapse to one:
                               // search-result tiles commonly wrap the title, a thumbnail, the
                               // URL text, and the snippet in separate <a> tags to the same href.

  function* candidates(root) {
    collectSuggestions(root);
    yield* new Set([...root.querySelectorAll(SELECTOR), ...suggestionRows.keys()].filter(el => el.getRootNode() === root));
    for (const host of root.querySelectorAll('*')) {
      if (host.id !== 'vox-host' && host.shadowRoot) yield* candidates(host.shadowRoot);
    }
  }
  for (const el of candidates(document)) {
    // the overlay lives in a Shadow DOM so document.querySelectorAll can never actually
    // see inside it, but skip defensively in case that ever changes.
    if (el.id === 'vox-host' || (el.closest && el.closest('#vox-host'))) continue;
    if ([...suggestionRows.keys()].some(row => row !== el && row.contains(el))) continue;
    // A one-row listbox shares its box with the option. Keep the actionable row,
    // not the popup container, when nested-box deduplication runs below.
    if (el.matches('[role=listbox], [role=grid]') &&
        [...suggestionRows.keys()].some(row => el !== row && el.contains(row))) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const style = getComputedStyle(el);
    if (!isVisible(el, style)) continue;
    if (isCovered(el, r)) continue;

    const tag = el.tagName.toLowerCase();
    const type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : null;
    const role = suggestionRows.has(el) ? 'option' : roleOf(el, tag);
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
    const labels = suggestionRows.has(el) ? [...el.querySelectorAll('[aria-label]')]
      .filter(node => !node.closest('[aria-hidden=true]'))
      .map(node => clean(node.getAttribute('aria-label'))).filter(Boolean) : [];
    const name = el.getAttribute('aria-label') ? computeName(el, tag, type) : labels[0] || computeName(el, tag, type);
    return {
      id: el.dataset.vbId, role, name,
      suggestion_group: suggestionRows.get(el) || '',
      is_video_link: tag === 'a' && /\/(?:watch\?|shorts\/)/.test(el.href || ''),
      is_search_result: role === 'link' && isSearchResultLink(el),
      description: clean(labels.slice(1).join(' ')) || clean((el.getAttribute('aria-describedby') || '').split(/\s+/)
        .map(id => el.getRootNode().getElementById(id)?.textContent || '').join(' ')) ||
        clean(el.closest('[role="article"]')?.innerText || ''),
      x: rect.x, y: rect.y, w: rect.w, h: rect.h,
      in_viewport: rect.y + rect.h > 0 && rect.y < innerHeight && rect.x + rect.w > 0 && rect.x < innerWidth,
      input_type: tag === 'select' ? 'select' : tag === 'textarea' ? 'textarea' : type,
      input_name: el.getAttribute('name') || null,
      value: valueOf(el, tag, type),
      checked: (role === 'checkbox' || role === 'radio') ? !!el.checked : null,
      href_domain: role === 'link' ? hrefDomain(el) : null,
      is_homepage_link: role === 'link' ? isHomepageLink(el) : false,
      active: el === el.getRootNode().activeElement,
      options: tag === 'select' ? Array.from(el.options).filter(o => !o.disabled && !o.parentElement.disabled).slice(0, 100).map((o) => clean(o.text)) : [],
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
