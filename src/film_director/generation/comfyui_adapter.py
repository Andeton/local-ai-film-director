"""ComfyUI REST + WebSocket adapter — transport layer for ComfyUI runtime.

Uses verified M3.A runtime contracts. Does NOT contain H3 parameter
policy, reference selection, persistence, or media staging logic.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from film_director.errors import (
    ComfyUIExecutionError,
    ComfyUIResultError,
    ComfyUIUnavailableError,
)

_DEFAULT_REST_TIMEOUT = 30.0
_DEFAULT_DOWNLOAD_TIMEOUT = 120.0


@dataclass(frozen=True)
class ComfyUIOutputRef:
    """Immutable reference to one ComfyUI output file from history metadata."""

    filename: str
    subfolder: str
    type: str


@dataclass(frozen=True)
class ComfyUIGenerationResult:
    """Parsed result from ComfyUI /history for a completed prompt."""

    prompt_id: str
    output_node_id: str
    outputs: list[ComfyUIOutputRef]


class ComfyUIAdapter:
    """Sync REST + WebSocket adapter for ComfyUI runtime."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        http_client: httpx.Client | None = None,
        rest_timeout: float = _DEFAULT_REST_TIMEOUT,
        download_timeout: float = _DEFAULT_DOWNLOAD_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._ws_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
        self._rest_timeout = rest_timeout
        self._download_timeout = download_timeout
        self._client = http_client

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(base_url=self._base_url, timeout=self._rest_timeout)

    def _owns_client(self) -> bool:
        return self._client is None

    # -----------------------------------------------------------------
    # Health
    # -----------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        client = self._get_client()
        try:
            resp = client.get("/system_stats")
        except (httpx.HTTPError, httpx.TransportError) as e:
            raise ComfyUIUnavailableError(
                f"ComfyUI unavailable: {e}",
                detail=f"base_url={self._base_url}",
            ) from e
        finally:
            if self._owns_client():
                client.close()

        if resp.status_code != 200:
            raise ComfyUIUnavailableError(
                f"ComfyUI health check failed: HTTP {resp.status_code}",
                detail=resp.text[:500],
            )
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise ComfyUIUnavailableError(
                f"ComfyUI health response not valid JSON: {e}",
            ) from e

    # -----------------------------------------------------------------
    # Object info — node class discovery (M7.G.B)
    # -----------------------------------------------------------------

    def get_object_info(self) -> dict[str, Any]:
        """GET /object_info → dict of node class definitions.

        Returns {class_name: {input: ..., output: ..., ...}, ...}.
        Used for runtime capability probing (checking required nodes).
        """
        client = self._get_client()
        try:
            resp = client.get("/object_info")
        except (httpx.HTTPError, httpx.TransportError) as e:
            raise ComfyUIUnavailableError(
                f"ComfyUI object_info unavailable: {e}",
                detail=f"base_url={self._base_url}",
            ) from e
        finally:
            if self._owns_client():
                client.close()

        if resp.status_code != 200:
            raise ComfyUIUnavailableError(
                f"ComfyUI object_info failed: HTTP {resp.status_code}",
                detail=resp.text[:500],
            )
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise ComfyUIUnavailableError(
                f"ComfyUI object_info response not valid JSON: {e}",
            ) from e

    # -----------------------------------------------------------------
    # Upload image
    # -----------------------------------------------------------------

    def upload_image(self, file_path: str, filename: str) -> str:
        if not os.path.exists(file_path):
            raise ComfyUIResultError(
                f"Reference file not found: {file_path}",
                detail=f"file_path={file_path}",
            )
        if not os.path.isfile(file_path):
            raise ComfyUIResultError(
                f"Path is not a regular file: {file_path}",
                detail=f"file_path={file_path}",
            )

        client = self._get_client()
        try:
            with open(file_path, "rb") as f:
                resp = client.post(
                    "/upload/image",
                    files={"image": (filename, f, "application/octet-stream")},
                    data={"overwrite": "true"},
                )
        except (httpx.HTTPError, httpx.TransportError) as e:
            raise ComfyUIUnavailableError(
                f"Upload transport failure: {e}",
            ) from e
        finally:
            if self._owns_client():
                client.close()

        if resp.status_code != 200:
            raise ComfyUIResultError(
                f"Upload failed: HTTP {resp.status_code}",
                detail=resp.text[:500],
            )

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise ComfyUIResultError(
                f"Upload response not valid JSON: {e}",
            ) from e

        uploaded_name = data.get("name")
        if not uploaded_name:
            raise ComfyUIResultError(
                "Upload response missing 'name' field",
                detail=str(data)[:500],
            )
        return uploaded_name

    # -----------------------------------------------------------------
    # Submit prompt
    # -----------------------------------------------------------------

    def submit(self, workflow_json: dict, client_id: str) -> str:
        body = {"prompt": workflow_json, "client_id": client_id}
        client = self._get_client()
        try:
            resp = client.post("/prompt", json=body)
        except (httpx.HTTPError, httpx.TransportError) as e:
            raise ComfyUIUnavailableError(
                f"Submit transport failure: {e}",
            ) from e
        finally:
            if self._owns_client():
                client.close()

        if resp.status_code != 200:
            raw = resp.text[:1000] if resp.text else ""
            # Extract concise validation info from ComfyUI error responses
            summary = f"HTTP {resp.status_code}"
            try:
                err_data = resp.json()
                if "node_errors" in err_data:
                    for nid, nerr in err_data["node_errors"].items():
                        cls = nerr.get("class_type", "?")
                        msgs = [e.get("message", "") for e in nerr.get("errors", [])]
                        summary = f"Node {nid} ({cls}): {'; '.join(msgs)}"
                        break
                elif "error" in err_data:
                    emsg = err_data["error"]
                    if isinstance(emsg, dict):
                        summary = emsg.get("message", str(emsg))[:200]
                    else:
                        summary = str(emsg)[:200]
            except Exception:
                summary = raw[:200] if raw else f"HTTP {resp.status_code}"
            raise ComfyUIExecutionError(
                f"ComfyUI rejected workflow: {summary}",
                detail=raw,
            )

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise ComfyUIExecutionError(
                f"Submit response not valid JSON: {e}",
            ) from e

        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIExecutionError(
                "Submit response missing 'prompt_id'",
                detail=str(data)[:500],
            )
        return prompt_id

    # -----------------------------------------------------------------
    # WebSocket monitor
    # -----------------------------------------------------------------

    def monitor(self, prompt_id: str, client_id: str, timeout: float) -> None:
        import websockets.sync.client
        import websockets.exceptions

        ws_url = f"{self._ws_url}/ws?clientId={client_id}"
        try:
            with websockets.sync.client.connect(ws_url, close_timeout=5) as ws:
                ws.recv_bufsize = 1024 * 1024  # 1MB
                deadline = _monotonic() + timeout
                while True:
                    remaining = deadline - _monotonic()
                    if remaining <= 0:
                        raise ComfyUIExecutionError(
                            f"Generation timeout after {timeout}s",
                            detail=f"prompt_id={prompt_id}",
                        )
                    try:
                        raw = ws.recv(timeout=min(remaining, 5.0))
                    except TimeoutError:
                        continue
                    except websockets.exceptions.ConnectionClosed:
                        raise ComfyUIExecutionError(
                            "WebSocket closed before completion",
                            detail=f"prompt_id={prompt_id}",
                        )

                    if isinstance(raw, bytes):
                        continue  # skip binary frames (preview images)

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    msg_type = msg.get("type", "")
                    data = msg.get("data", {})
                    msg_prompt = data.get("prompt_id", "")

                    if msg_prompt and msg_prompt != prompt_id:
                        continue  # event for a different prompt

                    if msg_type == "execution_success" and msg_prompt == prompt_id:
                        return

                    if msg_type == "execution_error" and msg_prompt == prompt_id:
                        node_id = data.get("node_id", "unknown")
                        node_type = data.get("node_type", "unknown")
                        exc_msg = data.get("exception_message", "unknown error")
                        exc_type = data.get("exception_type", "unknown")
                        raise ComfyUIExecutionError(
                            f"Execution error on node {node_id} ({node_type}): "
                            f"{exc_type}: {exc_msg}",
                            detail=f"prompt_id={prompt_id}, node_id={node_id}",
                        )

        except ComfyUIExecutionError:
            raise
        except Exception as e:
            if "timeout" in str(e).lower():
                raise ComfyUIExecutionError(
                    f"Generation timeout: {e}",
                    detail=f"prompt_id={prompt_id}",
                ) from e
            raise ComfyUIUnavailableError(
                f"WebSocket connection failed: {e}",
                detail=f"ws_url={ws_url}",
            ) from e

    # -----------------------------------------------------------------
    # Check prompt status (recovery)
    # -----------------------------------------------------------------

    def check_prompt_status(self, prompt_id: str) -> str:
        """Check ComfyUI prompt status without blocking.

        Returns one of: "succeeded", "failed", "running", "queued", "unknown".
        Raises ComfyUIUnavailableError if ComfyUI is unreachable.
        """
        client = self._get_client()
        try:
            resp = client.get(f"/history/{prompt_id}")
        except (httpx.HTTPError, httpx.TransportError) as e:
            raise ComfyUIUnavailableError(
                f"Cannot reach ComfyUI: {e}",
                detail=f"prompt_id={prompt_id}",
            ) from e
        finally:
            if self._owns_client():
                client.close()

        if resp.status_code != 200:
            return "unknown"

        try:
            history = resp.json()
        except Exception:
            return "unknown"

        entry = history.get(prompt_id)
        if entry is None:
            # Check queue
            try:
                q_resp = client.get("/queue")
                if q_resp.status_code == 200:
                    qdata = q_resp.json()
                    for item in qdata.get("queue_running", []):
                        if len(item) >= 2 and item[1] == prompt_id:
                            return "running"
                    for item in qdata.get("queue_pending", []):
                        if len(item) >= 2 and item[1] == prompt_id:
                            return "queued"
            except Exception:
                pass
            return "unknown"

        status_info = entry.get("status", {})
        status_str = status_info.get("status_str", "")
        completed = status_info.get("completed", False)

        if status_str == "success" or completed:
            return "succeeded"
        elif status_str == "error":
            return "failed"
        else:
            return "running"

    # -----------------------------------------------------------------
    # Get result (history)
    # -----------------------------------------------------------------

    def get_result(
        self, prompt_id: str, output_node_id: str
    ) -> ComfyUIGenerationResult:
        client = self._get_client()
        try:
            resp = client.get(f"/history/{prompt_id}")
        except (httpx.HTTPError, httpx.TransportError) as e:
            raise ComfyUIResultError(
                f"History retrieval failed: {e}",
                detail=f"prompt_id={prompt_id}",
            ) from e
        finally:
            if self._owns_client():
                client.close()

        if resp.status_code != 200:
            raise ComfyUIResultError(
                f"History request failed: HTTP {resp.status_code}",
                detail=f"prompt_id={prompt_id}",
            )

        try:
            history = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise ComfyUIResultError(
                f"History response not valid JSON: {e}",
            ) from e

        entry = history.get(prompt_id)
        if entry is None:
            raise ComfyUIResultError(
                f"Prompt {prompt_id!r} not found in history",
                detail=f"prompt_id={prompt_id}",
            )

        outputs = entry.get("outputs")
        if not outputs:
            raise ComfyUIResultError(
                f"No outputs in history for prompt {prompt_id!r}",
                detail=f"prompt_id={prompt_id}",
            )

        node_output = outputs.get(output_node_id)
        if node_output is None:
            raise ComfyUIResultError(
                f"Output node {output_node_id!r} not found in history outputs",
                detail=f"prompt_id={prompt_id}, node_id={output_node_id}",
            )

        images = node_output.get("images", [])
        if not images:
            raise ComfyUIResultError(
                f"Empty output list for node {output_node_id!r}",
                detail=f"prompt_id={prompt_id}",
            )

        output_refs: list[ComfyUIOutputRef] = []
        for item in images:
            fname = item.get("filename")
            if not fname:
                raise ComfyUIResultError(
                    f"Output entry missing 'filename' in node {output_node_id!r}",
                    detail=f"prompt_id={prompt_id}, item={item}",
                )
            output_refs.append(ComfyUIOutputRef(
                filename=fname,
                subfolder=item.get("subfolder", ""),
                type=item.get("type", "output"),
            ))

        return ComfyUIGenerationResult(
            prompt_id=prompt_id,
            output_node_id=output_node_id,
            outputs=output_refs,
        )

    # -----------------------------------------------------------------
    # Download output
    # -----------------------------------------------------------------

    def download_output(self, output_ref: ComfyUIOutputRef, destination: str) -> str:
        params = {
            "filename": output_ref.filename,
            "subfolder": output_ref.subfolder,
            "type": output_ref.type,
        }
        client = self._get_client()
        try:
            resp = client.get("/view", params=params, timeout=self._download_timeout)
        except (httpx.HTTPError, httpx.TransportError) as e:
            raise ComfyUIResultError(
                f"Download failed: {e}",
                detail=f"filename={output_ref.filename}",
            ) from e
        finally:
            if self._owns_client():
                client.close()

        if resp.status_code != 200:
            raise ComfyUIResultError(
                f"Download failed: HTTP {resp.status_code}",
                detail=f"filename={output_ref.filename}",
            )

        if not resp.content:
            raise ComfyUIResultError(
                f"Download returned empty body for {output_ref.filename}",
                detail=f"filename={output_ref.filename}",
            )

        with open(destination, "wb") as f:
            f.write(resp.content)
        return destination


def _monotonic() -> float:
    import time

    return time.monotonic()
