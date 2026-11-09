"""Ground dictated form values in observed labels; never guess the first field."""
import re
from shared.protocol import Decision


def field_names(element):
    label = element.name.strip().rstrip(' :*')
    names = [label] if label else []
    if element.input_type == 'email' or label.casefold() in {'e-mail', 'email address'}:
        names.append('email')
    if element.input_type == 'tel' or label.casefold() == 'phone number':
        names.append('phone')
    if label.casefold() == 'full name':
        names.append('name')
    return list({name.casefold(): name for name in names}.values())


def form_entry(text, elements):
    if re.match(r'^(?:search|google|look|click|tap|open|go|navigate|scroll|select|choose|pick|find|press|show)\b', text.strip(), re.I):
        return None
    match = re.match(r'^(?:(?:fill(?:\s+in)?|enter|type|write|set)\s+)?(?:(?:my|the)\s+)?(.+)$', text.strip(), re.I)
    if not match:
        return None
    tail = match[1]
    matches = []
    for e in elements:
        if e.role != 'textbox' or not e.name.strip():
            continue
        for label in field_names(e):
            found = re.match(rf'^{re.escape(label)}\s*(?::|\s)\s*(?:to\s+)?(.+)$', tail, re.I)
            if found:
                matches.append((len(label), e, found[1].strip().strip('"“”')))
    if not matches:
        return None
    longest = max(n for n, _, _ in matches)
    matches = [(e, value) for n, e, value in matches if n == longest]
    e, value = matches[0]
    # Spoken punctuation is normalized only for fields with an explicit data type.
    if e.input_type == 'email':
        value = re.sub(r'\s+(?:at|@)\s+', '@', value, flags=re.I)
        value = re.sub(r'\s+dot\s+', '.', value, flags=re.I)
        value = re.sub(r'\s*([@.])\s*', r'\1', value)
    return Decision(True, 'type', target_id=e.id if len(matches) == 1 else None,
                    text=value, ambiguous=len(matches) != 1,
                    target_probabilities={field.id: 1 / len(matches) for field, _ in matches})
