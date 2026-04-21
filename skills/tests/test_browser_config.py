import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from browser_config import launch_options, launch_options_fallback, context_options, NAVIGATOR_INIT_SCRIPT


def test_launch_options_contains_channel():
    opts = launch_options(headless=False)
    assert opts["channel"] == "chrome"
    assert opts["headless"] is False


def test_launch_options_headless():
    opts = launch_options(headless=True)
    assert opts["headless"] is True


def test_launch_options_has_automation_flag():
    opts = launch_options()
    args = opts.get("args", [])
    assert "--disable-blink-features=AutomationControlled" in args


def test_launch_options_has_no_first_run_flags():
    args = launch_options()["args"]
    assert "--no-first-run" in args
    assert "--no-default-browser-check" in args


def test_launch_options_fallback_no_channel():
    opts = launch_options_fallback()
    assert "channel" not in opts
    assert "--disable-blink-features=AutomationControlled" in opts["args"]


def test_navigator_init_script_hides_webdriver():
    assert "navigator" in NAVIGATOR_INIT_SCRIPT
    assert "webdriver" in NAVIGATOR_INIT_SCRIPT
    assert "undefined" in NAVIGATOR_INIT_SCRIPT


def test_context_options_default_viewport():
    opts = context_options()
    assert opts["viewport"] == {"width": 1280, "height": 800}
    assert "storage_state" not in opts


def test_context_options_with_storage():
    opts = context_options(storage_state="/tmp/state.json")
    assert opts["storage_state"] == "/tmp/state.json"


def test_context_options_custom_viewport():
    opts = context_options(viewport={"width": 1920, "height": 1080})
    assert opts["viewport"]["width"] == 1920
