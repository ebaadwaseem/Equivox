"""The shared contract. Everyone imports from here. Change it together, not alone."""
from dataclasses import dataclass, field
from typing import Literal, Optional


# ---- Person 1 (audio) produces these ----
@dataclass
class Transcript:
    text: str
    is_final: bool          # False = partial (still talking), True = utterance finished
    confidence: float = 1.0
    id: Optional[int] = None  # groups a stream of partials + their final into one utterance
                               # (set by audio/apple_transcriber.py; None for typed/panel input,
                               # which naturally opts those sources out of early-commit tracking)
    silent_ms: float = 0.0    # set only by the silence retry: how long the user has been quiet


# ---- Person 2 (perception) produces these ----
@dataclass
class Element:
    id: str                 # stable id like "e01" (also set as data-vb-id on the DOM node)
    role: str               # button, link, textbox, ...
    name: str               # visible text / aria-label / placeholder
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    in_viewport: bool = True
    # optional extra state (added by Person 2, all default so old code/tests still work)
    input_type: Optional[str] = None   # input[type] / "textarea" / "select"
    value: Optional[str] = None        # current text value / selected option label
    checked: Optional[bool] = None     # checkbox/radio state
    href_domain: Optional[str] = None  # short domain for links, e.g. "example.com"
    active: bool = False               # True if this is document.activeElement
    options: list[str] = field(default_factory=list)  # <select> option labels, for matching e.g. "select tuesday"
    in_chrome: bool = False  # inside <header>/<nav>/<footer> -- excluded when counting "results"
    is_homepage_link: bool = False  # link's href is the current site's own root ("/") -- a logo link


@dataclass
class PageState:
    url: str
    title: str
    elements: list[Element] = field(default_factory=list)
    screenshot_b64: Optional[str] = None   # optional low-res screenshot


# ---- Person 3 (backend/model) produces these ----
# "select" added for <select> dropdowns (e.g. "select Tuesday") -- clicking one open headlessly
# isn't reliable, it needs Playwright's select_option(), so it gets its own intent.
# "find" is voice Ctrl+F -- scrolls to/highlights arbitrary page TEXT, not just interactive
# elements, so it doesn't use target_id at all.
# "new_tab"/"close_tab"/"switch_tab" change which Page is active; main.py handles them before
# the generic executor since execute() only ever acts on a single already-known page.
# "accessibility" covers zoom/text-size/high-contrast toggles (Decision.text carries which one).
Intent = Literal[
    "click", "type", "select", "scroll", "navigate", "back", "forward", "search", "find",
    "new_tab", "close_tab", "switch_tab", "accessibility", "none",
]


@dataclass
class Decision:
    is_command: bool
    intent: Intent
    target_id: Optional[str] = None   # Element.id
    text: Optional[str] = None        # text to type / search query / url / option label
    # fraction of the scroll container's visible height, negative = up (e.g. -0.8 = up
    # 80% of a page). Was pixels (int); widened to float since the actual pixel amount
    # depends on whichever container ends up scrolling, only known at execute() time.
    scroll_amount: float = 0
    complete: bool = True             # is the command finished being spoken?
    confidence: float = 1.0
    # optional extras (added by Person 3, all default so old code/tests still work)
    destructive: bool = False         # irreversible-looking action (delete, submit) -> ask to confirm
    ambiguous: bool = False           # top-2 target candidates are too close to call automatically
    target_confidence: float = 1.0    # how sure the model is about target_id specifically
    scroll_to: Optional[str] = None   # "top" | "bottom" -- overrides scroll_amount when set
    # raw probabilities behind the already-thresholded is_command/complete booleans above
    # (those threshold at 0.5; early-commit-on-partials needs the stricter 0.85/0.8 bars).
    is_command_confidence: float = 1.0
    complete_confidence: float = 1.0
    intent_confidence: float = 1.0
    # Rolling-decision extras. act_now is None for callers that only know final transcripts
    # (keyword fallback / old tests); production Jev decisions always set it.
    act_now: Optional[str] = None      # not_a_command | incomplete | clause_complete | utterance_complete
    is_correction: bool = False        # user is reversing/redirecting the previous action
    payload_bounded: bool = False      # free text ended at a comma/semicolon, so it can commit early
    target_top_prob: float = 1.0       # probability of the picked target, for disambiguation
