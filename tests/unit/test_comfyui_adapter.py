"""Tests for ComfyUIAdapter — REST + WebSocket transport to ComfyUI runtime.

All tests use httpx mock transport / fake WS — no real ComfyUI needed.
"""
import json
import os
import threading
import time

import httpx
import pytest

from film_director.errors import (
    ComfyUIExecutionError,
    ComfyUIResultError,
    ComfyUIUnavailableError,
)
from film_director.generation.comfyui_adapter import (
    ComfyUIAdapter,
    ComfyUIGenerationResult,
    ComfyUIOutputRef,
)


# ---------------------------------------------------------------------------
# Helpers — httpx mock transport
# ---------------------------------------------------------------------------

def _mock_transport(handler):
    """Create an httpx MockTransport from a handler(request) -> Response."""
    return httpx.MockTransport(handler)


def _adapter_with(handler) -> ComfyUIAdapter:
    transport = _mock_transport(handler)
    client = httpx.Client(transport=transport, base_url="http://comfyui:8188")
    return ComfyUIAdapter(base_url="http://comfyui:8188", http_client=client)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_success(self):
        def handler(request):
            assert str(request.url).endswith("/system_stats")
            return httpx.Response(200, json={
                "system": {"os": "nt", "python_version": "3.13.12"},
                "devices": [{"name": "NVIDIA RTX 5090", "vram_total": 34190458880}],
            })
        adapter = _adapter_with(handler)
        result = adapter.health()
        assert "system" in result
        assert "devices" in result

    def test_health_transport_failure(self):
        def handler(request):
            raise httpx.ConnectError("Connection refused")
        adapter = _adapter_with(handler)
        with pytest.raises(ComfyUIUnavailableError, match="(?i)unavailable|connect"):
            adapter.health()

    def test_health_non_200(self):
        def handler(request):
            return httpx.Response(500, text="Internal error")
        adapter = _adapter_with(handler)
        with pytest.raises(ComfyUIUnavailableError):
            adapter.health()


# ---------------------------------------------------------------------------
# Upload image
# ---------------------------------------------------------------------------

class TestUploadImage:
    def test_upload_success(self, tmp_path):
        ref_file = os.path.join(str(tmp_path), "ref.png")
        with open(ref_file, "wb") as f:
            f.write(b"fake png data")

        def handler(request):
            assert "/upload/image" in str(request.url)
            return httpx.Response(200, json={
                "name": "ref_uploaded.png",
                "subfolder": "",
                "type": "input",
            })
        adapter = _adapter_with(handler)
        result = adapter.upload_image(ref_file, "ref_uploaded.png")
        assert result == "ref_uploaded.png"

    def test_upload_missing_file_raises(self):
        adapter = _adapter_with(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ComfyUIResultError, match="(?i)not found|exist"):
            adapter.upload_image("/nonexistent/file.png", "test.png")

    def test_upload_directory_raises(self, tmp_path):
        adapter = _adapter_with(lambda r: httpx.Response(200, json={}))
        with pytest.raises(ComfyUIResultError, match="(?i)regular file"):
            adapter.upload_image(str(tmp_path), "test.png")

    def test_upload_error_response(self, tmp_path):
        ref_file = os.path.join(str(tmp_path), "ref.png")
        with open(ref_file, "wb") as f:
            f.write(b"data")

        def handler(request):
            return httpx.Response(400, json={"error": "bad request"})
        adapter = _adapter_with(handler)
        with pytest.raises(ComfyUIResultError, match="(?i)upload"):
            adapter.upload_image(ref_file, "ref.png")

    def test_upload_malformed_response(self, tmp_path):
        ref_file = os.path.join(str(tmp_path), "ref.png")
        with open(ref_file, "wb") as f:
            f.write(b"data")

        def handler(request):
            return httpx.Response(200, json={"no_name_field": True})
        adapter = _adapter_with(handler)
        with pytest.raises(ComfyUIResultError, match="(?i)name"):
            adapter.upload_image(ref_file, "ref.png")

    def test_upload_returns_comfyui_filename(self, tmp_path):
        """ComfyUI may rename file — adapter returns actual ComfyUI name."""
        ref_file = os.path.join(str(tmp_path), "ref.png")
        with open(ref_file, "wb") as f:
            f.write(b"data")

        def handler(request):
            return httpx.Response(200, json={
                "name": "ref_renamed_by_comfy.png",
                "subfolder": "",
                "type": "input",
            })
        adapter = _adapter_with(handler)
        result = adapter.upload_image(ref_file, "original_name.png")
        assert result == "ref_renamed_by_comfy.png"


