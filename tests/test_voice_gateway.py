import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import ollama
import requests
from fastapi.testclient import TestClient

# Ensure workspace root is importable regardless of test runner cwd
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import server  # noqa: E402


VALID_COMMAND_DICT = {
    "floor": 1,
    "domain": "light",
    "service": "turn_on",
    "entity_id": "light.living_room",
    "service_data": {},
    "ack_message": "در حال روشن کردن چراغ پذیرایی طبقه اول...",
    "success_message": "چراغ پذیرایی در طبقه اول با موفقیت روشن شد.",
}


def make_fake_whisper_model(transcript: str = "چراغ پذیرایی را روشن کن", language_prob: float = 0.98):
    """Creates an isolated WhisperModel mock returning a fresh segment generator per call."""
    model = MagicMock()

    def _transcribe(*args, **kwargs):
        segments = (SimpleNamespace(text=part) for part in ([transcript] if transcript else []))
        info = SimpleNamespace(language_probability=language_prob)
        return segments, info

    model.transcribe.side_effect = _transcribe
    return model


def make_chat_response(content: str, as_dict: bool = False):
    """Builds either an ollama.ChatResponse object or legacy dict response."""
    if as_dict:
        return {"message": {"role": "assistant", "content": content}}
    return ollama.ChatResponse(message=ollama.Message(role="assistant", content=content))


class GatewayTestBase(unittest.TestCase):
    """Base TestCase isolating TEMP_AUDIO_DIR, LATEST_AUDIO_PATH, and module globals."""

    def setUp(self) -> None:
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_ctx.cleanup)
        self.temp_dir = Path(self._tmp_ctx.name)
        self.latest_audio_path = self.temp_dir / "latest_response.mp3"

        orig_temp_dir = server.TEMP_AUDIO_DIR
        orig_latest_path = server.LATEST_AUDIO_PATH
        orig_whisper = server.whisper_model
        orig_ollama_model = server.OLLAMA_MODEL
        orig_tts_timeout = server.TTS_TIMEOUT_SECONDS
        orig_max_upload = server.MAX_AUDIO_UPLOAD_BYTES
        orig_voice_token = server.VOICE_API_TOKEN
        orig_allowed_entities = set(server.ALLOWED_ENTITIES)

        def _restore_globals():
            server.TEMP_AUDIO_DIR = orig_temp_dir
            server.LATEST_AUDIO_PATH = orig_latest_path
            server.whisper_model = orig_whisper
            server.OLLAMA_MODEL = orig_ollama_model
            server.TTS_TIMEOUT_SECONDS = orig_tts_timeout
            server.MAX_AUDIO_UPLOAD_BYTES = orig_max_upload
            server.VOICE_API_TOKEN = orig_voice_token
            server.ALLOWED_ENTITIES = orig_allowed_entities

        self.addCleanup(_restore_globals)

        server.TEMP_AUDIO_DIR = self.temp_dir
        server.LATEST_AUDIO_PATH = self.latest_audio_path
        server.OLLAMA_MODEL = "qwen2.5:7b"
        server.VOICE_API_TOKEN = ""
        server.ALLOWED_ENTITIES = set()
        server.whisper_model = make_fake_whisper_model()

        # Do not enter context manager so FastAPI lifespan does not download WhisperModel
        self.client = TestClient(server.app, raise_server_exceptions=False)

    def _mock_ha_success(self):
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.raise_for_status.return_value = None
        return patch.object(server.requests, "post", return_value=fake_resp)

    def _mock_ollama_ready(self, response_content: str | None = None, model_name: str = "qwen2.5:7b"):
        content = response_content if response_content is not None else json.dumps(VALID_COMMAND_DICT, ensure_ascii=False)
        list_resp = ollama.ListResponse(models=[ollama.ListResponse.Model(model=model_name)])
        chat_resp = make_chat_response(content)
        return (
            patch.object(server.ollama, "list", return_value=list_resp),
            patch.object(server.ollama, "chat", return_value=chat_resp),
        )

    def _mock_tts_success(self, audio_bytes: bytes = b"ID3-FAKE-MP3-BYTES"):
        async def _fake_save(self_comm, path_str):
            Path(path_str).write_bytes(audio_bytes)

        return patch.object(server.edge_tts.Communicate, "save", new=_fake_save)


