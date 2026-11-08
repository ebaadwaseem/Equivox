from ui.vox import describe_failure, describe_not_found, is_hide_command, is_show_command


def test_is_hide_command_basic():
    assert is_hide_command("hide vox") is True


def test_is_hide_command_minimize_synonym():
    assert is_hide_command("minimize vox") is True


def test_is_hide_command_case_insensitive():
    assert is_hide_command("HIDE VOX") is True


def test_is_hide_command_rejects_unrelated():
    assert is_hide_command("hide the sidebar") is False
    assert is_hide_command("click delete account") is False


def test_is_show_command_basic():
    assert is_show_command("show vox") is True


def test_is_show_command_synonyms():
    assert is_show_command("restore vox") is True
    assert is_show_command("unhide vox") is True


def test_is_show_command_rejects_unrelated():
    assert is_show_command("show me the menu") is False


def test_describe_not_found_includes_target_and_suggestion():
    msg = describe_not_found("refund policy")
    assert "refund policy" in msg
    assert "differently" in msg.lower()


def test_describe_failure_includes_action_and_suggestion():
    msg = describe_failure("click that")
    assert "click that" in msg
    assert "differently" in msg.lower()
