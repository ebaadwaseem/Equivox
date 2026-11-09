"""Small, exact voice commands. These do not enlarge the model's intent question."""
import re

from shared.protocol import Decision

NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
           "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
           "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
           "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
ORDINALS = {f"{n}{'st' if n == 1 else 'nd' if n == 2 else 'rd' if n == 3 else 'th'}": n for n in range(1, 11)}
KEYS = {"enter": "Enter", "return": "Enter", "backspace": "Backspace", "back space": "Backspace",
        "delete": "Delete", "tab": "Tab", "escape": "Escape", "esc": "Escape", "space": "Space",
        "spacebar": "Space", "space bar": "Space", "up": "ArrowUp", "down": "ArrowDown",
        "left": "ArrowLeft", "right": "ArrowRight", "up arrow": "ArrowUp", "down arrow": "ArrowDown",
        "left arrow": "ArrowLeft", "right arrow": "ArrowRight", "home": "Home", "end": "End",
        "page up": "PageUp", "page down": "PageDown"}


def normalize(text):
    return " ".join(text.lower().strip().rstrip('.!?').split())


def ordinal_reply(text: str, count: int):
    text = normalize(text)
    text = re.sub(r"^quick(?=\s+option\b)", "click", text)
    text = re.sub(r"^(click|choose|select|pick) (?:the )?(first|second|third|fourth|fifth|last) option$", r"option \2", text)
    match = re.fullmatch(r"(?:(?:click|choose|select|pick)\s+)?(?:the\s+)?(?:option\s+)?([\w]+)(?:\s+one)?", text)
    if not match:
        return None
    token = match[1]
    value = count if token == "last" else int(token) if token.isdigit() else NUMBERS.get(token, ORDINALS.get(token))
    return value if value and 1 <= value <= count else None


def explicit_option(text):
    return bool(re.fullmatch(r"(?:(?:quick|click|choose|select|pick)\s+)?(?:the\s+)?(?:option\s+\w+|\w+\s+option)", normalize(text)))


def tab_query(text):
    text = normalize(text)
    if re.fullmatch(r"(?:switch(?:\s+to)?\s+)?(?:the\s+)?(?:next|other) tab", text):
        return "next"
    if re.fullmatch(r"(?:switch(?:\s+to)?\s+)?(?:the\s+)?(?:previous|prev|last) tab", text):
        return "previous"
    match = re.fullmatch(r"(?:switch(?:\s+to)?|go to|open)\s+(?:the\s+)?(?:tab\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten|first|second|third)(?:\s+tab)?", text)
    if not match:
        match = re.fullmatch(r"(?:tab)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)", text)
    if match and "tab" in text.split():
        return str(int(match[1]) if match[1].isdigit() else NUMBERS[match[1]])
    match = re.fullmatch(r"(?:switch(?:\s+to)?|go to)\s+(?:the\s+)?tab\s+(?:with|called|named)\s+(.+)", text)
    if match:
        return match[1]
    match = re.fullmatch(r"(?:switch(?:\s+to)?|go to)\s+(?:the\s+)?(.+?)\s+tab", text)
    return match[1] if match else None


def shortcut(text: str):
    text = normalize(text)
    if text in {"reload", "reload tab", "reload the tab", "reload this tab", "reload page", "reload the page", "refresh", "refresh page", "refresh the page", "refresh tab"}:
        return Decision(True, "reload")
    if text in {"clear", "clear text", "clear the text", "clear all text", "delete text", "delete the text", "remove text", "remove the text", "delete all text", "clear field", "clear the field", "clear this field", "clear the search box"}:
        return Decision(True, "clear")
    if text in {"paste", "paste link", "paste text", "control v", "ctrl v", "command v", "press control v", "press command v"}:
        return Decision(True, "key", text="ControlOrMeta+V")
    if text in {"copy", "copy text", "control c", "ctrl c", "command c"}:
        return Decision(True, "key", text="ControlOrMeta+C")
    if text in {"exit fullscreen", "exit full screen", "exit full-time", "exit full time"}:
        return Decision(True, "key", text="Escape")
    if text in {"select all", "select all text"}:
        return Decision(True, "key", text="ControlOrMeta+A")
    if text in {"backspace", "back space", "delete", "delete character"}:
        return Decision(True, "key", text="Backspace" if text.startswith("back") else "Delete")
    match = re.fullmatch(r"(?:press|hit|click)\s+(?:the\s+)?(.+?)(?:\s+key)?(?:\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+times)?", text)
    if match and (not text.startswith("click ") or " key" in text):
        key = KEYS.get(match[1])
        if not key and re.fullmatch(r"[a-z0-9]", match[1]):
            key = match[1]
        count = int(match[2]) if match[2] and match[2].isdigit() else NUMBERS.get(match[2], 1)
        if key and 1 <= count <= 20:
            return Decision(True, "key", text=key, repeat_count=count)
    if query := tab_query(text):
        return Decision(True, "switch_tab", text=query)
    if text in {"new tab", "open a new tab", "open new tab", "close tab", "close this tab", "close the tab"}:
        return Decision(True, "new_tab" if "new" in text else "close_tab")
    match = re.fullmatch(r"(?:plan (?:a |the )?route to|get directions to|directions to|navigate to)\s+(.+)", text)
    if match and not re.search(r"https?://|\.[a-z]{2,}(?:/|$)", match[1]):
        return Decision(True, "route", text=match[1])
    match = re.fullmatch(r"(?:pan|move)(?:\s+(?:the\s+)?map)?\s+(left|right|up|down|north|south|east|west)", text)
    if match:
        return Decision(True, "map_move", text=match[1])
    return None


def inspection_request(text):
    text = normalize(text)
    if text in {"tell me what you can see", "what can you see", "what do you see", "describe the page", "describe this page", "describe the map"}:
        return "describe"
    if text in {"show options", "show the options", "show map options", "show me the options", "show me my options", "show my options", "what can i click", "show clickable places", "show places on the map"}:
        return "options"
    if re.match(r"^(?:help me |plan |how (?:do|can) i )", text):
        return "plan"
    return None
