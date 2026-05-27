from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def register_app_shell_surface(app: FastAPI, *, cfg, cm) -> None:
    ui_dir = Path(__file__).resolve().parent.parent.parent.parent / "ui" / "public"
    if ui_dir.exists():
        app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")

    @app.get("/")
    def serve_index():
        index = ui_dir / "index.html" if ui_dir.exists() else None
        if index and index.exists():
            return FileResponse(str(index))
        return {"message": "Luxar API - visit /docs for Swagger UI"}

    @app.get("/api/config")
    def get_config():
        return cfg.model_dump(mode="json")

    @app.put("/api/config")
    async def update_config(body: dict):
        if "llm" in body:
            for k, v in body["llm"].items():
                if hasattr(cfg.llm, k):
                    setattr(cfg.llm, k, v)
        if "api_keys" in body and isinstance(body["api_keys"], dict):
            cfg.api_keys.update(body["api_keys"])
        from ruamel.yaml import YAML

        _yaml = YAML(typ="safe")
        config_path = cm.config_path
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with config_path.open("w", encoding="utf-8") as f:
            _yaml.dump(cfg.model_dump(mode="json"), f)
        return {"status": "ok", "config": cfg.model_dump(mode="json")}
