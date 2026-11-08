from backend.context import ConversationContext
from shared.protocol import Decision


def _decision(intent="click", target_id="e1", text=None):
    return Decision(is_command=True, intent=intent, target_id=target_id, text=text)


def test_turns_are_interleaved_oldest_to_newest():
    ctx = ConversationContext()
    ctx.commit("click sign in", _decision(), 'button "Sign in"')
    ctx.commit("search for pizza", _decision("search", None, "pizza"))

    assert [t.said for t in ctx.turns] == ["click sign in", "search for pizza"]
    assert ctx.turns[-1].text == "pizza"
    assert "search for pizza" in ctx.encode()


def test_only_committed_turns_are_stored_and_outcome_updates():
    ctx = ConversationContext()
    turn = ctx.commit("scroll down", _decision("scroll", None))
    assert turn.outcome == "running"
    ctx.finish(turn, "Scrolling down")
    assert ctx.last_turn().outcome == "Scrolling down"


def test_oldest_turn_drops_beyond_six():
    ctx = ConversationContext()
    for i in range(7):
        ctx.commit(f"command {i}", _decision())
    assert len(ctx.turns) == 6
    assert ctx.turns[0].said == "command 1"


def test_previous_page_is_navigation_only_state():
    ctx = ConversationContext()
    ctx.remember_page("https://example.com/old", "Old")
    assert "previous_page" in ctx.encode()
    assert "Example" not in ctx.encode()


def test_executed_prefix_is_sliced_into_the_next_command():
    ctx = ConversationContext()
    ctx.consume(1, "search for pizza")
    remainder, virtual_id = ctx.slice(1, "search for pizza, and scroll down")
    assert remainder == "scroll down"
    assert virtual_id == "1+1"


def test_prefix_must_end_on_a_word_boundary():
    ctx = ConversationContext()
    ctx.consume(1, "search for x")
    assert ctx.slice(1, "search for xy, and scroll down") == (None, 1)


def test_rewritten_prefix_is_ignored():
    ctx = ConversationContext()
    ctx.consume(1, "search for pizza")
    assert ctx.slice(1, "search for pasta, and scroll down") == (None, 1)


def test_too_little_new_text_is_not_a_command():
    ctx = ConversationContext()
    ctx.consume(1, "go back")
    assert ctx.slice(1, "go back please") == (None, 1)


def test_typed_input_is_never_sliced():
    ctx = ConversationContext()
    ctx.consume(None, "go back")
    assert ctx.slice(None, "scroll down") == ("scroll down", None)


def test_successive_clauses_consume_only_the_executed_remainder():
    ctx = ConversationContext()
    ctx.consume(1, "search for pizza,")
    ctx.consume(1, "search for pizza, and scroll down")
    assert ctx.active_prefix == "search for pizza, and scroll down"
    assert ctx.slice(1, "search for pizza, and scroll down, then click menu") == ("click menu", "1+2")