class TestSecurityHeadersCorsAndAuth(GatewayTestBase):
    def test_default_cors_settings_are_restricted_and_no_credentials(self):
        self.assertEqual(server.CORS_ORIGINS, ["http://localhost:8000", "http://127.0.0.1:8000"])
        self.assertFalse(server.CORS_ALLOW_CREDENTIALS)

    def test_html_root_includes_security_headers_and_csp(self):
        resp = self.client.get("/", headers={"Origin": "http://localhost:8000"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(resp.headers.get("x-frame-options"), "DENY")
        self.assertEqual(resp.headers.get("referrer-policy"), "strict-origin-when-cross-origin")
        csp = resp.headers.get("content-security-policy", "")
        self.assertIn("default-src 'self'", csp)
        self.assertIn("img-src 'self' data:;", csp)
        self.assertIn("script-src 'self' 'unsafe-inline'", csp)
        self.assertIn("media-src 'self' blob:", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "http://localhost:8000")
        self.assertNotIn("access-control-allow-credentials", resp.headers)

    def test_cors_blocks_disallowed_origin_even_on_unhandled_500_json(self):
        with patch.object(Path, "read_text", side_effect=RuntimeError("disk exploded")):
            allowed_resp = self.client.get("/", headers={"Origin": "http://localhost:8000"})
            disallowed_resp = self.client.get("/", headers={"Origin": "https://evil.example"})

        self.assertEqual(allowed_resp.status_code, 500)
        self.assertEqual(allowed_resp.headers.get("access-control-allow-origin"), "http://localhost:8000")
        self.assertEqual(allowed_resp.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(allowed_resp.headers.get("x-frame-options"), "DENY")
        self.assertNotIn("content-security-policy", allowed_resp.headers)
        self.assertEqual(allowed_resp.json()["status"], "error")

        self.assertEqual(disallowed_resp.status_code, 500)
        self.assertNotIn("access-control-allow-origin", disallowed_resp.headers)
        self.assertEqual(disallowed_resp.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(disallowed_resp.json()["status"], "error")

    def test_bearer_auth_enforced_when_voice_api_token_configured(self):
        server.VOICE_API_TOKEN = "super-secret-gateway-token"
        self.latest_audio_path.write_bytes(b"MP3DATA")

        # HTML UI and OPTIONS remain reachable without token
        root_resp = self.client.get("/")
        self.assertEqual(root_resp.status_code, 200)

        preflight_resp = self.client.options(
            "/process-audio",
            headers={
                "Origin": "http://localhost:8000",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(preflight_resp.status_code, 200)

        # Missing or wrong token returns 401 UNAUTHORIZED JSON with security headers
        for bad_headers in [
            {"Origin": "http://localhost:8000"},
            {"Authorization": "Bearer wrong-token", "Origin": "http://localhost:8000"},
            {"Authorization": "Basic dXNlcjpwYXNz"},
        ]:
            unauth_post = self.client.post(
                "/process-audio",
                headers=bad_headers,
                files={"audio": ("cmd.webm", b"AUDIO", "audio/webm")},
            )
            self.assertEqual(unauth_post.status_code, 401)
            self.assertEqual(unauth_post.headers.get("x-content-type-options"), "nosniff")
            self.assertEqual(unauth_post.headers.get("x-frame-options"), "DENY")
            body = unauth_post.json()
            self.assertEqual(body["status"], "error")
            self.assertEqual(body["stage"], "auth")
            self.assertEqual(body["error_code"], "UNAUTHORIZED")
            self.assertIsNone(body["requested_model"])

            unauth_get = self.client.get("/play-response", headers=bad_headers)
            self.assertEqual(unauth_get.status_code, 401)
            self.assertEqual(unauth_get.json()["error_code"], "UNAUTHORIZED")

        # Valid Bearer token succeeds on both /process-audio and /play-response
        p_list, p_chat = self._mock_ollama_ready()
        with p_list, p_chat, self._mock_ha_success(), self._mock_tts_success():
            ok_post = self.client.post(
                "/process-audio",
                headers={"Authorization": "Bearer super-secret-gateway-token"},
                files={"audio": ("cmd.webm", b"AUDIO", "audio/webm")},
            )
        self.assertEqual(ok_post.status_code, 200)

        ok_get = self.client.get(
            "/play-response",
            headers={"Authorization": "Bearer super-secret-gateway-token"},
        )
        self.assertEqual(ok_get.status_code, 200)
        self.assertEqual(ok_get.headers.get("content-type"), "audio/mpeg")


class TestGlobalJsonErrorHandlingAndUploads(GatewayTestBase):
    def test_build_error_payload_defaults_requested_model_to_none_on_non_llm_stages(self):
        payload = server.build_error_payload(detail="bad input", stage="upload", error_code="EMPTY_AUDIO_FILE")
        self.assertIsNone(payload["requested_model"])
        self.assertEqual(payload["available_models"], [])

    def test_unhandled_exception_escaping_root_endpoint_returns_json_500(self):
        with patch.object(Path, "read_text", side_effect=RuntimeError("disk exploded")):
            resp = self.client.get("/")

        self.assertEqual(resp.status_code, 500)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/json"))
        self.assertNotEqual(resp.text, "Internal Server Error")
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["error_code"], "INTERNAL_SERVER_ERROR")
        self.assertEqual(body["stage"], "internal")
        self.assertIsNone(body["requested_model"])

    def test_unhandled_exception_escaping_play_response_returns_json_500(self):
        broken_dir = MagicMock(spec=Path)
        broken_dir.resolve.side_effect = RuntimeError("filesystem failure")
        server.TEMP_AUDIO_DIR = broken_dir

        resp = self.client.get("/play-response?id=abcd1234")

        self.assertEqual(resp.status_code, 500)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/json"))
        self.assertNotEqual(resp.text, "Internal Server Error")
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["error_code"], "INTERNAL_SERVER_ERROR")
        self.assertIsNone(body["requested_model"])

    def test_unhandled_exception_in_pipeline_stage_returns_json_500_sanitized(self):
        with patch.object(server, "transcribe_persian_audio", side_effect=RuntimeError("C:\\secret\\trace")):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"RIFF-FAKE-AUDIO", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 500)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/json"))
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "stt")
        self.assertEqual(body["error_code"], "STT_UNEXPECTED_ERROR")
        self.assertNotIn("secret", body["detail"])
        self.assertIsNone(body["requested_model"])

    def test_unhandled_exception_in_outer_process_audio_block_returns_json_500(self):
        p_list, p_chat = self._mock_ollama_ready()
        with p_list, p_chat, self._mock_ha_success(), self._mock_tts_success():
            with patch.object(server.LLMCommandSchema, "model_dump", side_effect=RuntimeError("dump failure")):
                resp = self.client.post(
                    "/process-audio",
                    files={"audio": ("cmd.webm", b"RIFF-FAKE-AUDIO", "audio/webm")},
                )

        self.assertEqual(resp.status_code, 500)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/json"))
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "internal")
        self.assertEqual(body["error_code"], "PROCESS_AUDIO_INTERNAL_ERROR")

    def test_missing_audio_field_returns_422_validation_error_json(self):
        resp = self.client.post("/process-audio")

        self.assertEqual(resp.status_code, 422)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/json"))
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["error_code"], "VALIDATION_ERROR")
        self.assertEqual(body["stage"], "upload")
        self.assertIsNone(body["requested_model"])
        self.assertTrue(len(body["detail"]) > 0)

    def test_empty_uploaded_audio_returns_400_empty_audio_file_json(self):
        resp = self.client.post(
            "/process-audio",
            files={"audio": ("empty.webm", b"", "audio/webm")},
        )

        self.assertEqual(resp.status_code, 400)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/json"))
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["error_code"], "EMPTY_AUDIO_FILE")
        self.assertEqual(body["stage"], "upload")
        self.assertIsNone(body["requested_model"])

    def test_upload_exceeding_max_bytes_on_first_chunk_returns_413_without_temp_leak(self):
        server.MAX_AUDIO_UPLOAD_BYTES = 16
        resp = self.client.post(
            "/process-audio",
            files={"audio": ("oversized.webm", b"X" * 32, "audio/webm")},
        )

        self.assertEqual(resp.status_code, 413)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "upload")
        self.assertEqual(body["error_code"], "PAYLOAD_TOO_LARGE")
        self.assertIsNone(body["requested_model"])
        self.assertEqual(list(self.temp_dir.iterdir()), [])

    def test_streaming_upload_exceeding_max_bytes_across_chunks_returns_413_and_cleans_temp(self):
        # 65536 is chunk size; 70000 forces streaming across 2 chunks
        server.MAX_AUDIO_UPLOAD_BYTES = 70000
        resp = self.client.post(
            "/process-audio",
            files={"audio": ("stream_oversized.wav", b"A" * 80000, "audio/wav")},
        )

        self.assertEqual(resp.status_code, 413)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "upload")
        self.assertEqual(body["error_code"], "PAYLOAD_TOO_LARGE")
        self.assertEqual(list(self.temp_dir.iterdir()), [])

    def test_disallowed_audio_suffix_falls_back_to_webm(self):
        captured_paths = []
        fake_whisper = MagicMock()

        def _capture_transcribe(path_str, **kwargs):
            captured_paths.append(Path(path_str))
            return iter([SimpleNamespace(text="سلام")]), SimpleNamespace(language_probability=0.95)

        fake_whisper.transcribe.side_effect = _capture_transcribe
        server.whisper_model = fake_whisper

        p_list, p_chat = self._mock_ollama_ready()
        with p_list, p_chat, self._mock_ha_success(), self._mock_tts_success():
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("malicious.exe", b"FAKE_AUDIO_BYTES", "application/octet-stream")},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(captured_paths), 1)
        self.assertEqual(captured_paths[0].suffix, ".webm")
        # Ensure temp input file was cleaned up
        self.assertFalse(captured_paths[0].exists())

    def test_empty_transcript_returns_422_empty_transcript_json(self):
        server.whisper_model = make_fake_whisper_model(transcript="   ")

        resp = self.client.post(
            "/process-audio",
            files={"audio": ("silence.webm", b"\x1a\x45\xdf\xa3silence", "audio/webm")},
        )

        self.assertEqual(resp.status_code, 422)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/json"))
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["error_code"], "EMPTY_TRANSCRIPT")
        self.assertEqual(body["stage"], "stt")
        self.assertIsNone(body["requested_model"])

    def test_unknown_route_returns_404_json_not_plain_text(self):
        resp = self.client.get("/does-not-exist")

        self.assertEqual(resp.status_code, 404)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/json"))
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["error_code"], "HTTP_404")


