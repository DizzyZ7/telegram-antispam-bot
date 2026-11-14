"""Offline music module tests: no live downloads or Telegram writes."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fosgen_music import MusicService, Track, _trusted_download_url


class MusicTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = MusicService("demo")
        self.track = Track("Song", "Artist", "https://mp3d.jamendo.com/download/track/123/mp32/", 120, "123")

    def test_url_allowlist(self):
        self.assertTrue(_trusted_download_url(self.track.download_url))
        for url in (
            "http://mp3d.jamendo.com/a", "https://jamendo.com.evil.org/a",
            "https://127.0.0.1/a", "https://localhost/a",
            "https://user:pass@jamendo.com/a", "https://jamendo.com:8443/a",
        ):
            self.assertFalse(_trusted_download_url(url), url)

    def test_callback_is_user_and_chat_scoped(self):
        keyboard = self.service.offer(7, 10, [self.track])
        token = keyboard.inline_keyboard[0][0].callback_data[3:]
        self.assertEqual(self.service.take(token, 7, 10), self.track)
        self.assertIsNone(self.service.take(token, 8, 10))
        self.assertIsNone(self.service.take(token, 7, 11))

    def test_expired_callback(self):
        keyboard = self.service.offer(7, 10, [self.track])
        token = keyboard.inline_keyboard[0][0].callback_data[3:]
        expires, uid, cid, track = self.service._sessions[token]
        self.service._sessions[token] = (expires - 10000, uid, cid, track)
        self.assertIsNone(self.service.take(token, 7, 10))

    async def test_untrusted_download_rejected_before_network(self):
        track = Track("bad", "bad", "https://evil.example/audio.mp3", 2, "1")
        with self.assertRaises(ValueError):
            await self.service.download(track)

    async def test_search_filters_download_disallowed(self):
        payload = {"headers": {"status": "success"}, "results": [
            {"id": "1", "name": "Allowed", "artist_name": "A", "duration": 90,
             "audiodownload_allowed": True, "audiodownload": self.track.download_url},
            {"id": "2", "name": "Not allowed", "artist_name": "B",
             "audiodownload_allowed": False, "audiodownload": self.track.download_url},
            {"id": "3", "name": "Unsafe", "artist_name": "C",
             "audiodownload_allowed": True, "audiodownload": "https://evil.example/a"},
        ]}
        response = AsyncMock()
        response.raise_for_status = lambda: None
        response.json.return_value = payload
        response.__aenter__.return_value = response
        session = AsyncMock()
        session.get.return_value = response
        session.__aenter__.return_value = session
        with patch("fosgen_music.aiohttp.ClientSession", return_value=session):
            tracks = await self.service.search("artist")
        self.assertEqual([t.title for t in tracks], ["Allowed"])


if __name__ == "__main__":
    unittest.main()
