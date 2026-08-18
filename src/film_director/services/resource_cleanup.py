"""Minimal resource cleanup for idle stage boundaries.

Releases GPU memory from Ollama and ComfyUI when operations complete
and the system enters human-review/idle state.

Does NOT: kill servers, interrupt active work, or create a framework.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


def unload_ollama_models(ollama_base_url: str = "http://127.0.0.1:11434") -> dict:
    """Unload all loaded Ollama models. Idempotent, non-destructive."""
    result = {"unloaded": [], "errors": []}
    try:
        r = httpx.get(f"{ollama_base_url}/api/ps", timeout=10)
        if r.status_code != 200:
            return result
        models = r.json().get("models", [])
        for m in models:
            name = m.get("name", "")
            try:
                httpx.post(
                    f"{ollama_base_url}/api/generate",
                    json={"model": name, "keep_alive": 0},
                    timeout=30,
                )
                result["unloaded"].append(name)
                logger.info("Ollama model unloaded: %s", name)
            except Exception as e:
                result["errors"].append(f"{name}: {e}")
                logger.warning("Failed to unload Ollama model %s: %s", name, e)
    except Exception as e:
        result["errors"].append(f"ps: {e}")
    return result


def free_comfyui_memory(comfyui_base_url: str = "http://127.0.0.1:8188") -> dict:
    """Free ComfyUI cached models/memory if queue is idle. Idempotent."""
    result = {"freed": False, "error": ""}
    try:
        # Check queue first — refuse if work is active
        r = httpx.get(f"{comfyui_base_url}/queue", timeout=10)
        q = r.json()
        running = len(q.get("queue_running", []))
        pending = len(q.get("queue_pending", []))
        if running > 0 or pending > 0:
            result["error"] = f"Queue not idle: {running} running, {pending} pending"
            return result

        # Free memory
        r2 = httpx.post(
            f"{comfyui_base_url}/free",
            json={"unload_models": True, "free_memory": True},
            timeout=30,
        )
        result["freed"] = r2.status_code == 200
        if result["freed"]:
            logger.info("ComfyUI memory freed")
        else:
            result["error"] = f"HTTP {r2.status_code}"
    except Exception as e:
        result["error"] = str(e)
        logger.warning("Failed to free ComfyUI memory: %s", e)
    return result


def get_gpu_status(comfyui_base_url: str = "http://127.0.0.1:8188") -> dict:
    """Get GPU status from ComfyUI system_stats."""
    try:
        r = httpx.get(f"{comfyui_base_url}/system_stats", timeout=5)
        d = r.json()
        devs = d.get("devices", [])
        if devs:
            dev = devs[0]
            total = dev.get("vram_total", 0)
            free = dev.get("vram_free", 0)
            return {
                "gpu": dev.get("name", "unknown"),
                "vram_used_gb": round((total - free) / (1024 ** 3), 1),
                "vram_total_gb": round(total / (1024 ** 3), 1),
                "vram_pct": round(100 * (total - free) / total) if total else 0,
            }
    except Exception:
        pass
    return {"gpu": "unavailable", "vram_used_gb": 0, "vram_total_gb": 0, "vram_pct": 0}


def get_ollama_status(ollama_base_url: str = "http://127.0.0.1:11434") -> dict:
    """Get Ollama loaded model status."""
    try:
        r = httpx.get(f"{ollama_base_url}/api/ps", timeout=5)
        models = r.json().get("models", [])
        return {
            "status": "loaded" if models else "idle",
            "models": [m.get("name", "?") for m in models],
        }
    except Exception:
        return {"status": "unavailable", "models": []}