class TestSTTCudaToCpuFallback(GatewayTestBase):
    def test_is_cuda_runtime_error_narrowed_markers(self):
        self.assertTrue(server._is_cuda_runtime_error(RuntimeError("Library cublas64_12.dll not found")))
        self.assertTrue(server._is_cuda_runtime_error(RuntimeError("libcudnn_ops_infer.so.8 missing")))
        self.assertTrue(server._is_cuda_runtime_error(RuntimeError("CUDA driver version insufficient")))
        self.assertTrue(server._is_cuda_runtime_error(RuntimeError("nvcuda.dll load error")))
        self.assertTrue(server._is_cuda_runtime_error(RuntimeError("cudart64_12.dll missing")))
        # Generic "library" alone must NOT trigger CUDA->CPU fallback
        self.assertFalse(server._is_cuda_runtime_error(RuntimeError("Audio codec library failed to open")))

    def test_cublas_runtime_error_falls_back_to_cpu_int8_and_succeeds(self):
        cuda_model = MagicMock()
        cuda_model.transcribe.side_effect = RuntimeError(
            "Library cublas64_12.dll is not found or cannot be loaded"
        )
        server.whisper_model = cuda_model

        cpu_model = make_fake_whisper_model(transcript="چراغ آشپزخانه را خاموش کن")

        p_list, p_chat = self._mock_ollama_ready()
        with (
            patch.object(server, "WhisperModel", return_value=cpu_model) as mock_whisper_cls,
            p_list,
            p_chat,
            self._mock_ha_success(),
            self._mock_tts_success(),
        ):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"WEBM_AUDIO_BYTES", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["transcript"], "چراغ آشپزخانه را خاموش کن")
        mock_whisper_cls.assert_called_once_with(
            server.WHISPER_MODEL_SIZE, device="cpu", compute_type="int8"
        )
        self.assertIs(server.whisper_model, cpu_model)

    def test_cuda_error_raised_during_segment_generator_iteration_triggers_cpu_fallback(self):
        cuda_model = MagicMock()

        def _lazy_cuda_failure(*args, **kwargs):
            def _failing_gen():
                raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                yield  # pragma: no cover

            return _failing_gen(), SimpleNamespace(language_probability=0.0)

        cuda_model.transcribe.side_effect = _lazy_cuda_failure
        server.whisper_model = cuda_model
        cpu_model = make_fake_whisper_model(transcript="دمای کولر را ۲۴ بگذار")

        audio_file = self.temp_dir / "sample.webm"
        audio_file.write_bytes(b"AUDIO")

        with patch.object(server, "WhisperModel", return_value=cpu_model) as mock_whisper_cls:
            transcript = server.transcribe_persian_audio(audio_file)

        self.assertEqual(transcript, "دمای کولر را ۲۴ بگذار")
        mock_whisper_cls.assert_called_once_with(
            server.WHISPER_MODEL_SIZE, device="cpu", compute_type="int8"
        )

    def test_generic_library_error_does_not_trigger_cpu_fallback_and_sanitizes_detail(self):
        broken_model = MagicMock()
        broken_model.transcribe.side_effect = ValueError(
            "Audio library failed on C:\\Users\\iliag\\secret.webm"
        )
        server.whisper_model = broken_model

        with patch.object(server, "WhisperModel") as mock_whisper_cls:
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("bad.webm", b"NOT_VALID_AUDIO", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 500)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/json"))
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "stt")
        self.assertEqual(body["error_code"], "STT_TRANSCRIPTION_FAILED")
        self.assertNotIn("C:\\Users", body["detail"])
        self.assertNotIn("secret.webm", body["detail"])
        mock_whisper_cls.assert_not_called()

    def test_uninitialized_whisper_model_returns_503_json_stage_stt(self):
        server.whisper_model = None

        resp = self.client.post(
            "/process-audio",
            files={"audio": ("cmd.webm", b"WEBM_AUDIO", "audio/webm")},
        )

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "stt")
        self.assertEqual(body["error_code"], "STT_MODEL_NOT_INITIALIZED")

    def test_cuda_fallback_when_cpu_retry_also_fails_returns_500_json(self):
        cuda_model = MagicMock()
        cuda_model.transcribe.side_effect = RuntimeError("cublas64_12.dll missing")
        server.whisper_model = cuda_model

        broken_cpu_model = MagicMock()
        broken_cpu_model.transcribe.side_effect = RuntimeError("C:\\internal\\cpu_fail")

        with patch.object(server, "WhisperModel", return_value=broken_cpu_model):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"WEBM_AUDIO", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 500)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "stt")
        self.assertEqual(body["error_code"], "STT_TRANSCRIPTION_FAILED")
        self.assertNotIn("cpu_fail", body["detail"])

    def test_lifespan_startup_falls_back_to_cpu_when_auto_device_fails(self):
        cpu_model = make_fake_whisper_model("سلام")

        def _fake_whisper_init(size, device="auto", compute_type="auto"):
            if device == "auto":
                raise RuntimeError("CUDA driver version is insufficient")
            return cpu_model

        with patch.object(server, "WhisperModel", side_effect=_fake_whisper_init) as mock_cls:
            with TestClient(server.app) as client:
                self.assertIs(server.whisper_model, cpu_model)
                resp = client.get("/")
                self.assertEqual(resp.status_code, 200)

        self.assertEqual(mock_cls.call_count, 2)
        self.assertEqual(mock_cls.call_args_list[0].kwargs["device"], "auto")
        self.assertEqual(mock_cls.call_args_list[1].kwargs, {"device": "cpu", "compute_type": "int8"})
        self.assertFalse(self.temp_dir.exists())