# ---------------------------------------------------------------------------
# Submit prompt
# ---------------------------------------------------------------------------

class TestSubmit:
    def test_submit_success(self):
        def handler(request):
            body = json.loads(request.content)
            assert "prompt" in body
            assert "client_id" in body
            return httpx.Response(200, json={
                "prompt_id": "abc-123",
                "number": 1,
                "node_errors": {},
            })
        adapter = _adapter_with(handler)
        prompt_id = adapter.submit({"104": {"inputs": {}}}, "client-1")
        assert prompt_id == "abc-123"

    def test_submit_body_contract(self):
        """POST /prompt body must contain 'prompt' (workflow) and 'client_id'."""
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"prompt_id": "x", "number": 1, "node_errors": {}})

        adapter = _adapter_with(handler)
        workflow = {"6": {"inputs": {"model": "test"}, "class_type": "UNETLoader"}}
        adapter.submit(workflow, "my-client-id")
        assert captured["body"]["prompt"] == workflow
        assert captured["body"]["client_id"] == "my-client-id"

    def test_submit_failure(self):
        def handler(request):
            return httpx.Response(400, json={"error": "invalid workflow"})
        adapter = _adapter_with(handler)
        with pytest.raises(ComfyUIExecutionError, match="(?i)submit"):
            adapter.submit({"bad": {}}, "client-1")

    def test_submit_transport_failure(self):
        def handler(request):
            raise httpx.ConnectError("refused")
        adapter = _adapter_with(handler)
        with pytest.raises(ComfyUIUnavailableError):
            adapter.submit({"x": {}}, "client-1")

    def test_submit_returns_prompt_id_verbatim(self):
        def handler(request):
            return httpx.Response(200, json={
                "prompt_id": "fea453b7-ccb6-4f0b-8bee-05225c4df2bd",
                "number": 42,
                "node_errors": {},
            })
        adapter = _adapter_with(handler)
        pid = adapter.submit({}, "c1")
        assert pid == "fea453b7-ccb6-4f0b-8bee-05225c4df2bd"


# ---------------------------------------------------------------------------
# WebSocket monitor — using fake WS server
# ---------------------------------------------------------------------------

