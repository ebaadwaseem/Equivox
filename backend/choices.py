"""Displayed choices bind to one page and scene, with deterministic spoken numbering."""
import time
from dataclasses import dataclass, field

from shared.protocol import Decision, Element


@dataclass
class Choice:
    label: str
    decision: Decision
    detail: str = ""
    element: Element | None = None
    point: tuple[float, float] | None = None


@dataclass
class ChoiceSession:
    page: object
    url: str
    choices: list[Choice]
    revision: object = None
    screenshot: str | None = None
    created_at: float = field(default_factory=time.monotonic)

    def valid(self, page, revision=None):
        return (page is self.page and page.url == self.url and
                time.monotonic() - self.created_at < 60 and
                (self.revision is None or revision == self.revision))

    def rows(self):
        return [dict(label=c.label, detail=c.detail) for c in self.choices]


def ranked_targets(decision, candidates, limit=5):
    """Prefer Jev's candidate probabilities, rather than the first lexical shortlist entries."""
    if decision.target_probabilities:
        candidates = sorted(candidates, key=lambda e: decision.target_probabilities.get(e.id, 0), reverse=True)
    roles = {"type": {"textbox"}, "select": {"select", "option"}}.get(decision.intent)
    eligible = [e for e in candidates if e.name.strip() and (roles is None or e.role in roles)]
    if decision.target_probabilities:
        # Do not pad the list with controls Jev assigned zero probability.
        eligible = [e for e in eligible if decision.target_probabilities.get(e.id, 0) > 0]
        if eligible:
            best = decision.target_probabilities[eligible[0].id]
            eligible = [e for e in eligible if decision.target_probabilities[e.id] >= best * .1]
    return eligible[:limit]


def page_suggestions(elements):
    """Number the open autocomplete popup in its visible order, never toolbar controls."""
    rows = [e for e in elements if e.suggestion_group and e.in_viewport and e.name.strip()]
    return sorted(rows, key=lambda e: (e.y, e.x))
