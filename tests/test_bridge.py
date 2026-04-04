import unittest
from unittest import IsolatedAsyncioTestCase, mock

from flowlens.core.bridge import ExtensionBridge, ensure_extension_connection


class BridgeHelpersTest(unittest.TestCase):
    def test_with_tab_adds_tab_id_when_requested(self) -> None:
        params = ExtensionBridge._with_tab({"x": 1}, 123)
        self.assertEqual(params, {"x": 1, "tabId": 123})

    def test_with_tab_leaves_params_unchanged_without_tab(self) -> None:
        params = ExtensionBridge._with_tab({"x": 1}, None)
        self.assertEqual(params, {"x": 1})


class EnsureExtensionConnectionTest(IsolatedAsyncioTestCase):
    async def test_returns_without_launching_chrome_when_already_connected(self) -> None:
        bridge = ExtensionBridge()
        bridge.wait_for_connection = mock.AsyncMock(return_value=None)

        with mock.patch("flowlens.core.bridge.subprocess.run") as run:
            woke = await ensure_extension_connection(bridge, fast_timeout=0.5, timeout=5)

        self.assertFalse(woke)
        bridge.wait_for_connection.assert_awaited_once()
        run.assert_not_called()

    async def test_launches_chrome_after_fast_timeout(self) -> None:
        bridge = ExtensionBridge()
        bridge.wait_for_connection = mock.AsyncMock(side_effect=[RuntimeError("timeout"), None])

        with mock.patch("flowlens.core.bridge.subprocess.run") as run:
            woke = await ensure_extension_connection(bridge, fast_timeout=0.5, timeout=5)

        self.assertTrue(woke)
        self.assertEqual(bridge.wait_for_connection.await_count, 2)
        run.assert_called_once_with(["open", "-a", "Google Chrome"], check=True)


class TabBridgeTest(IsolatedAsyncioTestCase):
    async def test_tab_bridge_routes_commands_to_specific_tab(self) -> None:
        bridge = ExtensionBridge()
        tab = bridge.tab(321)

        with mock.patch.object(bridge, "find_chat_input", new=mock.AsyncMock(return_value={"found": True})) as mocked:
            result = await tab.find_chat_input(["textarea"])

        mocked.assert_awaited_once_with(["textarea"], tab_id=321)
        self.assertEqual(result, {"found": True})

    async def test_tab_bridge_routes_input_state_to_specific_tab(self) -> None:
        bridge = ExtensionBridge()
        tab = bridge.tab(654)

        with mock.patch.object(
            bridge,
            "get_chat_input_state",
            new=mock.AsyncMock(return_value={"found": True, "empty": False}),
        ) as mocked:
            result = await tab.get_chat_input_state(["textarea"])

        mocked.assert_awaited_once_with(["textarea"], tab_id=654)
        self.assertEqual(result, {"found": True, "empty": False})
