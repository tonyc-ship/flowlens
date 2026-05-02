"""ExtensionBridge tests — per-tab routing + single-instance lock."""

import os
import tempfile
from unittest import IsolatedAsyncioTestCase, mock

from flowlens.core.bridge import BridgeAlreadyRunningError, ExtensionBridge


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


class BridgeSingleInstanceTest(IsolatedAsyncioTestCase):
    async def test_second_bridge_on_same_port_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"FLOWLENS_APP_DATA_DIR": tmp},
            clear=False,
        ):
            first = ExtensionBridge(port=9877)
            second = ExtensionBridge(port=9877)
            await first.start()
            try:
                with self.assertRaises(BridgeAlreadyRunningError) as ctx:
                    await second.start()
                self.assertEqual(ctx.exception.port, 9877)
                self.assertEqual(ctx.exception.owner.get("pid"), os.getpid())
            finally:
                await first.stop()