class TestOllamaReachabilityModelDiscoveryAndParsing(GatewayTestBase):
    def test_default_ollama_helpers_use_bounded_timeout_client(self):
        fake_client = MagicMock()
        fake_client.list.return_value = ollama.ListResponse(
            models=[ollama.ListResponse.Model(model="qwen2.5:7b")]
        )
        fake_client.chat.return_value = make_chat_response(json.dumps(VALID_COMMAND_DICT, ensure_ascii=False))

        with patch.object(server.ollama, "Client", return_value=fake_client) as mock_client_cls:
            cmd = server.reason_with_qwen("چراغ را روشن کن")

        self.assertEqual(cmd.entity_id, "light.living_room")
        self.assertEqual(mock_client_cls.call_args_list[0].kwargs["timeout"], server.OLLAMA_LIST_TIMEOUT)
        self.assertEqual(mock_client_cls.call_args_list[1].kwargs["timeout"], server.OLLAMA_CHAT_TIMEOUT)
        self.assertEqual(fake_client.close.call_count, 2)

        # Fallback to _client.close() when client has no top-level .close()
        nested_http_client = MagicMock()
        server._close_ollama_client(SimpleNamespace(_client=nested_http_client))
        nested_http_client.close.assert_called_once_with()

        # Exceptions from close() must be swallowed safely
        failing_client = MagicMock()
        failing_client.close.side_effect = RuntimeError("close error")
        server._close_ollama_client(failing_client)

    def test_ollama_unreachable_returns_503_json_and_sanitizes_internal_errors(self):
        with patch.object(
            server.ollama,
            "list",
            side_effect=ConnectionError("[WinError 10061] No connection could be made to 127.0.0.1:11434"),
        ):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 503)
        self.assertTrue(resp.headers.get("content-type", "").startswith("application/json"))
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "llm")
        self.assertEqual(body["error_code"], "OLLAMA_UNREACHABLE")
        self.assertIs(body["ollama_running"], False)
        self.assertEqual(body["requested_model"], "qwen2.5:7b")
        self.assertEqual(body["available_models"], [])
        self.assertIn("Ollama", body["detail"])
        self.assertNotIn("WinError", body["detail"])

    def test_ollama_request_error_during_chat_returns_503_json(self):
        list_resp = ollama.ListResponse(models=[ollama.ListResponse.Model(model="qwen2.5:7b")])
        with (
            patch.object(server.ollama, "list", return_value=list_resp),
            patch.object(server.ollama, "chat", side_effect=ollama.RequestError("socket closed")),
        ):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["error_code"], "OLLAMA_UNREACHABLE")
        self.assertIs(body["ollama_running"], False)
        self.assertNotIn("socket closed", body["detail"])

    def test_requested_model_missing_list_response_object_returns_404_json(self):
        server.OLLAMA_MODEL = "qwen2.5:7b"
        server.OLLAMA_FALLBACK_ENABLED = False
        list_resp = ollama.ListResponse(models=[ollama.ListResponse.Model(model="qwen3.5:4b")])

        with patch.object(server.ollama, "list", return_value=list_resp):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "llm")
        self.assertEqual(body["error_code"], "MODEL_NOT_FOUND")
        self.assertIs(body["ollama_running"], True)
        self.assertEqual(body["requested_model"], "qwen2.5:7b")
        self.assertEqual(body["available_models"], ["qwen3.5:4b"])

    def test_requested_model_missing_dict_response_returns_404_json(self):
        server.OLLAMA_MODEL = "qwen2.5:7b"
        server.OLLAMA_FALLBACK_ENABLED = False
        dict_resp = {"models": [{"name": "qwen3.5:4b"}]}

        with patch.object(server.ollama, "list", return_value=dict_resp):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "llm")
        self.assertEqual(body["error_code"], "MODEL_NOT_FOUND")
        self.assertIs(body["ollama_running"], True)
        self.assertEqual(body["requested_model"], "qwen2.5:7b")
        self.assertEqual(body["available_models"], ["qwen3.5:4b"])

    def test_model_without_tag_matches_latest_tag(self):
        self.assertTrue(server._is_model_available("qwen3", ["qwen3:latest"]))
        self.assertFalse(server._is_model_available("qwen3", ["qwen3:4b"]))
        self.assertFalse(server._is_model_available("qwen3:4b", ["qwen3:latest"]))

        server.OLLAMA_MODEL = "qwen3"
        dict_resp = {"models": [{"name": "qwen3:latest"}]}
        chat_resp = make_chat_response(json.dumps(VALID_COMMAND_DICT, ensure_ascii=False), as_dict=True)

        with (
            patch.object(server.ollama, "list", return_value=dict_resp),
            patch.object(server.ollama, "chat", return_value=chat_resp),
        ):
            cmd = server.reason_with_qwen("چراغ پذیرایی را روشن کن")

        self.assertEqual(cmd.entity_id, "light.living_room")
        self.assertEqual(cmd.floor, 1)

    def test_normalize_floor_supports_persian_arabic_digits_and_floats(self):
        self.assertEqual(server._normalize_floor(1), 1)
        self.assertEqual(server._normalize_floor(2), 2)
        self.assertEqual(server._normalize_floor(2.0), 2)
        self.assertEqual(server._normalize_floor("2"), 2)
        self.assertEqual(server._normalize_floor("۲"), 2)
        self.assertEqual(server._normalize_floor("٢"), 2)
        self.assertEqual(server._normalize_floor("۱"), 1)
        self.assertEqual(server._normalize_floor("١"), 1)
        self.assertEqual(server._normalize_floor(True), 1)
        self.assertEqual(server._normalize_floor("3"), 1)
        self.assertEqual(server._normalize_floor(2.5), 1)

    def test_extract_json_and_reason_with_qwen_support_all_llm_output_formats(self):
        payload_json = json.dumps(
            {**VALID_COMMAND_DICT, "floor": "۲", "entity_id": "light.kitchen"},
            ensure_ascii=False,
        )
        cases = {
            "raw_json": payload_json,
            "json_markdown_fence": f"```json\n{payload_json}\n```",
            "plain_markdown_fence": f"```\n{payload_json}\n```",
            "think_block_plus_fenced_json": (
                '<think>بررسی طبقه و {invalid_inside_think}</think>\n'
                f"```json\n{payload_json}\n```"
            ),
            "commentary_with_incidental_empty_braces_before_and_after": (
                "توضیح {} و نمونه دیگر {\"note\": \"ignored\"} قبل از پاسخ نهایی:\n"
                f"{payload_json}\n"
                "امیدوارم کمک کرده باشد."
            ),
        }

        list_resp = ollama.ListResponse(models=[ollama.ListResponse.Model(model="qwen2.5:7b")])

        for name, raw_text in cases.items():
            with self.subTest(format=name):
                extracted = server.extract_json_from_llm(raw_text)
                self.assertEqual(extracted["entity_id"], "light.kitchen")

                for as_dict in (False, True):
                    with (
                        patch.object(server.ollama, "list", return_value=list_resp),
                        patch.object(
                            server.ollama,
                            "chat",
                            return_value=make_chat_response(raw_text, as_dict=as_dict),
                        ),
                    ):
                        cmd = server.reason_with_qwen("چراغ آشپزخانه طبقه دو را روشن کن")
                    self.assertEqual(cmd.floor, 2)
                    self.assertEqual(cmd.entity_id, "light.kitchen")

    def test_invalid_json_from_ollama_returns_502_llm_invalid_json(self):
        p_list, p_chat = self._mock_ollama_ready(response_content="متاسفم، نتوانستم دستور را بفهمم.")
        with p_list, p_chat:
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "llm")
        self.assertEqual(body["error_code"], "LLM_INVALID_JSON")
        self.assertIs(body["ollama_running"], True)

    def test_invalid_pydantic_schema_from_ollama_returns_502_llm_invalid_json(self):
        incomplete_json = json.dumps({"floor": 1, "domain": "light"})
        p_list, p_chat = self._mock_ollama_ready(response_content=incomplete_json)
        with p_list, p_chat:
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "llm")
        self.assertEqual(body["error_code"], "LLM_INVALID_JSON")
        self.assertIs(body["ollama_running"], True)

    def test_json_array_from_ollama_returns_502_llm_invalid_json(self):
        p_list, p_chat = self._mock_ollama_ready(response_content='[{"floor": 1}]')
        with p_list, p_chat:
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["error_code"], "LLM_INVALID_JSON")


