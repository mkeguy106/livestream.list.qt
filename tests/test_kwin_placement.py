"""Tests for KWin-based window placement (Wayland position persistence)."""

import json
import re

from livestream_list.gui.kwin_placement import (
    Rect,
    PlacementRegistry,
    build_config,
    build_script,
    expected_captions,
    is_titlebar_reachable,
    resolve_placement,
)


def _screen(x: int, y: int, w: int, h: int) -> Rect:
    return Rect(x=x, y=y, width=w, height=h)


def test_window_fully_inside_single_screen_is_reachable():
    win = Rect(x=100, y=100, width=460, height=830)
    assert is_titlebar_reachable(win, [_screen(0, 0, 3072, 1728)])


def test_window_straddling_right_edge_is_reachable():
    win = Rect(x=3000, y=100, width=460, height=830)
    assert is_titlebar_reachable(win, [_screen(0, 0, 3072, 1728)])


def test_window_entirely_above_top_edge_is_unreachable():
    win = Rect(x=100, y=-900, width=460, height=830)
    assert not is_titlebar_reachable(win, [_screen(0, 0, 3072, 1728)])


def test_window_body_on_screen_but_titlebar_above_is_unreachable():
    # Only the bottom of the window pokes onto the screen; the titlebar strip
    # sits above y=0, so the user cannot grab it.
    win = Rect(x=100, y=-100, width=460, height=830)
    assert not is_titlebar_reachable(win, [_screen(0, 0, 3072, 1728)])


def test_window_on_second_screen_is_reachable_in_dual_setup():
    win = Rect(x=5484, y=894, width=460, height=830)
    screens = [_screen(0, 0, 3072, 1728), _screen(3072, 0, 3072, 1728)]
    assert is_titlebar_reachable(win, screens)


def test_window_on_unplugged_second_screen_is_unreachable():
    win = Rect(x=5484, y=894, width=460, height=830)
    assert not is_titlebar_reachable(win, [_screen(0, 0, 3072, 1728)])


def test_window_straddling_dual_screen_seam_is_reachable():
    win = Rect(x=3000, y=100, width=460, height=830)
    screens = [_screen(0, 0, 3072, 1728), _screen(3072, 0, 3072, 1728)]
    assert is_titlebar_reachable(win, screens)


def test_no_screens_reported_is_unreachable():
    win = Rect(x=100, y=100, width=460, height=830)
    assert not is_titlebar_reachable(win, [])


# --- caption derivation -----------------------------------------------------
# KWin matches windows by caption. Qt composes a window's caption from its
# title and QGuiApplication.applicationDisplayName(), joined by " — "
# (EM DASH), and suppresses the suffix when the two are equal. Verified
# against KWin on Plasma 6.7.


def test_caption_appends_display_name_with_em_dash():
    assert expected_captions("Chat", "Livestream List (Qt)") == [
        "Chat — Livestream List (Qt)",
        "Chat",
    ]


def test_caption_is_bare_title_when_it_equals_display_name():
    assert expected_captions("Livestream List (Qt)", "Livestream List (Qt)") == [
        "Livestream List (Qt)"
    ]


def test_caption_is_bare_title_when_no_display_name_is_set():
    assert expected_captions("Chat", "") == ["Chat"]


def test_popout_caption_does_not_collide_with_chat_window_caption():
    # The popout window's title is "Chat - <channel>" (hyphen); the tabbed chat
    # window's is "Chat". Exact-match candidates must not overlap, or a popout
    # would be mistaken for the chat window.
    chat = set(expected_captions("Chat", "Livestream List (Qt)"))
    popout = set(expected_captions("Chat - Bob", "Livestream List (Qt)"))
    assert not (chat & popout)


# --- generated KWin script --------------------------------------------------
# The script carries a JSON payload describing which windows to position and
# where to report geometry back to. Only the position is applied: sizing still
# goes through Qt's resize(), which works fine on Wayland.

_DBUS = {
    "service": "app.livestreamlist.Placement.ab12cd34",
    "path": "/Placement",
    "interface": "app.livestreamlist.Placement",
}
_APP_ID = "app.livestreamlist.LivestreamListQt"


