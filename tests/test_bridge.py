"""ExtensionBridge tests — connection bootstrap + per-tab routing."""

import unittest
from unittest import IsolatedAsyncioTestCase, mock

from flowlens.core.bridge import ExtensionBridge, ensure_extension_connection


class EnsureExtensionConnectionTest(IsolatedAsyncioTestCase):
    async def test_launches_chrome_only_when_not_already_connected(self) -> None:
        # Already connected: first wait succeeds, Chrome must not be launched.
        bridge = ExtensionBridge()
        bridge.wait_for_connection = mock.AsyncMock(return_value=None)
        with mock.patch("flowlens.core.bridge.subprocess.run") as run:
            woke = await ensure_extension_connection(bridge, fast_timeout=0.1, timeout=1)
        self.assertFalse(woke)
        run.assert_not_called()

        # Not connected: fast wait times out → Chrome is launched → reconnect.
        bridge = ExtensionBridge()
        bridge.wait_for_connection = mock.AsyncMock(
            side_effect=[RuntimeError("timeout"), None]
        )
        with mock.patch("flowlens.core.bridge.subprocess.run") as run:
            woke = await ensure_extension_connection(bridge, fast_timeout=0.1, timeout=1)
        self.assertTrue(woke)
        run.assert_called_once_with(["open", "-a", "Google Chrome"], check=True)


class TabBridgeTest(IsolatedAsyncioTestCase):
    async def test_tab_bridge_routes_calls_with_correct_tab_id(self) -> None:
        bridge = ExtensionBridge()
        tab = bridge.tab(321)
        with mock.patch.object(
            bridge,
            "find_chat_input",
            new=mock.AsyncMock(return_value={"found": True}),
        ) as mocked:
            result = await tab.find_chat_input(["textarea"])
        mocked.assert_awaited_once_with(["textarea"], tab_id=321)
        self.assertEqual(result, {"found": True})