class TestHomeAssistantValidationAndDispatch(GatewayTestBase):
    def test_llm_schema_rejects_disallowed_domains_services_entities_and_service_data(self):
        invalid_payloads = [
            ("disallowed_domain", {**VALID_COMMAND_DICT, "domain": "script", "entity_id": "script.evil"}),
            ("disallowed_service_for_domain", {**VALID_COMMAND_DICT, "domain": "light", "service": "set_temperature"}),
            ("cross_domain_entity_id", {**VALID_COMMAND_DICT, "domain": "light", "entity_id": "switch.living_room"}),
            ("traversal_entity_id", {**VALID_COMMAND_DICT, "entity_id": "light.bad/../x"}),
            ("forbidden_entity_id_in_service_data", {**VALID_COMMAND_DICT, "service_data": {"entity_id": "light.other"}}),
            ("forbidden_empty_entity_id_in_service_data", {**VALID_COMMAND_DICT, "service_data": {"entity_id": ""}}),
            ("forbidden_area_id_in_service_data", {**VALID_COMMAND_DICT, "service_data": {"area_id": "all"}}),
            ("unsafe_service_data_key", {**VALID_COMMAND_DICT, "service_data": {"url": "http://evil"}}),
            ("temperature_too_low", {**VALID_COMMAND_DICT, "domain": "climate", "service": "set_temperature", "entity_id": "climate.main", "service_data": {"temperature": 4.9}}),
            ("temperature_too_high", {**VALID_COMMAND_DICT, "domain": "climate", "service": "set_temperature", "entity_id": "climate.main", "service_data": {"temperature": 40.1}}),
            ("temperature_boolean", {**VALID_COMMAND_DICT, "domain": "climate", "service": "set_temperature", "entity_id": "climate.main", "service_data": {"temperature": True}}),
        ]

        for case_name, raw_cmd in invalid_payloads:
            with self.subTest(case=case_name):
                p_list, p_chat = self._mock_ollama_ready(json.dumps(raw_cmd, ensure_ascii=False))
                with p_list, p_chat:
                    resp = self.client.post(
                        "/process-audio",
                        files={"audio": ("cmd.webm", b"AUDIO", "audio/webm")},
                    )
                self.assertEqual(resp.status_code, 502)
                self.assertEqual(resp.json()["error_code"], "LLM_INVALID_JSON")

    def test_allowed_entities_enforced_when_configured(self):
        server.ALLOWED_ENTITIES = {"light.living_room"}
        disallowed_cmd = {**VALID_COMMAND_DICT, "entity_id": "light.kitchen"}
        p_list, p_chat = self._mock_ollama_ready(json.dumps(disallowed_cmd, ensure_ascii=False))
        with p_list, p_chat:
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO", "audio/webm")},
            )
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["error_code"], "LLM_INVALID_JSON")

        # Also enforced directly inside execute_home_assistant_service
        schema_obj = server.LLMCommandSchema.model_construct(**disallowed_cmd)
        with self.assertRaises(ValueError):
            server.execute_home_assistant_service(schema_obj)

    def test_non_finite_float_rejected_by_schema(self):
        with self.assertRaises(Exception):
            server.LLMCommandSchema(
                **{
                    **VALID_COMMAND_DICT,
                    "domain": "climate",
                    "service": "set_temperature",
                    "entity_id": "climate.main",
                    "service_data": {"temperature": math.nan},
                }
            )

    def test_floor_2_dispatches_to_8502_with_no_redirects_and_safe_temperature_bounds(self):
        cmd_floor2 = {
            **VALID_COMMAND_DICT,
            "floor": 2,
            "domain": "climate",
            "service": "set_temperature",
            "entity_id": "climate.main_thermostat",
            "service_data": {"temperature": 24},
        }
        fake_resp = MagicMock()
        fake_resp.status_code = 200

        p_list, p_chat = self._mock_ollama_ready(json.dumps(cmd_floor2, ensure_ascii=False))
        with (
            p_list,
            p_chat,
            patch.object(server.requests, "post", return_value=fake_resp) as mock_post,
            self._mock_tts_success(),
        ):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["parsed_command"]["floor"], 2)
        self.assertIs(body["ha_execution"]["ok"], True)
        self.assertEqual(body["ha_execution"]["floor_port"], 8502)
        self.assertEqual(
            mock_post.call_args.args[0],
            "http://localhost:8502/api/services/climate/set_temperature",
        )
        self.assertIs(mock_post.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(
            mock_post.call_args.kwargs["json"],
            {"entity_id": "climate.main_thermostat", "temperature": 24},
        )

    def test_ha_redirect_or_http_error_reported_without_simulated_flag(self):
        redirect_resp = MagicMock()
        redirect_resp.status_code = 302
        cmd = server.LLMCommandSchema(**VALID_COMMAND_DICT)
        with patch.object(server.requests, "post", return_value=redirect_resp):
            result = server.execute_home_assistant_service(cmd)

        self.assertFalse(result["ok"])
        self.assertFalse(result["simulated"])
        self.assertEqual(result["status_code"], 302)
        self.assertEqual(result["error"], "HTTP 302")

    def test_ha_offline_falls_back_to_simulated(self):
        cmd_floor2 = {**VALID_COMMAND_DICT, "floor": 2, "entity_id": "light.kitchen"}
        p_list, p_chat = self._mock_ollama_ready(json.dumps(cmd_floor2, ensure_ascii=False))
        with (
            p_list,
            p_chat,
            patch.object(
                server.requests,
                "post",
                side_effect=requests.exceptions.ConnectionError("HA floor 2 down"),
            ),
            self._mock_tts_success(),
        ):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIs(body["ha_execution"]["ok"], False)
        self.assertIs(body["ha_execution"]["simulated"], True)
        self.assertEqual(body["ha_execution"]["floor_port"], 8502)
        self.assertEqual(body["ha_execution"]["error"], "Home Assistant unreachable")

    def test_execute_ha_raises_502_ha_execution_error_when_bypassed_validation(self):
        bypassed = server.LLMCommandSchema.model_construct(
            **{**VALID_COMMAND_DICT, "domain": "../admin", "service": "turn_on"}
        )
        with (
            patch.object(server, "reason_with_qwen", return_value=bypassed),
        ):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "ha")
        self.assertEqual(body["error_code"], "HA_EXECUTION_ERROR")