def test_config_carries_app_id_so_script_only_touches_our_own_windows():
    # Deliberately NOT the PID: inside a Flatpak sandbox os.getpid() returns
    # the namespaced pid (2), while KWin reports the host pid, so a pid filter
    # never matches and nothing is ever placed. The Wayland app_id is
    # identical inside and outside the sandbox.
    config = build_config(app_id=_APP_ID, captions=[], placements={}, **_DBUS)
    assert config["appId"] == _APP_ID


def test_config_carries_known_captions_so_unrelated_windows_are_ignored():
    config = build_config(
        app_id=_APP_ID, captions=["Livestream List (Qt)", "Chat"], placements={}, **_DBUS
    )
    assert config["captions"] == ["Livestream List (Qt)", "Chat"]


def test_config_carries_dbus_callback_target():
    config = build_config(app_id=_APP_ID, captions=[], placements={}, **_DBUS)
    assert config["service"] == "app.livestreamlist.Placement.ab12cd34"
    assert config["path"] == "/Placement"
    assert config["interface"] == "app.livestreamlist.Placement"


def test_config_maps_caption_to_position_only():
    config = build_config(
        app_id=_APP_ID,
        captions=["Livestream List (Qt)"],
        placements={"Livestream List (Qt)": Rect(x=5484, y=894, width=460, height=830)},
        **_DBUS,
    )
    # Size is deliberately absent - Qt owns it.
    assert config["placements"] == {"Livestream List (Qt)": {"x": 5484, "y": 894}}


def test_config_allows_no_placements_so_geometry_can_still_be_learned():
    # First run has nothing saved, but the script must still load and report
    # geometry back, or we would never learn where the window is.
    config = build_config(app_id=_APP_ID, captions=["Chat"], placements={}, **_DBUS)
    assert config["placements"] == {}


def test_script_embeds_config_as_parseable_json():
    placements = {"Chat \u2014 Livestream List (Qt)": Rect(x=10, y=20, width=400, height=600)}
    script = build_script(
        build_config(
            app_id=_APP_ID,
            captions=["Chat \u2014 Livestream List (Qt)"],
            placements=placements,
            **_DBUS,
        )
    )
    match = re.search(r"^var CONFIG = (.*);$", script, re.MULTILINE)
    assert match, "script must embed its payload as a single JSON literal"
    assert json.loads(match.group(1))["placements"] == {
        "Chat \u2014 Livestream List (Qt)": {"x": 10, "y": 20}
    }


def test_script_escapes_captions_containing_quotes():
    # A window title is user-influenced text; it must not be able to break out
    # of the generated JavaScript.
    caption = 'Evil" + attack() + "'
    script = build_script(
        build_config(
            app_id=_APP_ID,
            captions=[caption],
            placements={caption: Rect(x=1, y=2, width=3, height=4)},
            **_DBUS,
        )
    )
    match = re.search(r"^var CONFIG = (.*);$", script, re.MULTILINE)
    assert caption in json.loads(match.group(1))["placements"]


def test_script_matches_on_app_id_not_pid():
    # Guards the Flatpak regression: a pid-based filter silently placed
    # nothing inside the sandbox.
    script = build_script(build_config(app_id=_APP_ID, captions=[], placements={}, **_DBUS))
    assert "resourceClass" in script
    assert "w.pid" not in script


# --- off-screen fallback policy ---------------------------------------------
# "Reopen where it closed, unless that spot is off screen — then fall back to
# opening where the mouse is." Returning None means "emit no placement", which
# leaves the window to KWin's configured placement policy.

_SCREENS = [_screen(0, 0, 3072, 1728), _screen(3072, 0, 3072, 1728)]


def test_reachable_saved_position_is_used():
    saved = Rect(x=5484, y=894, width=460, height=830)
    assert resolve_placement(saved, _SCREENS) == saved


def test_unreachable_saved_position_defers_to_compositor():
    saved = Rect(x=5484, y=894, width=460, height=830)
    assert resolve_placement(saved, [_screen(0, 0, 3072, 1728)]) is None


def test_absent_saved_position_defers_to_compositor():
    assert resolve_placement(None, _SCREENS) is None


