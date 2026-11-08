// Vox on-screen overlay. Injected via BrowserContext.add_init_script() so it mounts on
// every page/tab and survives navigation. Lives entirely inside a Shadow DOM host
// (#vox-host) so page CSS can't touch it and it can't touch the page.
//
// Design (SVG, colours, animations, states, panel styling, hide/minimize) is ported
// EXACTLY from ui/vox-mascot.html, the source of truth -- only host/sizing/mount
// plumbing, fail-safes, and the typed-command wiring are new.
//
// Public API: window.__vox = { set(state, text, level, minimized) }
(() => {
  if (window.__voxMounting) return;
  window.__voxMounting = true;

  const STATES = {
    idle:      ["Idle",          'Say something like "search for physio clinics"'],
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
      .vox { width: 110px; height: 110px; position: relative; pointer-events: none; margin: 8px; }
      .vox svg { width: 100%; height: 100%; overflow: hidden; } /* nothing can spill out and get clipped by the screen edge */
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

      .cmd-row { display: flex; gap: 6px; margin-top: 10px; pointer-events: auto; }
      .cmd-row input {
        flex: 1; font: inherit; font-size: 14px; padding: 7px 12px; border-radius: 999px;
        border: 1px solid rgba(255,255,255,.6); background: rgba(255,255,255,.5); outline: none; min-width: 0;
      }
      .cmd-row input:focus { border-color: #2f9be0; }

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
    stage.innerHTML = `
      <div class="panel" aria-live="polite">
        <button class="hide" id="hide" aria-label="Hide Vox" title="Hide (or say 'hide Vox')">–</button>
        <div class="label" id="label">Idle</div>
        <div class="text" id="text">Say something like "search for physio clinics"</div>
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
    const cmdInput = shadow.getElementById("cmd-input");
    const hideBtn = shadow.getElementById("hide");

    function sendCommand(text) {
      if (typeof window.voxCommand === "function") window.voxCommand(text);
    }

    let currentState = "idle";
    let currentText = "";

    function applyFace(state, text, level) {
      const meta = STATES[state] || STATES.idle;
      voxEl.className = "vox " + state;
      stage.dataset.state = state;
      voxEl.setAttribute("aria-label", "Vox assistant: " + state);
      labelEl.textContent = meta[0];
      const shown = (text !== undefined && text !== null && text !== "") ? text : meta[1];
      textEl.textContent = shown;
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
      if (stage.classList.contains("min")) { restore(); sendCommand("show vox"); }
    });

    cmdInput.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      const value = cmdInput.value.trim();
      if (!value) return;
      cmdInput.value = "";
      sendCommand(value);
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

    window.__vox = { set: setVoxState };
    setVoxState("idle");
  }

  whenBodyReady(mount);
})();