class TestEdgeTTSErrorHandlingAndFallback(GatewayTestBase):
    def test_tts_connection_error_returns_200_fallback_sanitized_and_preserves_latest_audio(self):
        existing_mp3 = b"PREVIOUS-VALID-MP3-CONTENT"
        self.latest_audio_path.write_bytes(existing_mp3)

        async def _failing_save(self_comm, path_str):
            Path(path_str).write_bytes(b"")
            raise ConnectionError("wss://speech.platform.bing.com/internal-path")

        p_list, p_chat = self._mock_ollama_ready()
        with (
            p_list,
            p_chat,
            self._mock_ha_success(),
            patch.object(server.edge_tts.Communicate, "save", new=_failing_save),
        ):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "success")
        self.assertIsNone(body["audio_url"])
        self.assertIs(body["tts_status"]["ok"], False)
        self.assertTrue(body["tts_status"]["fallback_message"])
        self.assertEqual(body["tts_status"]["error"], "Edge-TTS error (ConnectionError)")
        self.assertEqual(self.latest_audio_path.read_bytes(), existing_mp3)
        self.assertEqual(list(self.temp_dir.glob("response_*.mp3")), [])

    def test_tts_timeout_error_returns_200_fallback_and_does_not_create_latest_audio(self):
        self.assertFalse(self.latest_audio_path.exists())

        async def _timeout_save(self_comm, path_str):
            raise TimeoutError()

        p_list, p_chat = self._mock_ollama_ready()
        with (
            p_list,
            p_chat,
            self._mock_ha_success(),
            patch.object(server.edge_tts.Communicate, "save", new=_timeout_save),
        ):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "success")
        self.assertIsNone(body["audio_url"])
        self.assertIs(body["tts_status"]["ok"], False)
        self.assertTrue(body["tts_status"]["fallback_message"])
        self.assertEqual(body["tts_status"]["error"], "Edge-TTS request timed out")
        self.assertFalse(self.latest_audio_path.exists())

    def test_tts_happy_path_writes_mp3_and_serves_playable_audio_url(self):
        fake_mp3 = b"ID3-REALISTIC-MP3-BYTES"
        p_list, p_chat = self._mock_ollama_ready()
        with p_list, p_chat, self._mock_ha_success(), self._mock_tts_success(fake_mp3):
            resp = self.client.post(
                "/process-audio",
                files={"audio": ("cmd.webm", b"AUDIO_DATA", "audio/webm")},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "success")
        self.assertIs(body["tts_status"]["ok"], True)
        self.assertIsNone(body["tts_status"]["fallback_message"])
        self.assertRegex(body["audio_url"], r"^/play-response\?id=[a-f0-9]{10}$")
        self.assertEqual(self.latest_audio_path.read_bytes(), fake_mp3)

        audio_resp = self.client.get(body["audio_url"])
        self.assertEqual(audio_resp.status_code, 200)
        self.assertEqual(audio_resp.headers.get("content-type"), "audio/mpeg")
        self.assertEqual(audio_resp.content, fake_mp3)

    def test_prune_old_tts_files_keeps_newest_and_preserves_latest_response(self):
        self.latest_audio_path.write_bytes(b"LATEST")
        created = []
        for idx in range(5):
            path = self.temp_dir / f"response_000000000{idx}.mp3"
            path.write_bytes(b"MP3")
            ts_ns = (idx + 1) * 1_000_000_000
            os.utime(path, ns=(ts_ns, ts_ns))
            created.append(path)

        server._prune_old_tts_files(keep_count=2)

        remaining = sorted(p.name for p in self.temp_dir.glob("response_*.mp3"))
        self.assertEqual(remaining, ["response_0000000003.mp3", "response_0000000004.mp3"])
        self.assertTrue(self.latest_audio_path.exists())