def test_zero_origin_saved_position_defers_to_compositor():
    # (0, 0) is what Wayland reported before this feature existed, so treat a
    # saved origin of exactly (0, 0) as "never actually learned" rather than
    # pinning the window to the top-left corner on upgrade.
    saved = Rect(x=0, y=0, width=460, height=830)
    assert resolve_placement(saved, _SCREENS) is None


# --- registry ---------------------------------------------------------------
# Pure bookkeeping: which windows we want placed, which captions map to which
# window, and the last real geometry KWin told us about. Kept free of Qt so it
# can be tested directly.

_APP = "Livestream List (Qt)"


def _registry() -> PlacementRegistry:
    return PlacementRegistry(display_name=_APP)


def test_registered_window_with_saved_position_is_pending_placement():
    reg = _registry()
    reg.register("main", _APP, Rect(x=5484, y=894, width=460, height=830), _SCREENS)
    assert reg.pending_placements() == {_APP: Rect(x=5484, y=894, width=460, height=830)}


def test_registered_window_with_offscreen_position_is_not_pending():
    reg = _registry()
    reg.register("main", _APP, Rect(x=5484, y=894, width=460, height=830), [_SCREENS[0]])
    assert reg.pending_placements() == {}


def test_report_for_known_caption_resolves_to_its_role():
    reg = _registry()
    reg.register("chat", "Chat", None, _SCREENS)
    role = reg.note_report("Chat — Livestream List (Qt)", Rect(x=10, y=20, width=400, height=600))
    assert role == "chat"


def test_report_for_unknown_caption_is_ignored():
    reg = _registry()
    reg.register("chat", "Chat", None, _SCREENS)
    assert reg.note_report("Some Other Window", Rect(x=1, y=2, width=3, height=4)) is None


def test_popout_report_is_not_attributed_to_the_chat_window():
    reg = _registry()
    reg.register("chat", "Chat", None, _SCREENS)
    role = reg.note_report("Chat - Bob — Livestream List (Qt)", Rect(x=1, y=2, width=3, height=4))
    assert role is None


def test_reported_geometry_is_retrievable_by_role():
    reg = _registry()
    reg.register("main", _APP, None, _SCREENS)
    reg.note_report(_APP, Rect(x=5484, y=894, width=460, height=830))
    assert reg.geometry("main") == Rect(x=5484, y=894, width=460, height=830)


def test_geometry_is_none_before_kwin_has_reported():
    reg = _registry()
    reg.register("main", _APP, None, _SCREENS)
    assert reg.geometry("main") is None


def test_placement_stops_being_pending_once_the_window_has_been_placed():
    # Otherwise reloading the script to place a newly-opened chat window would
    # yank the main window back to its startup position.
    reg = _registry()
    reg.register("main", _APP, Rect(x=5484, y=894, width=460, height=830), _SCREENS)
    reg.note_report(_APP, Rect(x=5484, y=894, width=460, height=830))
    assert reg.pending_placements() == {}


def test_later_registration_keeps_its_own_pending_placement():
    reg = _registry()
    reg.register("main", _APP, Rect(x=5484, y=894, width=460, height=830), _SCREENS)
    reg.note_report(_APP, Rect(x=5484, y=894, width=460, height=830))
    reg.register("chat", "Chat", Rect(x=100, y=200, width=400, height=600), _SCREENS)
    assert reg.pending_placements() == {
        "Chat — Livestream List (Qt)": Rect(x=100, y=200, width=400, height=600)
    }


def test_registry_exposes_position_separately_from_frame_size():
    # KWin reports the *frame* rect (including titlebar); Qt's resize() takes
    # the *client* size. Saving KWin's height and feeding it back to resize()
    # would grow the window by the titlebar height on every launch, so callers
    # get position only.
    reg = _registry()
    reg.register("main", _APP, None, _SCREENS)
    reg.note_report(_APP, Rect(x=5484, y=894, width=460, height=846))
    assert reg.position("main") == (5484, 894)


def test_position_is_none_before_kwin_has_reported():
    reg = _registry()
    reg.register("main", _APP, None, _SCREENS)
    assert reg.position("main") is None
