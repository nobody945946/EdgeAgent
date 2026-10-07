"""Model downloads and switches started from the UI: one at a time, in the
background, with the progress the model list shows."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from edge_agent.config import MODEL_SETTINGS, required_string, validated_config
from edge_agent.deploy import download
from edge_agent.models import hub_catalog, model_filename, model_metadata


class ModelJobs:
    """The running or last model job; its lock keeps one at a time."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.current: dict[str, object] = {"state": "idle"}

    def start(
        self,
        action: str,
        payload: dict[str, object],
        *,
        config: dict[str, object],
        model_dir: Path,
        switch: Callable[[dict[str, object]], object],
    ) -> dict[str, object]:
        """Check the request against `config`, then run it in the background;
        a switch is carried out by `switch`."""
        if not self.lock.acquire(blocking=False):
            raise PermissionError("模型下載或切換正在進行")
        try:
            if action == "switch":
                model_filename(payload.get("model"))
                if set(payload) - MODEL_SETTINGS:
                    raise ValueError("unknown model settings")
                validated_config({**config, **payload})
            elif action == "download":
                model_filename(Path(required_string(payload, "filename")).name)
                required_string(payload, "repository")
            else:
                raise ValueError("unknown model action")
            self.current = {
                "id": uuid4().hex,
                "state": "running",
                "action": action,
                "received": 0,
                "total": 0,
                "error": "",
            }
            threading.Thread(
                target=self._run,
                args=(action, dict(payload), model_dir, switch),
                name="model-" + action,
                daemon=True,
            ).start()
            return dict(self.current)
        except Exception:
            self.lock.release()
            raise

    def _run(
        self,
        action: str,
        payload: dict[str, object],
        model_dir: Path,
        switch: Callable[[dict[str, object]], object],
    ) -> None:
        try:
            if action == "download":
                self._download(payload, model_dir)
            else:
                switch(payload)
            self.current = {**self.current, "state": "done"}
        except Exception as exc:  # noqa: BLE001 - report background failures to UI
            self.current = {**self.current, "state": "failed", "error": str(exc)}
        finally:
            self.lock.release()

    def _download(self, payload: dict[str, object], model_dir: Path) -> None:
        catalog = hub_catalog(payload["repository"])
        entry = next(
            (
                entry
                for entry in catalog["files"]
                if entry["filename"] == payload["filename"]
            ),
            None,
        )
        if entry is None:
            raise ValueError("找不到可驗證的單檔 GGUF")
        name = model_filename(Path(str(entry["filename"])).name)
        if entry.get("projector"):
            repository = str(payload["repository"]).split("/")[-1]
            name = "mmproj-" + repository + "-" + name.removeprefix("mmproj-")
        target = model_dir / name
        if target.exists():
            raise ValueError("此檔名已存在；請直接選擇已下載的模型")

        def progress(received: int, total: int) -> None:
            self.current = {
                **self.current,
                "received": received,
                "total": total,
                "filename": name,
            }

        download(
            str(entry["url"]), target, str(entry["sha256"]), progress=progress
        )
        metadata = model_metadata(target)
        if metadata["adapter"] or metadata["split"]:
            raise ValueError(
                "下載完成，但此檔是配件或分片，不能作為獨立回覆模型"
            )