class TestPlayResponseHygieneAndTempCleanup(GatewayTestBase):
    def test_play_response_rejects_path_traversal_and_invalid_ids(self):
        bad_ids = [
            "../../etc/passwd",
            "../server.py",
            r"..\..\windows\win.ini",
            "abc_not_hex",
            "",
        ]
        for bad_id in bad_ids:
            with self.subTest(bad_id=bad_id):
                resp = self.client.get("/play-response", params={"id": bad_id})
                self.assertEqual(resp.status_code, 400)
                self.assertTrue(resp.headers.get("content-type", "").startswith("application/json"))
                body = resp.json()
                self.assertEqual(body["status"], "error")
                self.assertEqual(body["error_code"], "INVALID_AUDIO_ID")
                self.assertEqual(body["stage"], "tts")

    def test_play_response_missing_valid_id_strictly_returns_404_even_when_latest_exists(self):
        # Regression check: valid hex id that doesn't exist must NOT fall back to latest_response.mp3
        self.latest_audio_path.write_bytes(b"LATEST-MP3-DATA")

        resp = self.client.get("/play-response", params={"id": "deadbeef12"})
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["stage"], "tts")
        self.assertEqual(body["error_code"], "AUDIO_NOT_FOUND")

        # Omitting id still serves latest_response.mp3
        latest_resp = self.client.get("/play-response")
        self.assertEqual(latest_resp.status_code, 200)
        self.assertEqual(latest_resp.content, b"LATEST-MP3-DATA")

    def test_temp_input_audio_cleaned_up_even_when_pipeline_stages_fail(self):
        scenarios = []

        # 1. Empty upload (400)
        scenarios.append(
            ("empty_upload", lambda: self.client.post("/process-audio", files={"audio": ("e.webm", b"", "audio/webm")}))
        )

        # 2. Oversized upload (413)
        def _oversized():
            server.MAX_AUDIO_UPLOAD_BYTES = 16
            resp = self.client.post("/process-audio", files={"audio": ("big.webm", b"X" * 32, "audio/webm")})
            server.MAX_AUDIO_UPLOAD_BYTES = 10 * 1024 * 1024
            return resp

        scenarios.append(("oversized_upload", _oversized))

        # 3. STT error (500)
        def _stt_fail():
            bad_whisper = MagicMock()
            bad_whisper.transcribe.side_effect = ValueError("decode fail")
            server.whisper_model = bad_whisper
            return self.client.post("/process-audio", files={"audio": ("../../evil.wav", b"AUDIO", "audio/wav")})

        scenarios.append(("stt_error", _stt_fail))

        # 4. Empty transcript (422)
        def _empty_transcript():
            server.whisper_model = make_fake_whisper_model("")
            return self.client.post("/process-audio", files={"audio": ("s.webm", b"AUDIO", "audio/webm")})

        scenarios.append(("empty_transcript", _empty_transcript))

        # 5. Ollama unreachable (503)
        def _ollama_down():
            server.whisper_model = make_fake_whisper_model("چراغ را روشن کن")
            with patch.object(server.ollama, "list", side_effect=ConnectionError("offline")):
                return self.client.post("/process-audio", files={"audio": ("o.webm", b"AUDIO", "audio/webm")})

        scenarios.append(("ollama_unreachable", _ollama_down))

        # 6. Invalid LLM JSON (502)
        def _llm_bad_json():
            server.whisper_model = make_fake_whisper_model("چراغ را روشن کن")
            p_list, p_chat = self._mock_ollama_ready("not json")
            with p_list, p_chat:
                return self.client.post("/process-audio", files={"audio": ("j.webm", b"AUDIO", "audio/webm")})

        scenarios.append(("llm_invalid_json", _llm_bad_json))

        for name, invoke in scenarios:
            with self.subTest(scenario=name):
                self.assertEqual(list(self.temp_dir.iterdir()), [])
                resp = invoke()
                self.assertGreaterEqual(resp.status_code, 400)
                self.assertEqual(
                    list(self.temp_dir.iterdir()),
                    [],
                    f"Temporary input audio leaked after {name}",
                )


