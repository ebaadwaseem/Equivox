// Vox on-screen overlay. Injected via BrowserContext.add_init_script() so it mounts on
// every page/tab and survives navigation. Lives entirely inside a Shadow DOM host
// (#vox-host) so page CSS can't touch it and it can't touch the page.
//
// Design (SVG, colours, animations, states, panel styling) is unchanged from
// ui/vox-mascot.html -- only the host/sizing/mount plumbing is new.
//
// Public API: window.__vox = { set(state, text, level) }
(() => {
  if (window.__voxMounting) return;
  window.__voxMounting = true;

  const STATES = {
    idle:      ["Idle",          'Say something like "search for physio clinics"'],
    listening: ["Listening…",    ""],
    thinking:  ["Thinking…",     ""],
    acting:    ["Done",          ""],
    unsure:    ["Did you mean…", ""],
    error:     ["Hmm",           "I couldn't find that on this page"],
  };

  const HOST_ID = "vox-host";

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
    if (document.getElementById(HOST_ID)) return;

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
      .wrap { display: flex; align-items: flex-end; gap: 12px; font-family: "Segoe UI", system-ui, -apple-system, sans-serif; }

      /* ---------- the mascot ---------- */
      .vox { width: 110px; height: 110px; position: relative; pointer-events: none; }
      .vox svg { width: 100%; height: 100%; overflow: visible; }
      .vox .body-g { transform-origin: 100px 110px; animation: bob 3.2s ease-in-out infinite; }
      .vox .eyes  { transform-origin: 100px 100px; transition: transform .25s ease; }
      .vox .eye   { transform-box: fill-box; transform-origin: center; transition: transform .2s ease; }
      .vox .lid   { transform-box: fill-box; transform-origin: center; transform: scaleY(0); }
      .vox .happy, .vox .brow, .vox .q, .vox .sweat { opacity: 0; transition: opacity .2s; }
      .vox .ring  { opacity: 0; transform-origin: 100px 100px; }

      /* idle: bob + blink */
      .vox.idle .eye { animation: blink 4s infinite; }

      /* listening: wide eyes, pulse with mic level */
      .vox.listening .eye { transform: scale(1.15); }
      .vox.listening .body-g { animation: none; transform: scale(calc(1 + var(--level, 0) * .08)); transition: transform .08s; }
      .vox.listening .ring { opacity: 1; animation: ring 1.4s ease-out infinite; }

      /* thinking: eyes look side to side */
      .vox.thinking .eyes { animation: look 1.2s ease-in-out infinite; }

      /* acting: happy ^ ^ and a hop */
      .vox.acting .eye { opacity: 0; }
      .vox.acting .happy { opacity: 1; }
      .vox.acting .body-g { animation: hop .6s ease-out infinite; }

      /* unsure: tilt, brow, question bubble */
      .vox.unsure .body-g { animation: none; transform: rotate(-10deg); transition: transform .3s; }
      .vox.unsure .brow, .vox.unsure .q { opacity: 1; }
      .vox.unsure .eye.r { transform: scaleY(.8); }

      /* error: wobble + worried */
      .vox.error .body-g { animation: wobble .5s ease-in-out infinite; }
      .vox.error .eye { transform: scaleY(.75); }
      .vox.error .sweat { opacity: 1; }

      @keyframes bob    { 0%,100% { transform: translateY(0) } 50% { transform: translateY(-6px) } }
      @keyframes blink  { 0%,92%,100% { transform: scaleY(1) } 95% { transform: scaleY(.1) } }
      @keyframes look   { 0%,100% { transform: translateX(-7px) } 50% { transform: translateX(7px) } }
      @keyframes hop    { 0%,100% { transform: translateY(0) scale(1,1) } 30% { transform: translateY(-14px) scale(.96,1.04) } 60% { transform: translateY(0) scale(1.05,.95) } }
      @keyframes wobble { 0%,100% { transform: rotate(0) } 25% { transform: rotate(-6deg) } 75% { transform: rotate(6deg) } }
      @keyframes ring   { from { transform: scale(.85); opacity: .7 } to { transform: scale(1.35); opacity: 0 } }

      @media (prefers-reduced-motion: reduce) {
        .vox * { animation: none !important; }
      }

      /* ---------- frosted transcript panel ---------- */
      .panel {
        width: min(360px, 60vw); padding: 14px 18px; border-radius: 18px;
        background: rgba(255,255,255,.88); backdrop-filter: blur(14px);
        border: 1px solid rgba(255,255,255,.9);
        box-shadow: 0 8px 30px rgba(20,80,140,.18), inset 0 1px 0 #fff;
        pointer-events: none; color: #0b2540;
      }
      .panel .label { font-size: 12px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: #1d5f8a; }
      .panel .text  { font-size: 20px; margin-top: 4px; color: #0b2540; min-height: 1.4em; }

      /* optional typed-command box -- the ONE part of the overlay that accepts clicks */
      .cmd-row { display: flex; gap: 6px; margin-top: 10px; pointer-events: auto; }
      .cmd-row input {
        flex: 1; font: inherit; font-size: 14px; padding: 7px 12px; border-radius: 999px;
        border: 1px solid rgba(20,80,140,.25); outline: none; min-width: 0;
      }
      .cmd-row input:focus { border-color: #2f9be0; }
    `;
    shadow.appendChild(style);

    const wrap = document.createElement("div");
    wrap.className = "wrap";
    wrap.innerHTML = `
      <div class="panel" aria-live="polite">
        <div class="label" id="label">Idle</div>
        <div class="text" id="text">Say something like "search for physio clinics"</div>
        <div class="cmd-row">
          <input id="cmd-input" type="text" placeholder="Type a command…" aria-label="Type a voice command" />
        </div>
      </div>

      <div class="vox idle" id="vox" role="img" aria-label="Vox assistant: idle">
        <svg viewBox="0 0 200 200" aria-hidden="true">
          <defs>
            <radialGradient id="vx-body" cx="38%" cy="32%" r="75%">
              <stop offset="0%"  stop-color="#e9fbff"/>
              <stop offset="35%" stop-color="#7fdcff"/>
              <stop offset="75%" stop-color="#1fa3e0"/>
              <stop offset="100%" stop-color="#0f6fb0"/>
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
          <circle class="ring" cx="100" cy="100" r="80" fill="none" stroke="#5cc8ff" stroke-width="4"/>

          <g class="body-g">
            <path d="M100 18 C 128 48, 172 74, 172 112 A 72 68 0 0 1 28 112 C 28 74, 72 48, 100 18 Z"
                  fill="url(#vx-body)" stroke="#0e6aa8" stroke-opacity=".35" stroke-width="2"/>
            <ellipse cx="74" cy="72" rx="26" ry="15" fill="url(#vx-shine)" transform="rotate(-28 74 72)"/>
            <circle cx="140" cy="140" r="5" fill="#fff" opacity=".55"/>
            <circle cx="128" cy="152" r="2.5" fill="#fff" opacity=".45"/>

            <g class="eyes">
              <g class="eye l"><ellipse cx="80" cy="108" rx="9" ry="13" fill="#0b2540"/><circle cx="83" cy="102" r="3.2" fill="#fff"/></g>
              <g class="eye r"><ellipse cx="120" cy="108" rx="9" ry="13" fill="#0b2540"/><circle cx="123" cy="102" r="3.2" fill="#fff"/></g>
              <g class="happy" fill="none" stroke="#0b2540" stroke-width="5" stroke-linecap="round">
                <path d="M70 112 Q80 98 90 112"/><path d="M110 112 Q120 98 130 112"/>
              </g>
              <path class="brow" d="M110 86 Q121 78 132 84" fill="none" stroke="#0b2540" stroke-width="4" stroke-linecap="round"/>
            </g>
            <ellipse cx="66" cy="128" rx="8" ry="4.5" fill="#ff9fc0" opacity=".55"/>
            <ellipse cx="134" cy="128" rx="8" ry="4.5" fill="#ff9fc0" opacity=".55"/>
            <path class="sweat" d="M156 70 c 5 8, 8 12, 8 16 a 8 8 0 0 1 -16 0 c 0 -4, 3 -8, 8 -16 z" fill="#bfefff" stroke="#3aa6dc" stroke-width="1.5"/>
          </g>

          <g class="q">
            <circle cx="168" cy="38" r="18" fill="#fff" stroke="#7fcdf5" stroke-width="2"/>
            <text x="168" y="46" text-anchor="middle" font-size="24" font-weight="700" fill="#1d7fc0">?</text>
          </g>
        </svg>
      </div>
    `;
    shadow.appendChild(wrap);

    const voxEl = shadow.getElementById("vox");
    const labelEl = shadow.getElementById("label");
    const textEl = shadow.getElementById("text");
    const cmdInput = shadow.getElementById("cmd-input");

    function setVoxState(state, text, level) {
      const meta = STATES[state] || STATES.idle;
      voxEl.className = "vox " + state;
      voxEl.setAttribute("aria-label", "Vox assistant: " + state);
      labelEl.textContent = meta[0];
      textEl.textContent = (text !== undefined && text !== null && text !== "") ? text : meta[1];
      if (typeof level === "number") {
        voxEl.style.setProperty("--level", String(Math.max(0, Math.min(1, level))));
      }
    }

    cmdInput.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      const value = cmdInput.value.trim();
      if (!value) return;
      cmdInput.value = "";
      if (typeof window.voxCommand === "function") {
        window.voxCommand(value);
      }
    });

    window.__vox = { set: setVoxState };
    setVoxState("idle");
  }

  whenBodyReady(mount);
})();
