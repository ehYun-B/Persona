"""Local dashboard that prefers the newest natural deliberation run."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Flask, jsonify, render_template


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NATURAL_LIVE = PROJECT_ROOT / "logs" / "natural_deliberation_live.json"
LEGACY_LIVE = PROJECT_ROOT / "logs" / "clustered_deliberation_live.json"
PERSONAS_PATH = PROJECT_ROOT / "agents" / "clustered_personas.json"
VALIDATION_PATH = PROJECT_ROOT / "reports" / "cluster_validation.json"

app = Flask(__name__, template_folder=str(PROJECT_ROOT / "dashboard" / "templates"), static_folder=str(PROJECT_ROOT / "dashboard" / "static"))


def read_json(path: Path, default: dict | None = None) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    live = read_json(NATURAL_LIVE) or read_json(LEGACY_LIVE, {"status": "idle", "percent": 0, "action": "대기 중"})
    return jsonify({"live": live, "personas": read_json(PERSONAS_PATH, {"personas": []}), "validation": read_json(VALIDATION_PATH, {})})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5050")), debug=False)