class TestListenPortResolution(unittest.TestCase):
    def _make_fake_socket_cls(self, blocked_ports, ephemeral_port=54321):
        attempts = []

        class _FakeSocket:
            def __init__(self, family, sock_type):
                self._bound_port = None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def setsockopt(self, level, optname, value):
                pass

            def bind(self, addr):
                _, port = addr
                attempts.append(port)
                if port in blocked_ports:
                    raise OSError(f"Port {port} unavailable")
                self._bound_port = ephemeral_port if port == 0 else port

            def getsockname(self):
                return ("0.0.0.0", self._bound_port)

        return _FakeSocket, attempts

    def test_returns_requested_port_when_available(self):
        fake_sock_cls, attempts = self._make_fake_socket_cls(blocked_ports=set())
        with patch("socket.socket", side_effect=fake_sock_cls):
            chosen = server._resolve_listen_port("0.0.0.0", 8000)

        self.assertEqual(chosen, 8000)
        self.assertEqual(attempts, [8000])

    def test_falls_back_to_8800_or_8888_or_9000_or_ephemeral_when_blocked(self):
        # 8000 blocked -> picks 8800
        sock_8800, attempts_8800 = self._make_fake_socket_cls(blocked_ports={8000})
        with patch("socket.socket", side_effect=sock_8800):
            self.assertEqual(server._resolve_listen_port("0.0.0.0", 8000), 8800)
        self.assertEqual(attempts_8800, [8000, 8800])

        # 8000 and 8800 blocked -> picks 8888
        sock_8888, attempts_8888 = self._make_fake_socket_cls(blocked_ports={8000, 8800})
        with patch("socket.socket", side_effect=sock_8888):
            self.assertEqual(server._resolve_listen_port("0.0.0.0", 8000), 8888)
        self.assertEqual(attempts_8888, [8000, 8800, 8888])

        # 8000, 8800, 8888 blocked -> picks 9000
        sock_9000, attempts_9000 = self._make_fake_socket_cls(blocked_ports={8000, 8800, 8888})
        with patch("socket.socket", side_effect=sock_9000):
            self.assertEqual(server._resolve_listen_port("0.0.0.0", 8000), 9000)
        self.assertEqual(attempts_9000, [8000, 8800, 8888, 9000])

        # All fixed ports blocked -> falls back to ephemeral port 0
        sock_ephemeral, attempts_eph = self._make_fake_socket_cls(
            blocked_ports={8000, 8800, 8888, 9000},
            ephemeral_port=51234,
        )
        with patch("socket.socket", side_effect=sock_ephemeral):
            self.assertEqual(server._resolve_listen_port("0.0.0.0", 8000), 51234)
        self.assertEqual(attempts_eph, [8000, 8800, 8888, 9000, 0])

    def test_raises_runtime_error_when_all_ports_including_ephemeral_fail(self):
        sock_none, _ = self._make_fake_socket_cls(blocked_ports={8000, 8800, 8888, 9000, 0})
        with patch("socket.socket", side_effect=sock_none):
            with self.assertRaises(RuntimeError):
                server._resolve_listen_port("127.0.0.1", 8000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