class TestMonitor:
    """WS tests use a real local WS server on a random port."""

    def _run_ws_server(self, port, messages, ready_event):
        """Start a simple WS server that sends messages then closes."""
        import websockets.sync.server
        def ws_handler(ws):
            for msg in messages:
                ws.send(json.dumps(msg))
            # Keep open briefly for client to process
            try:
                ws.recv(timeout=1)
            except Exception:
                pass
        server = websockets.sync.server.serve(ws_handler, "127.0.0.1", port)
        ready_event.set()
        server.serve_forever()

    def _start_server(self, messages):
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        ready = threading.Event()
        t = threading.Thread(
            target=self._run_ws_server, args=(port, messages, ready), daemon=True
        )
        t.start()
        ready.wait(timeout=5)
        time.sleep(0.1)  # brief settle
        return port

    def test_monitor_success_ignores_unrelated(self):
        target = "target-prompt-id"
        messages = [
            {"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 1}}}},
            {"type": "execution_start", "data": {"prompt_id": "OTHER-PROMPT"}},
            {"type": "execution_start", "data": {"prompt_id": target}},
            {"type": "executing", "data": {"node": "6", "prompt_id": target}},
            {"type": "execution_success", "data": {"prompt_id": target}},
        ]
        port = self._start_server(messages)
        adapter = ComfyUIAdapter(base_url=f"http://127.0.0.1:{port}")
        adapter.monitor(target, "client-1", timeout=10)
        # Should complete without error

    def test_monitor_execution_error(self):
        target = "target-prompt-id"
        messages = [
            {"type": "execution_start", "data": {"prompt_id": target}},
            {"type": "execution_error", "data": {
                "prompt_id": target,
                "node_id": "104",
                "node_type": "MiniMaxH3ReferenceToVideo",
                "exception_message": "OOM",
                "exception_type": "RuntimeError",
            }},
        ]
        port = self._start_server(messages)
        adapter = ComfyUIAdapter(base_url=f"http://127.0.0.1:{port}")
        with pytest.raises(ComfyUIExecutionError, match="(?i)execution.*error|OOM"):
            adapter.monitor(target, "client-1", timeout=10)

    def test_monitor_minimal_error_payload(self):
        target = "target-prompt-id"
        messages = [
            {"type": "execution_error", "data": {"prompt_id": target}},
        ]
        port = self._start_server(messages)
        adapter = ComfyUIAdapter(base_url=f"http://127.0.0.1:{port}")
        with pytest.raises(ComfyUIExecutionError):
            adapter.monitor(target, "client-1", timeout=10)

    def test_monitor_timeout(self):
        """No completion event → timeout."""
        messages = [
            {"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 1}}}},
        ]
        port = self._start_server(messages)
        adapter = ComfyUIAdapter(base_url=f"http://127.0.0.1:{port}")
        with pytest.raises((ComfyUIExecutionError, ComfyUIUnavailableError), match="(?i)timeout"):
            adapter.monitor("target", "client-1", timeout=1)


# ---------------------------------------------------------------------------
# Get result (history parsing)
# ---------------------------------------------------------------------------

class TestGetResult:
    def test_result_success_verified_shape(self):
        """BLOCKING: uses exact M3.A verified R2V history output shape."""
        history = {
            "test-prompt-id": {
                "outputs": {
                    "92": {
                        "images": [
                            {
                                "filename": "m3a_validation_00001_.mp4",
                                "subfolder": "preflight",
                                "type": "output",
                            }
                        ],
                        "animated": [True],
                    }
                },
                "status": {
                    "status_str": "success",
                    "completed": True,
                },
            }
        }

        def handler(request):
            assert "/history/test-prompt-id" in str(request.url)
            return httpx.Response(200, json=history)

        adapter = _adapter_with(handler)
        result = adapter.get_result("test-prompt-id", "92")
        assert isinstance(result, ComfyUIGenerationResult)
        assert result.prompt_id == "test-prompt-id"
        assert result.output_node_id == "92"
        assert len(result.outputs) == 1
        assert result.outputs[0].filename == "m3a_validation_00001_.mp4"
        assert result.outputs[0].subfolder == "preflight"
        assert result.outputs[0].type == "output"

    def test_result_missing_prompt(self):
        def handler(request):
            return httpx.Response(200, json={})
        adapter = _adapter_with(handler)
        with pytest.raises(ComfyUIResultError, match="(?i)prompt"):
            adapter.get_result("missing-id", "92")

    def test_result_missing_outputs(self):
        def handler(request):
            return httpx.Response(200, json={
                "test-id": {"status": {"status_str": "success"}}
            })
        adapter = _adapter_with(handler)
        with pytest.raises(ComfyUIResultError, match="(?i)output"):
            adapter.get_result("test-id", "92")

    def test_result_missing_node(self):
        def handler(request):
            return httpx.Response(200, json={
                "test-id": {"outputs": {"99": {"images": []}}}
            })
        adapter = _adapter_with(handler)
        with pytest.raises(ComfyUIResultError, match="92"):
            adapter.get_result("test-id", "92")

    def test_result_empty_images(self):
        def handler(request):
            return httpx.Response(200, json={
                "test-id": {"outputs": {"92": {"images": []}}}
            })
        adapter = _adapter_with(handler)
        with pytest.raises(ComfyUIResultError, match="(?i)empty|output"):
            adapter.get_result("test-id", "92")

    def test_result_missing_filename(self):
        def handler(request):
            return httpx.Response(200, json={
                "test-id": {"outputs": {"92": {"images": [
                    {"subfolder": "x", "type": "output"}
                ]}}}
            })
        adapter = _adapter_with(handler)
        with pytest.raises(ComfyUIResultError, match="(?i)filename"):
            adapter.get_result("test-id", "92")

    def test_result_output_node_caller_supplied(self):
        """Output node ID is NOT hardcoded — caller provides it."""
        history = {
            "pid": {"outputs": {
                "42": {"images": [{"filename": "out.mp4", "subfolder": "", "type": "output"}]},
                "92": {"images": [{"filename": "other.mp4", "subfolder": "", "type": "output"}]},
            }}
        }

        def handler(request):
            return httpx.Response(200, json=history)

        adapter = _adapter_with(handler)
        result = adapter.get_result("pid", "42")
        assert result.output_node_id == "42"
        assert result.outputs[0].filename == "out.mp4"


# ---------------------------------------------------------------------------
# Download output
# ---------------------------------------------------------------------------

class TestDownloadOutput:
    def test_download_success(self, tmp_path):
        video_bytes = b"\x00\x00\x00\x1cftypisom" + b"\x00" * 100  # fake mp4

        def handler(request):
            url = str(request.url)
            assert "filename=test.mp4" in url
            assert "subfolder=video" in url
            assert "type=output" in url
            return httpx.Response(200, content=video_bytes)

        adapter = _adapter_with(handler)
        ref = ComfyUIOutputRef(filename="test.mp4", subfolder="video", type="output")
        dest = os.path.join(str(tmp_path), "downloaded.mp4")
        result = adapter.download_output(ref, dest)
        assert result == dest
        with open(dest, "rb") as f:
            assert f.read() == video_bytes

    def test_download_failure(self, tmp_path):
        def handler(request):
            return httpx.Response(404, text="Not found")

        adapter = _adapter_with(handler)
        ref = ComfyUIOutputRef(filename="missing.mp4", subfolder="", type="output")
        dest = os.path.join(str(tmp_path), "out.mp4")
        with pytest.raises(ComfyUIResultError, match="(?i)download"):
            adapter.download_output(ref, dest)

    def test_download_empty_body_rejected(self, tmp_path):
        def handler(request):
            return httpx.Response(200, content=b"")
        adapter = _adapter_with(handler)
        ref = ComfyUIOutputRef(filename="empty.mp4", subfolder="", type="output")
        dest = os.path.join(str(tmp_path), "out.mp4")
        with pytest.raises(ComfyUIResultError, match="(?i)empty"):
            adapter.download_output(ref, dest)

    def test_download_uses_exact_history_metadata(self, tmp_path):
        """Adapter passes exact filename/subfolder/type from history to /view."""
        captured_params = {}

        def handler(request):
            url = str(request.url)
            captured_params["url"] = url
            return httpx.Response(200, content=b"video data")

        adapter = _adapter_with(handler)
        ref = ComfyUIOutputRef(
            filename="m3a_validation_00001_.mp4",
            subfolder="preflight",
            type="output",
        )
        dest = os.path.join(str(tmp_path), "out.mp4")
        adapter.download_output(ref, dest)
        assert "filename=m3a_validation_00001_.mp4" in captured_params["url"]
        assert "subfolder=preflight" in captured_params["url"]
        assert "type=output" in captured_params["url"]


# ---------------------------------------------------------------------------
# BLOCKING: full result pipeline (history → output ref → download)
# ---------------------------------------------------------------------------

class TestBlockingResultPipeline:
    def test_history_to_download(self, tmp_path):
        video_bytes = b"real video content bytes for verification"
        history = {
            "pid-1": {
                "outputs": {
                    "92": {
                        "images": [{
                            "filename": "m3a_validation_00001_.mp4",
                            "subfolder": "preflight",
                            "type": "output",
                        }],
                        "animated": [True],
                    }
                },
                "status": {"status_str": "success", "completed": True},
            }
        }

        def handler(request):
            url = str(request.url)
            if "/history/" in url:
                return httpx.Response(200, json=history)
            elif "/view" in url:
                return httpx.Response(200, content=video_bytes)
            return httpx.Response(404)

        adapter = _adapter_with(handler)
        result = adapter.get_result("pid-1", "92")
        assert result.outputs[0].filename == "m3a_validation_00001_.mp4"
        assert result.outputs[0].subfolder == "preflight"
        assert result.outputs[0].type == "output"

        dest = os.path.join(str(tmp_path), "video.mp4")
        adapter.download_output(result.outputs[0], dest)
        with open(dest, "rb") as f:
            assert f.read() == video_bytes
