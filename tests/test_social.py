"""
Tests for farmcortes.social module (TikTok + Export).
Uses mocks - no network calls.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
import subprocess
from unittest.mock import patch, MagicMock, mock_open

import pytest

# Ensure farmcortes is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestTikTokOAuth(unittest.TestCase):
    """Tests for TikTok OAuth flow."""

    def setUp(self):
        self.env_patcher = patch.dict(os.environ, {
            "FARMCORTES_TIKTOK_CLIENT_KEY": "test_client_key",
            "FARMCORTES_TIKTOK_CLIENT_SECRET": "test_client_secret",
        })
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    @patch("farmcortes.social.tiktok.requests.post")
    def test_authorize_url_generation(self, mock_post):
        from farmcortes.social.tiktok import authorize_url
        url = authorize_url("https://example.com/callback", state="teststate123")
        assert "client_key=test_client_key" in url
        assert "response_type=code" in url
        assert "scope=video.upload%2Cvideo.publish%2Cuser.info.basic" in url
        assert "redirect_uri=https%3A%2F%2Fexample.com%2Fcallback" in url
        assert "state=teststate123" in url

    @patch("farmcortes.social.tiktok.requests.post")
    def test_exchange_code_success(self, mock_post):
        from farmcortes.social.tiktok import exchange_code, _load_token
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 86400,
            "scope": "video.upload,video.publish,user.info.basic",
            "open_id": "user123",
        }
        mock_post.return_value = mock_response

        token = exchange_code("auth_code_123", "https://example.com/callback")
        assert token["access_token"] == "new_access_token"
        assert token["refresh_token"] == "new_refresh_token"
        assert "expires_at" in token

        # Verify token was saved
        saved = _load_token()
        assert saved["access_token"] == "new_access_token"

    @patch("farmcortes.social.tiktok.requests.post")
    def test_exchange_code_failure(self, mock_post):
        from farmcortes.social.tiktok import exchange_code, TikTokError
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "invalid_code"
        mock_post.return_value = mock_response

        with pytest.raises(TikTokError):
            exchange_code("bad_code", "https://example.com/callback")

    @patch("farmcortes.social.tiktok.requests.post")
    def test_refresh_token_success(self, mock_post):
        from farmcortes.social.tiktok import _refresh_access_token, _save_token
        # Pre-save a token with refresh_token
        _save_token({
            "access_token": "old_access",
            "refresh_token": "valid_refresh",
            "expires_at": 0,  # expired
            "scope": "video.upload,video.publish,user.info.basic",
            "open_id": "user123",
        })

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "refreshed_access",
            "refresh_token": "refreshed_refresh",
            "expires_in": 86400,
            "scope": "video.upload,video.publish,user.info.basic",
            "open_id": "user123",
        }
        mock_post.return_value = mock_response

        from farmcortes.social.tiktok import get_valid_token
        token = get_valid_token()
        assert token == "refreshed_access"

    @patch("farmcortes.social.tiktok.requests.post")
    def test_refresh_token_rate_limit(self, mock_post):
        from farmcortes.social.tiktok import _refresh_access_token, TikTokRateLimitError
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_post.return_value = mock_response

        with pytest.raises(TikTokRateLimitError):
            _refresh_access_token("some_refresh_token")


class TestTikTokVideoUpload(unittest.TestCase):
    """Tests for TikTok video upload (chunking, draft vs direct)."""

    def setUp(self):
        self.env_patcher = patch.dict(os.environ, {
            "FARMCORTES_TIKTOK_CLIENT_KEY": "test_client_key",
            "FARMCORTES_TIKTOK_CLIENT_SECRET": "test_client_secret",
        })
        self.env_patcher.start()

        # Create a temp video file
        self.temp_dir = tempfile.mkdtemp()
        self.video_path = Path(self.temp_dir) / "test_video.mp4"
        self.video_path.write_bytes(b"fake video content" * 1000)  # ~18KB

    def tearDown(self):
        self.env_patcher.stop()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("farmcortes.social.tiktok.requests.post")
    @patch("farmcortes.social.tiktok.requests.put")
    @patch("farmcortes.social.tiktok.get_valid_token")
    def test_upload_draft_flow(self, mock_get_token, mock_put, mock_post):
        from farmcortes.social.tiktok import upload_draft

        mock_get_token.return_value = "valid_access_token"

        # Mock init response
        init_response = MagicMock()
        init_response.status_code = 200
        init_response.json.return_value = {
            "data": {
                "upload_url": "https://upload.tiktok.com/upload/abc123",
                "publish_id": "publish_123",
            }
        }

        # Mock publish (draft) response
        publish_response = MagicMock()
        publish_response.status_code = 200
        publish_response.json.return_value = {
            "data": {"video_id": "video_456", "user_id": "user789"}
        }

        mock_post.side_effect = [init_response, publish_response]
        mock_put.return_value = MagicMock(status_code=200)

        result = upload_draft(str(self.video_path), "Meu corte incrível!")

        assert result["platform"] == "tiktok"
        assert result["mode"] == "draft"
        assert result["publish_id"] == "publish_123"
        assert result["status"] == "draft_created"
        assert "rascunho" in result["message"].lower()

        # Verify chunked upload was called
        assert mock_put.called

    @patch("farmcortes.social.tiktok.requests.post")
    @patch("farmcortes.social.tiktok.requests.put")
    @patch("farmcortes.social.tiktok.get_valid_token")
    def test_upload_direct_post_flow(self, mock_get_token, mock_put, mock_post):
        from farmcortes.social.tiktok import upload_draft

        mock_get_token.return_value = "valid_access_token"

        init_response = MagicMock()
        init_response.status_code = 200
        init_response.json.return_value = {
            "data": {"upload_url": "https://upload.tiktok.com/upload/abc123", "publish_id": "publish_123"}
        }

        publish_response = MagicMock()
        publish_response.status_code = 200
        publish_response.json.return_value = {
            "data": {"video_id": "video_456", "user_id": "user789"}
        }

        mock_post.side_effect = [init_response, publish_response]
        mock_put.return_value = MagicMock(status_code=200)

        result = upload_draft(str(self.video_path), "Meu corte!", direct_post=True, privacy_level="PUBLIC")

        assert result["mode"] == "direct_post"
        assert result["status"] == "published"

    @patch("farmcortes.social.tiktok.requests.post")
    @patch("farmcortes.social.tiktok.get_valid_token")
    def test_upload_init_rate_limit(self, mock_get_token, mock_post):
        from farmcortes.social.tiktok import upload_draft, TikTokRateLimitError

        mock_get_token.return_value = "valid_access_token"
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_post.return_value = mock_response

        with pytest.raises(TikTokRateLimitError):
            upload_draft(str(self.video_path), "Test")

    @patch("farmcortes.social.tiktok.requests.post")
    @patch("farmcortes.social.tiktok.get_valid_token")
    def test_upload_token_expired_error(self, mock_get_token, mock_post):
        from farmcortes.social.tiktok import upload_draft, TikTokTokenError

        mock_get_token.side_effect = TikTokTokenError("Token expirado")

        with pytest.raises(TikTokTokenError):
            upload_draft(str(self.video_path), "Test")

    def test_upload_missing_file(self):
        from farmcortes.social.tiktok import upload_draft, TikTokError

        with pytest.raises(TikTokError):
            upload_draft("/nonexistent/video.mp4", "Test")

    def test_upload_non_mp4(self):
        from farmcortes.social.tiktok import upload_draft, TikTokError

        txt_file = Path(self.temp_dir) / "test.txt"
        txt_file.write_text("not a video")

        with pytest.raises(TikTokError):
            upload_draft(str(txt_file), "Test")


class TestExportPack(unittest.TestCase):
    """Tests for export package building."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Create a synthetic MP4 using ffmpeg testsrc
        self.video_path = Path(self.temp_dir) / "test_1080x1920.mp4"
        self._create_synthetic_video()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_video(self):
        """Create a short synthetic 1080x1920 MP4 for testing."""
        try:
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                "testsrc=duration=5:size=1080x1920:rate=30",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "5",
                str(self.video_path)
            ], capture_output=True, timeout=60, check=True)
        except Exception:
            # Fallback: create minimal file
            self.video_path.write_bytes(b"fake mp4" * 1000)

    def test_build_instagram_pack(self):
        from farmcortes.social.export import build_instagram_pack

        meta = {
            "title": "Momento épico no GTA",
            "description": "Jogador faz jogada incrível",
            "cta": "Siga para mais!",
            "hashtags": ["gta", "gaming", "brasil"],
        }

        zip_path = build_instagram_pack(str(self.video_path), meta)
        assert Path(zip_path).is_file()
        assert zip_path.endswith(".zip")
        assert "instagram" in zip_path

    def test_build_kwai_pack(self):
        from farmcortes.social.export import build_kwai_pack

        meta = {
            "title": "Fail engraçado",
            "description": "Deu ruim kkk",
        }

        zip_path = build_kwai_pack(str(self.video_path), meta)
        assert Path(zip_path).is_file()
        assert zip_path.endswith(".zip")
        assert "kwai" in zip_path

    def test_build_pack_invalid_platform(self):
        from farmcortes.social.export import build_pack

        with pytest.raises(ValueError):
            build_pack(str(self.video_path), {}, "tiktok")

    def test_build_pack_missing_video(self):
        from farmcortes.social.export import build_pack

        with pytest.raises(FileNotFoundError):
            build_pack("/nonexistent/video.mp4", {}, "instagram")

    def test_zip_contents(self):
        from farmcortes.social.export import build_instagram_pack
        import zipfile

        meta = {"title": "Teste", "description": "Desc"}
        zip_path = build_instagram_pack(str(self.video_path), meta)

        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "video.mp4" in names
            assert "caption.txt" in names
            assert "cover.jpg" in names

            # Check caption content
            caption = zf.read("caption.txt").decode("utf-8")
            assert "Título: Teste" in caption
            assert "Descrição: Desc" in caption
            assert "CTA:" in caption
            assert "Hashtags:" in caption
            assert "Instagram Reels" in caption

    def test_caption_duration_warning(self):
        from farmcortes.social.export import build_kwai_pack
        import zipfile

        # Create a longer video (70s) to trigger Kwai warning
        long_video = Path(self.temp_dir) / "long.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "testsrc=duration=70:size=1080x1920:rate=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "70",
            str(long_video)
        ], capture_output=True, timeout=120, check=False)

        meta = {"title": "Vídeo longo", "description": "Teste"}
        zip_path = build_kwai_pack(str(long_video), meta)

        with zipfile.ZipFile(zip_path, "r") as zf:
            caption = zf.read("caption.txt").decode("utf-8")
            assert "AVISO" in caption or "excede" in caption


class TestTikTokConfiguration(unittest.TestCase):
    """Tests for configuration checks."""

    def test_is_configured_false_no_env(self):
        with patch.dict(os.environ, {}, clear=True):
            from farmcortes.social.tiktok import is_configured
            assert is_configured() is False

    def test_is_configured_false_no_token(self):
        with patch.dict(os.environ, {
            "FARMCORTES_TIKTOK_CLIENT_KEY": "key",
            "FARMCORTES_TIKTOK_CLIENT_SECRET": "secret",
        }):
            from farmcortes.social.tiktok import is_configured, TOKEN_PATH
            # Token file doesn't exist - remove if exists from other tests
            if TOKEN_PATH.is_file():
                TOKEN_PATH.unlink()
            assert is_configured() is False


# Run with pytest or unittest
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
