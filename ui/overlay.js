// Vox on-screen overlay. Injected via BrowserContext.add_init_script() so it mounts on
// every page/tab and survives navigation. Lives entirely inside a Shadow DOM host
// (#vox-host) so page CSS can't touch it and it can't touch the page.
//
// Design (SVG, colours, animations, states, panel styling, hide/minimize) is ported
// EXACTLY from ui/vox-mascot.html, the source of truth -- only host/sizing/mount
// plumbing, fail-safes, and the typed-command wiring are new.
//
// Public API: window.__vox = { set(state, text, level, minimized), setLevel(level) }
(() => {
  if (window.__voxMounting) return;
  window.__voxMounting = true;

  const STATES = {
    idle:      ["Idle",          'Say "search for physio clinics" — or click to type'],
    listening: ["Listening…",    ""],
    thinking:  ["Thinking…",     ""],
    acting:    ["Done",          ""],
    unsure:    ["Did you mean…", ""],
    error:     ["Oops",          "I couldn't find that on this page"],
  };

  const HOST_ID = "vox-host";
  const HC_STYLE_ID = "vb-high-contrast"; // keep in sync with executor/act.py

  function whenBodyReady(cb) {
    if (document.body) { cb(); return; }
    if (!document.documentElement) {
      // add_init_script can run before <html> itself exists -- retry shortly
      setTimeout(() => whenBodyReady(cb), 0);
      return;
    }
    const obs = new MutationObserver(() => {
      if (document.body) { obs.disconnect(); cb(); }
    });
    obs.observe(document.documentElement, { childList: true });
  }

  function mount() {
    if (document.getElementById(HOST_ID)) return; // double-injection guard

    const host = document.createElement("div");
    host.id = HOST_ID;
    host.style.cssText =
      "position:fixed;bottom:16px;right:16px;z-index:2147483647;pointer-events:none;" +
      "direction:ltr;";
    document.body.appendChild(host);

    const shadow = host.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = `
      * { box-sizing: border-box; }
      .stage { display: flex; align-items: flex-end; gap: 12px; font-family: "Segoe UI", system-ui, -apple-system, sans-serif; }

      /* ---------- the mascot ---------- */
      /* clickable even at normal size (not just minimized) -- clicking it toggles
         listening pause/resume. The DIV stays click-through (pointer-events:none);
         only the <svg>'s actual PAINTED pixels (pointer-events:auto resolves to
         "visiblePainted" inside SVG content) are hit-testable, so the transparent
         padding around the droplet still lets real page clicks through underneath. */
      .vox { width: 110px; height: 110px; position: relative; pointer-events: none; cursor: pointer; margin: 8px; }
      .vox svg { width: 100%; height: 100%; overflow: hidden; pointer-events: auto; } /* nothing can spill out and get clipped by the screen edge */
      .vox .body-g { transform-origin: 100px 110px; animation: bob 3.2s ease-in-out infinite; }
      .vox .eyes  { transform-origin: 100px 100px; transition: transform .25s ease; }
      .vox .eye   { transform-box: fill-box; transform-origin: center; transition: transform .2s ease; }
      .vox .lid   { transform-box: fill-box; transform-origin: center; transform: scaleY(0); }
      .vox .happy, .vox .brow, .vox .q, .vox .sweat, .vox .worry { transition: opacity .2s; }
      /* fail-safes already set opacity:0 as inline attributes (survives a blocked stylesheet);
         these !important rules are what's needed to turn them back ON per state. */
      .vox.acting .happy { opacity: 1 !important; }
      .vox.unsure .brow, .vox.unsure .q { opacity: 1 !important; }
      .vox.error .worry { opacity: 1 !important; }
      .vox.listening .ring { opacity: 1 !important; animation: ring 1.4s ease-out infinite; }

      /* idle: bob + blink */
      .vox.idle .eye { animation: blink 4s infinite; }

      /* listening: wide eyes, pulse with mic level */
      .vox.listening .eye { transform: scale(1.15); }
      .vox.listening .body-g { animation: none; transform: scale(calc(1 + var(--level, 0) * .08)); transition: transform .08s; }

      /* thinking: eyes look side to side */
      .vox.thinking .eyes { animation: look 1.2s ease-in-out infinite; }

      /* acting: happy ^ ^ and a hop */
      .vox.acting .eye { opacity: 0; }
      .vox.acting .body-g { animation: hop .6s ease-out infinite; }

      /* unsure: tilt, brow, question bubble */
      .vox.unsure .body-g { animation: none; transform: rotate(-10deg); transition: transform .3s; }
      .vox.unsure .eye.r { transform: scaleY(.8); }

      /* error: one flinch, worried brows, dripping sweat (friendly, not alarming) */
      .vox.error .body-g { animation: flinch .5s ease-in-out 1; }
      .vox.error .eye { transform: scaleY(.8) translateY(2px); }
      .vox .sweat { transform-box: fill-box; transform-origin: center top; }
      .vox.error .sweat   { opacity: 1 !important; animation: drip 1.4s ease-in .2s infinite; }
      .vox.error .sweat.s2 { opacity: 1 !important; animation: drip 1.4s ease-in .9s infinite; }

      @keyframes bob    { 0%,100% { transform: translateY(0) } 50% { transform: translateY(-6px) } }
      @keyframes blink  { 0%,92%,100% { transform: scaleY(1) } 95% { transform: scaleY(.1) } }
      @keyframes look   { 0%,100% { transform: translateX(-7px) } 50% { transform: translateX(7px) } }
      @keyframes hop    { 0%,100% { transform: translateY(0) scale(1,1) } 30% { transform: translateY(-14px) scale(.96,1.04) } 60% { transform: translateY(0) scale(1.05,.95) } }
      @keyframes flinch { 0%,100% { transform: translateX(0) rotate(0) } 20% { transform: translateX(-6px) rotate(-5deg) } 45% { transform: translateX(5px) rotate(4deg) } 70% { transform: translateX(-2px) rotate(-1deg) } }
      @keyframes drip   { 0% { opacity: 0; transform: translateY(-4px) scale(.6) } 15% { opacity: 1; transform: translateY(0) scale(1) } 75% { opacity: 1; transform: translateY(26px) scale(1,1.15) } 100% { opacity: 0; transform: translateY(34px) scale(.8,1.2) } }
      @keyframes ring   { 0%,100% { opacity: .35; stroke-width: 3 } 50% { opacity: 1; stroke-width: 6 } }

      @media (prefers-reduced-motion: reduce) {
        .vox * { animation: none !important; }
        .panel .text, .panel .text * { transition: none !important; animation: none !important; }
      }

      /* ---------- live transcript: word-diffing ---------- */
      /* max 2 lines; a soft mask (not a hard clip) fades out whatever scrolls past the
         top edge, so overflow feels like it's continuously scrolling up rather than
         getting chopped off. */
      .panel .text {
        font-size: 20px; font-weight: 600; text-shadow: 0 1px 0 rgba(255,255,255,.8); margin-top: 4px; color: #06182b;
        min-height: 1.4em; max-height: 2.8em; line-height: 1.4em; overflow: hidden; position: relative;
        /* click-through by default (it's just text); only the editable state below
           opts in, because that's what reveals the typed-command box. */
        pointer-events: none; cursor: default;
        -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 1.1em, #000 100%);
        mask-image: linear-gradient(to bottom, transparent 0, #000 1.1em, #000 100%);
        transition: opacity .12s ease;
      }
      .panel .text.crossfade-out { opacity: 0; }
      .panel .text .w {
        display: inline-block; margin-right: .28em; opacity: 1; transform: translateY(0);
        transition: opacity .12s ease, transform .12s ease;
      }
      .panel .text .w.enter { opacity: 0; transform: translateY(5px); }
      .panel .text .w.tail { opacity: .65; } /* last 2 words of a live partial -- still uncertain */

      /* when the text isn't echoing live speech it doubles as a "type instead" button */
      .panel .text[data-editable="1"] { pointer-events: auto; cursor: text; }
      .panel .text[data-editable="1"]:hover { text-decoration: underline dotted rgba(13,79,122,.45); text-underline-offset: 4px; }
      .panel .text[data-editable="1"]:focus-visible { outline: 2px solid #2f9be0; outline-offset: 3px; border-radius: 6px; }

      /* visually hidden but still reachable by screen readers -- carries ONLY finals
         and action results (see updateAriaLive in JS); the visible .text above updates
         on every partial too, which would be announced far too often if it were live. */
      .sr-only {
        position: absolute; width: 1px; height: 1px; overflow: hidden;
        clip: rect(0, 0, 0, 0); white-space: nowrap;
      }

      /* ---------- frosted transcript panel (Frutiger Aero) ---------- */
      .panel {
        width: min(360px, 60vw); padding: 14px 18px; border-radius: 18px; position: relative; overflow: hidden;
        /* Frutiger Aero frosted glass: light aqua/mint tint, a little see-through */
        background: linear-gradient(180deg, rgba(255,255,255,.80) 0%, rgba(232,248,255,.72) 55%, rgba(214,244,236,.70) 100%);
        backdrop-filter: blur(16px) saturate(160%); -webkit-backdrop-filter: blur(16px) saturate(160%);
        border: 1px solid rgba(255,255,255,.95);
        box-shadow: 0 10px 28px rgba(10,60,110,.22), 0 0 0 1px rgba(120,190,230,.35), inset 0 1px 0 #fff;
        pointer-events: none;
      }
      .panel::after {            /* glossy top half, classic Aero */
        content: ""; position: absolute; left: 0; right: 0; top: 0; height: 48%; pointer-events: none;
        background: linear-gradient(180deg, rgba(255,255,255,.75), rgba(255,255,255,.12));
        border-radius: 18px 18px 50% 50% / 18px 18px 14px 14px;
      }
      .panel > * { position: relative; z-index: 1; }
      .panel .label { font-size: 13px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: #0d4f7a; }
      .stage[data-state="error"] .panel .label { color: #9a4a00; }
      .panel .hide { position: absolute; top: 8px; right: 10px; width: 26px; height: 26px; border-radius: 50%;
        border: 1px solid rgba(255,255,255,.7); background: rgba(255,255,255,.4); color: #0b2540;
        font-size: 16px; line-height: 1; cursor: pointer; pointer-events: auto; z-index: 2; }
      /* minimized: panel gone, small droplet only; click it to restore */
      .stage.min .panel { display: none; }
      .stage.min .vox { width: 40px; height: 40px; cursor: pointer; pointer-events: auto; } /* scaled with our 110px (not the source's 180px) mascot */
      .stage.min .vox .q, .stage.min .vox .ring { display: none; }
      .panel .text  { font-size: 20px; font-weight: 600; text-shadow: 0 1px 0 rgba(255,255,255,.8); margin-top: 4px; color: #06182b; min-height: 1.4em; }

      /* Hidden by default: the panel's text IS the live transcript. Clicking the text
         (when Vox isn't mid-utterance) reveals the input; submitting or clearing it
         hides the input again. */
      .cmd-row { display: none; gap: 6px; margin-top: 10px; pointer-events: auto; }
      .stage[data-editing="1"] .cmd-row { display: flex; }
      .cmd-row input {
        flex: 1; font: inherit; font-size: 14px; padding: 7px 12px; border-radius: 999px;
        border: 1px solid rgba(20,80,140,.35); background: rgba(255,255,255,.88); outline: none; min-width: 0;
        color: #06182b; /* explicit dark text -- shadow DOM inherits color from the host
                            page's computed style otherwise, which isn't guaranteed readable */
      }
      .cmd-row input::placeholder { color: #4b5563; opacity: 1; } /* ~7:1 contrast on the input's own background */
      .cmd-row input:focus { border-color: #2f9be0; outline: 2px solid #2f9be0; outline-offset: 1px; }

      .stage[data-state="error"] .panel { border-color: rgba(245,158,11,.85); box-shadow: 0 10px 30px rgba(0,30,60,.25), 0 0 0 3px rgba(245,158,11,.25), inset 0 1px 0 rgba(255,255,255,.9); }

      /* the page's own high-contrast mode (our accessibility command) inverts everything,
         including us -- #vox-host gets a counter-filter (see JS below) to cancel that, and
         the panel switches to plain solid colours instead of the liquid-glass look, which
         would otherwise render as a muddy, half-inverted mess. */
      .stage[data-hc="1"] .panel {
        background: #fff; backdrop-filter: none; -webkit-backdrop-filter: none;
        border: 2px solid #0b2540; box-shadow: 0 4px 12px rgba(0,0,0,.3);
      }
      .stage[data-hc="1"] .panel::before,
      .stage[data-hc="1"] .panel::after { display: none; }
      .stage[data-hc="1"] .panel .text { color: #000; text-shadow: none; }
    `;
    shadow.appendChild(style);

    const stage = document.createElement("div");
    stage.className = "stage";
    stage.dataset.state = "idle";
    stage.dataset.editing = "0";
    stage.innerHTML = `
      <div class="panel">
        <button class="hide" id="hide" aria-label="Hide Vox" title="Hide (or say 'hide Vox')">–</button>
        <div class="label" id="label">Idle</div>
        <div class="text" id="text"><span id="text-inner"></span></div>
        <div class="sr-only" id="sr-live" aria-live="polite" aria-atomic="true"></div>
        <div class="cmd-row">
          <input id="cmd-input" type="text" placeholder="Type a command…" aria-label="Type a voice command" />
        </div>
      </div>

      <div class="vox idle" id="vox" role="img" aria-label="Vox assistant: idle">
        <svg viewBox="0 0 200 200" width="110" height="110" aria-hidden="true">
          <defs>
            <radialGradient id="vx-body" cx="38%" cy="32%" r="75%">
              <stop offset="0%"  stop-color="#ffffff" stop-opacity=".55"/>
              <stop offset="40%" stop-color="#8fe3ff" stop-opacity=".38"/>
              <stop offset="80%" stop-color="#2aa8e8" stop-opacity=".55"/>
              <stop offset="100%" stop-color="#0f78c0" stop-opacity=".85"/>
            </radialGradient>
            <linearGradient id="vx-ring" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stop-color="#b6f36a"/>
              <stop offset="100%" stop-color="#3ccf8e"/>
            </linearGradient>
            <radialGradient id="vx-glow" cx="50%" cy="85%" r="45%">
              <stop offset="0%" stop-color="#c8ffe9" stop-opacity=".8"/>
              <stop offset="100%" stop-color="#c8ffe9" stop-opacity="0"/>
            </radialGradient>
            <linearGradient id="vx-shine" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#fff" stop-opacity=".95"/>
              <stop offset="100%" stop-color="#fff" stop-opacity="0"/>
            </linearGradient>
            <radialGradient id="vx-shadow" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stop-color="#0b4a7a" stop-opacity=".28"/>
              <stop offset="100%" stop-color="#0b4a7a" stop-opacity="0"/>
            </radialGradient>
          </defs>

          <ellipse cx="100" cy="184" rx="52" ry="9" fill="url(#vx-shadow)"/>
          <circle class="ring" cx="100" cy="108" r="84" fill="none" stroke="url(#vx-ring)" stroke-width="5" style="opacity:0"/>

          <g class="body-g">
            <path d="M100 18 C 128 48, 172 74, 172 112 A 72 68 0 0 1 28 112 C 28 74, 72 48, 100 18 Z"
                  fill="url(#vx-body)" stroke="#ffffff" stroke-opacity=".85" stroke-width="2.5"/>
            <ellipse cx="100" cy="150" rx="46" ry="22" fill="url(#vx-glow)"/>
            <ellipse cx="74" cy="72" rx="26" ry="15" fill="url(#vx-shine)" transform="rotate(-28 74 72)"/>
            <circle cx="140" cy="140" r="5" fill="#fff" opacity=".55"/>
            <circle cx="128" cy="152" r="2.5" fill="#fff" opacity=".45"/>

            <g class="eyes">
              <g class="eye l"><ellipse cx="80" cy="108" rx="9" ry="13" fill="#0b2540"/><circle cx="83" cy="102" r="3.2" fill="#fff"/></g>
              <g class="eye r"><ellipse cx="120" cy="108" rx="9" ry="13" fill="#0b2540"/><circle cx="123" cy="102" r="3.2" fill="#fff"/></g>
              <g class="happy" fill="none" stroke="#0b2540" stroke-width="5" stroke-linecap="round" style="opacity:0">
                <path d="M70 112 Q80 98 90 112"/><path d="M110 112 Q120 98 130 112"/>
              </g>
              <g class="worry" fill="none" stroke="#0b2540" stroke-width="4" stroke-linecap="round" style="opacity:0">
                <path d="M68 92 L88 86"/><path d="M132 92 L112 86"/>
              </g>
              <path class="brow" d="M110 86 Q121 78 132 84" fill="none" stroke="#0b2540" stroke-width="4" stroke-linecap="round" style="opacity:0"/>
            </g>
            <ellipse cx="66" cy="128" rx="8" ry="4.5" fill="#ff9fc0" opacity=".55"/>
            <ellipse cx="134" cy="128" rx="8" ry="4.5" fill="#ff9fc0" opacity=".55"/>
            <path class="sweat s2" d="M150 86 c 3.5 6, 5.5 9, 5.5 12 a 5.5 5.5 0 0 1 -11 0 c 0 -3, 2 -6, 5.5 -12 z" fill="#bfefff" stroke="#3aa6dc" stroke-width="1.2" style="opacity:0"/>
            <path class="sweat" d="M156 70 c 5 8, 8 12, 8 16 a 8 8 0 0 1 -16 0 c 0 -4, 3 -8, 8 -16 z" fill="#bfefff" stroke="#3aa6dc" stroke-width="1.5" style="opacity:0"/>
          </g>

          <g class="q" style="opacity:0">
            <circle cx="168" cy="38" r="18" fill="#fff" stroke="#7fcdf5" stroke-width="2"/>
            <text x="168" y="46" text-anchor="middle" font-size="24" font-weight="700" fill="#1d7fc0">?</text>
          </g>
        </svg>
      </div>
    `;
    shadow.appendChild(stage);

    const voxEl = shadow.getElementById("vox");
    const labelEl = shadow.getElementById("label");
    const textEl = shadow.getElementById("text");
    const textInnerEl = shadow.getElementById("text-inner");
    const srLiveEl = shadow.getElementById("sr-live");
    const cmdInput = shadow.getElementById("cmd-input");
    const hideBtn = shadow.getElementById("hide");

    function sendCommand(text) {
      if (typeof window.voxCommand === "function") window.voxCommand(text);
    }

    // --- click-to-type -----------------------------------------------------------------
    // The text line is the transcript, not a form field. The typed-command input stays
    // hidden until the user clicks (or focuses + Enter) the text while Vox isn't
    // echoing a live partial -- clicking mid-utterance would mean "I'm still talking".
    function openEditor() {
      if (stage.classList.contains("min")) restore();
      stage.dataset.editing = "1";
      requestAnimationFrame(() => cmdInput.focus());
    }

    function closeEditor() {
      stage.dataset.editing = "0";
    }

    textEl.addEventListener("click", () => {
      if (currentState === "listening" && currentText) return;
      if (stage.dataset.editing === "1") return;
      openEditor();
    });
    textEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        e.stopPropagation();
        openEditor();
      }
    });

    const prefersReducedMotion =
      window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let currentState = "idle";
    let currentText = "";
    let wasListening = false; // was the PREVIOUS applyFace call also "listening"? (continuation vs fresh)

    // --- live transcript rendering: word-diffing for in-place partial updates, a
    // plain crossfade for everything else (state changes, action text, idle). Both
    // paths are coalesced through one requestAnimationFrame per tick so several
    // applyFace() calls landing in the same frame only touch the DOM once.
    function wordsOf(text) {
      return text.split(/\s+/).filter(Boolean);
    }

    function setWordsInstant(words, tailCount) {
      cancelPendingCrossfade();
      textInnerEl.innerHTML = "";
      words.forEach((word, i) => {
        const span = document.createElement("span");
        span.className = "w" + (tailCount && i >= words.length - tailCount ? " tail" : "");
        span.textContent = word;
        textInnerEl.appendChild(span);
      });
      textEl.scrollTop = textEl.scrollHeight; // pin to the latest 2 lines; the CSS mask softens the rest
    }

    // crossfadeWords() writes the DOM from a delayed setTimeout, not immediately --
    // if a newer update (another crossfade, or a word-diff) arrives before that timer
    // fires, the stale write must never land on top of it. At most one crossfade timer
    // is ever outstanding; anything that changes the text cancels it first.
    let crossfadeTimer = null;

    function cancelPendingCrossfade() {
      if (crossfadeTimer !== null) {
        clearTimeout(crossfadeTimer);
        crossfadeTimer = null;
      }
    }

    function renderWordsDiff(words) {
      // unchanged leading words are left alone entirely (no DOM touch, no animation);
      // a word that differs from what's already at that index crossfades in place;
      // words appended past the old length fade+slide in. The last 2 words stay
      // dimmer than the rest while the utterance is still live.
      cancelPendingCrossfade(); // a diff update supersedes any fade-out still in flight
      textEl.classList.remove("crossfade-out");
      const existing = Array.from(textInnerEl.children);
      words.forEach((word, i) => {
        const isTail = i >= words.length - 2;
        let span = existing[i];
        if (!span) {
          span = document.createElement("span");
          span.className = "w enter";
          span.textContent = word;
          textInnerEl.appendChild(span);
          requestAnimationFrame(() => span.classList.remove("enter"));
        } else if (span.textContent !== word) {
          span.textContent = word;
          span.classList.remove("enter");
          void span.offsetWidth; // restart the transition
          span.classList.add("enter");
          requestAnimationFrame(() => span.classList.remove("enter"));
        }
        span.classList.toggle("tail", isTail);
      });
      for (let i = existing.length - 1; i >= words.length; i--) existing[i].remove();
      textEl.scrollTop = textEl.scrollHeight;
    }

    function crossfadeWords(words) {
      cancelPendingCrossfade();
      textEl.classList.add("crossfade-out");
      crossfadeTimer = setTimeout(() => {
        crossfadeTimer = null;
        setWordsInstant(words, 0);
        textEl.classList.remove("crossfade-out");
      }, 120);
    }

    let pendingWords = null;
    let pendingContinuation = false;
    let rafScheduled = false;

    function scheduleWordsRender(words, isContinuation) {
      pendingWords = words;
      pendingContinuation = isContinuation;
      if (rafScheduled) return;
      rafScheduled = true;
      requestAnimationFrame(() => {
        rafScheduled = false;
        if (pendingContinuation) renderWordsDiff(pendingWords);
        else crossfadeWords(pendingWords);
      });
    }

    // the hidden aria-live region: only ever updated for results the user actually
    // needs announced -- never for "listening"/"thinking" (partials would spam a
    // screen reader every ~65ms) or the default idle placeholder (unchanging noise).
    function updateAriaLive(state, shown) {
      if (state === "acting" || state === "unsure" || state === "error") {
        srLiveEl.textContent = shown;
      }
    }

    function applyFace(state, text, level) {
      const meta = STATES[state] || STATES.idle;
      voxEl.className = "vox " + state;
      stage.dataset.state = state;
      voxEl.setAttribute("aria-label", "Vox assistant: " + state);
      labelEl.textContent = meta[0];
      let shown = (text !== undefined && text !== null && text !== "") ? text : meta[1];
      // "thinking" has no placeholder of its own: keep the words already on screen
      // instead of blanking the panel while the command is being understood.
      if (!shown && state === "thinking") shown = currentText;
      // Editable (clickable -> typed-command box) unless the line is echoing a live
      // partial, in which case it is the transcript and clicks should pass through.
      const editable = !(state === "listening" && shown);
      textEl.dataset.editable = editable ? "1" : "0";
      textEl.title = editable ? "Click to type a command" : "";
      if (editable) {
        textEl.setAttribute("role", "button");
        textEl.setAttribute("tabindex", "0");
      } else {
        textEl.removeAttribute("role");
        textEl.removeAttribute("tabindex");
      }

      if (shown !== currentText) {
        const words = wordsOf(shown);
        const isContinuation = state === "listening" && wasListening;
        if (prefersReducedMotion) {
          setWordsInstant(words, 0); // instant text updates only, no animation
        } else {
          scheduleWordsRender(words, isContinuation);
        }
        updateAriaLive(state, shown);
      }
      wasListening = (state === "listening");

      if (typeof level === "number") {
        voxEl.style.setProperty("--level", String(Math.max(0, Math.min(1, level))));
      }
      return shown;
    }

    function minimize() {
      stage.classList.add("min");
    }

    function restore() {
      stage.classList.remove("min");
      // reset + reflow + reapply forces CSS animations (bob, ring, etc.) to restart --
      // just removing the "min" class alone leaves them in whatever paused state
      // they were in when minimized.
      voxEl.className = "vox";
      void voxEl.offsetWidth;
      applyFace(currentState, currentText);
    }

    function setVoxState(state, text, level, minimized) {
      currentState = state;
      currentText = applyFace(state, text, level);
      // unsure (confirm/disambiguate) and error auto-restore the panel so the user
      // never misses them, even if Vox was minimized when they came in.
      const forceShow = state === "unsure" || state === "error";
      const wantMin = forceShow ? false : !!minimized;
      const isMin = stage.classList.contains("min");
      if (wantMin && !isMin) minimize();
      else if (!wantMin && isMin) restore();
    }

    hideBtn.addEventListener("click", () => { minimize(); sendCommand("minimize"); });
    voxEl.addEventListener("click", () => {
      if (stage.classList.contains("min")) {
        restore();
        sendCommand("show vox");
      } else {
        // Python owns whether listening is actually paused right now; it decides
        // what "toggle" means and sets the resulting state/text back on us.
        sendCommand("toggle listening");
      }
    });

    // --- typed-command input: history, submit, and isolating keystrokes from the page ---
    let cmdHistory = [];       // last ~20 submitted commands, most recent last
    let historyPos = -1;       // -1 = not browsing history
    let draftBeforeHistory = "";

    function submitCommand() {
      const value = cmdInput.value.trim();
      if (!value) return;
      cmdInput.value = "";
      historyPos = -1;
      draftBeforeHistory = "";
      cmdHistory.push(value);
      if (cmdHistory.length > 20) cmdHistory.shift();
      closeEditor(); // back to the live-transcript view; the input was only temporary
      applyFace("listening", value); // optimistic immediate feedback; Python's own
                                      // listening -> thinking -> ... follows right after
      sendCommand(value);
    }

    function historyUp() {
      if (cmdHistory.length === 0) return;
      if (historyPos === -1) {
        draftBeforeHistory = cmdInput.value;
        historyPos = cmdHistory.length - 1;
      } else if (historyPos > 0) {
        historyPos--;
      }
      cmdInput.value = cmdHistory[historyPos];
    }

    function historyDown() {
      if (historyPos === -1) return;
      if (historyPos < cmdHistory.length - 1) {
        historyPos++;
        cmdInput.value = cmdHistory[historyPos];
      } else {
        historyPos = -1;
        cmdInput.value = draftBeforeHistory;
      }
    }

    function onCmdKeydown(e) {
      // isolate the input from the page: site keyboard shortcuts (YouTube "k"/"f",
      // GitHub "s", Gmail's single-letter shortcuts, ...) must never fire while typing
      // here. Capture phase + stopImmediatePropagation on the target itself is the
      // earliest possible point to cut the event off before it can bubble out through
      // the shadow boundary (keydown/keyup/keypress are composed and DO cross it).
      e.stopPropagation();
      e.stopImmediatePropagation();
      if (e.key === "Enter") {
        if (e.shiftKey) return; // no special action
        e.preventDefault();
        submitCommand();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        historyUp();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        historyDown();
      } else if (e.key === "Escape") {
        cmdInput.value = "";
        historyPos = -1;
        closeEditor();
        cmdInput.blur();
      }
    }
    cmdInput.addEventListener("keydown", onCmdKeydown, { capture: true });
    // keep a half-typed draft visible on blur, but collapse the input when it's empty
    cmdInput.addEventListener("blur", () => {
      if (!cmdInput.value.trim()) closeEditor();
    });
    ["keyup", "keypress"].forEach((type) => {
      cmdInput.addEventListener(type, (e) => {
        e.stopPropagation();
        e.stopImmediatePropagation();
      }, { capture: true });
    });

    // the page's own high-contrast mode (our accessibility command) applies
    // `filter: invert(1) hue-rotate(180deg)` to <html>, which -- since filter
    // inherits into descendants regardless of Shadow DOM -- would invert us too.
    // Applying the identical filter again on the host cancels it out (invert∘invert
    // = identity, hueRotate(180)∘hueRotate(180) = hueRotate(360) = identity).
    function syncHighContrast() {
      const on = !!document.getElementById(HC_STYLE_ID);
      host.style.filter = on ? "invert(1) hue-rotate(180deg)" : "";
      stage.dataset.hc = on ? "1" : "0";
    }
    syncHighContrast();
    new MutationObserver(syncHighContrast).observe(document.head, { childList: true });

    function setLevel(level) {
      // high-frequency mic-level updates (~10-15/s) -- only touches --level, skips
      // the full state/text/minimized pipeline above.
      voxEl.style.setProperty("--level", String(Math.max(0, Math.min(1, level))));
    }

    window.__vox = { set: setVoxState, setLevel };
    setVoxState("idle");
  }

  whenBodyReady(mount);
})();
