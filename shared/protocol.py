"""The shared contract. Everyone imports from here. Change it together, not alone."""
from dataclasses import dataclass, field
from typing import Literal, Optional


# ---- Person 1 (audio) produces these ----
@dataclass
class Transcript:
    text: str
    is_final: bool          # False = partial (still talking), True = utterance finished
    confidence: float = 1.0


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


@dataclass
class PageState:
    url: str
    title: str
    elements: list[Element] = field(default_factory=list)
    screenshot_b64: Optional[str] = None   # optional low-res screenshot


# ---- Person 3 (backend/model) produces these ----
Intent = Literal["click", "type", "scroll", "navigate", "back", "forward", "search", "none"]


@dataclass
class Decision:
    is_command: bool
    intent: Intent
    target_id: Optional[str] = None   # Element.id
    text: Optional[str] = None        # text to type / search query / url
    scroll_amount: int = 0            # pixels, negative = up
    complete: bool = True             # is the command finished being spoken?
    confidence: float = 1.0
