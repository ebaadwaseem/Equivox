from ui.vox import describe_failure, describe_not_found, is_hide_command, is_show_command


# ---- minimize phrases ----

def test_is_hide_command_basic():
    assert is_hide_command("hide vox") is True


def test_is_hide_command_bare_minimize():
    assert is_hide_command("minimize") is True


def test_is_hide_command_minimize_yourself():
    assert is_hide_command("minimize yourself") is True


def test_is_hide_command_vox_minimize_order():
    assert is_hide_command("vox minimize") is True


def test_is_hide_command_other_phrases():
    assert is_hide_command("go away") is True
    assert is_hide_command("go small") is True
    assert is_hide_command("get out of the way") is True
    assert is_hide_command("shrink") is True
    assert is_hide_command("hide yourself") is True


def test_is_hide_command_case_insensitive():
    assert is_hide_command("HIDE VOX") is True


def test_is_hide_command_with_hey_vox_prefix():
    assert is_hide_command("hey vox hide") is True
    assert is_hide_command("vox hide") is True


def test_is_hide_command_with_please():
    assert is_hide_command("please hide vox") is True
    assert is_hide_command("hide vox please") is True


def test_is_hide_command_rejects_unrelated():
    assert is_hide_command("hide the sidebar") is False
    assert is_hide_command("click delete account") is False


# ---- show/restore phrases ----

def test_is_show_command_basic():
    assert is_show_command("show vox") is True


def test_is_show_command_synonyms():
    assert is_show_command("restore") is True
    assert is_show_command("expand") is True
    assert is_show_command("show yourself") is True
    assert is_show_command("come back") is True
    assert is_show_command("vox come back") is True
    assert is_show_command("where are you") is True


def test_is_show_command_bare_name():
    assert is_show_command("vox") is True


def test_is_show_command_with_hey_vox_prefix():
    assert is_show_command("hey vox show vox") is True


def test_is_show_command_with_please():
    assert is_show_command("please come back") is True
    assert is_show_command("show vox please") is True


def test_is_show_command_rejects_unrelated():
    assert is_show_command("show me the menu") is False
    assert is_show_command("coming back from lunch") is False


def test_describe_not_found_includes_target_and_suggestion():
    msg = describe_not_found("refund policy")
    assert "refund policy" in msg
    assert "differently" in msg.lower()


def test_describe_failure_includes_action_and_suggestion():
    msg = describe_failure("click that")
    assert "click that" in msg
    assert "differently" in msg.lower()


def test_idle_hint_returns_only_after_the_cooldown(monkeypatch):
    import asyncio
    from ui import vox
    class Page:
        pass
    async def noop(*args,**kwargs):
        pass
    async def run():
        monkeypatch.setattr(vox,'_wire',noop)
        monkeypatch.setattr(vox,'_apply',noop)
        monkeypatch.setattr(vox,'HINT_COOLDOWN_S',.03)
        page=Page()
        await vox.set(page,'listening','reload tab')
        await vox.set(page,'idle')
        assert vox._current==('idle','reload tab')
        await asyncio.sleep(.05)
        assert vox._current==('idle','')
    asyncio.run(run())
