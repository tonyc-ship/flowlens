from __future__ import annotations

import tempfile
import unittest
from unittest import IsolatedAsyncioTestCase, mock

from flowlens.platforms.xhs.entities import NoteEntity
from flowlens.platforms.xhs.processor import XHSSiteAdapter


class XHSSearchTransitionTest(unittest.TestCase):
    def test_mismatched_visible_keyword_is_rejected(self) -> None:
        state = {
            "page_state": "search_results",
            "input_keyword": "小红书网页版",
            "url_keyword": "南港AR1轮胎",
            "card_count": 16,
            "tabs": ["全部", "图文", "视频"],
            "loading": False,
        }

        self.assertFalse(XHSSiteAdapter._search_transition_ok(state, "南港AR1轮胎"))


class XHSReadNoteTargetValidationTest(IsolatedAsyncioTestCase):
    async def test_read_note_raises_when_opened_note_id_does_not_match_expected_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = XHSSiteAdapter(
                bridge=mock.AsyncMock(),
                ext_bridge=mock.AsyncMock(),
                media=mock.Mock(),
                run_dir=tmp,
            )
            adapter.open_note = mock.AsyncMock(return_value={"ok": True})
            adapter.extract_note = mock.AsyncMock(return_value=NoteEntity(note_id="actual-note"))

            with self.assertRaisesRegex(RuntimeError, "expected note_id=expected-note, got note_id=actual-note"):
                await adapter.read_note(note_id="expected-note")


class XHSCoverOCRTest(IsolatedAsyncioTestCase):
    async def test_extract_note_runs_cover_ocr_even_without_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ext_bridge = mock.AsyncMock()
            ext_bridge.send_command = mock.AsyncMock(return_value={
                "note": {
                    "note_id": "cover-note",
                    "type": "image",
                    "title": "封面含字的帖子",
                    "content": "正文",
                    "image_urls": [
                        "https://sns-webpic-qc.xhscdn.com/cover.jpg",
                        "https://sns-webpic-qc.xhscdn.com/second.jpg",
                    ],
                }
            })
            media = mock.Mock()
            media.download_image = mock.Mock(return_value=b"fake-image")
            media.ocr_image = mock.Mock(return_value="封面上的文字")
            media.detect_media_type = mock.Mock(return_value="image/jpeg")

            adapter = XHSSiteAdapter(
                bridge=mock.AsyncMock(),
                ext_bridge=ext_bridge,
                media=media,
                run_dir=tmp,
            )

            note = await adapter.extract_note(level="lite", include_comments=False, include_media=False)

            self.assertEqual(note.images[0].ocr_text, "封面上的文字")
            self.assertEqual(note.images[1].ocr_text, "")
            self.assertTrue(note.images[0].is_cover)
            media.download_image.assert_called_once_with(
                "https://sns-webpic-qc.xhscdn.com/cover.jpg",
                "https://www.xiaohongshu.com/explore/cover-note",
            )
            media.ocr_image.assert_called_once()


if __name__ == "__main__":
    unittest.main()
