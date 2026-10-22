from __future__ import annotations

import csv
import copy
import contextlib
import importlib.util
import json
import os
import shutil
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from flask import Flask, jsonify, render_template, request, send_file

from research_app.core import ALL_FIELDS, COMPENSATION, DISPLAY, FEATURES, analyze_survey, analyze_survey_automatically, atomic_json, auto_mapping, automatic_analysis_plan, background_data_source_health, build_panel, new_session_id, read_json, read_table
from research_app.region_catalog import find_region, region_catalog
from research_app.result_analysis import build_result_report
from research_app.report_export import create_report_docx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    install_root = Path(sys.executable).resolve().parents[2]
    DATA_ROOT = install_root if (install_root / "agents").exists() else Path(os.environ.get("LOCALAPPDATA", Path.home())) / "UDL_Pesona"
else:
    DATA_ROOT = PROJECT_ROOT
SESSION_ROOT = DATA_ROOT / "research_sessions"
TEMPLATE_PATH = APP_ROOT / "facility_templates.json"
GUIDE_PATH = APP_ROOT / "facility_deliberation_guides.json"
ENGINE_PATH = PROJECT_ROOT / "scripts" / "run_natural_deliberation.py"
RUNNING: dict[str, threading.Thread] = {}
PANEL_RUNNING: dict[str, threading.Thread] = {}
ENGINE_LOCK = threading.Lock()


def load_engine() -> Any:
    """Load the bundled engine module without launching another app process."""
    if not ENGINE_PATH.exists():
        raise FileNotFoundError("scripts/run_natural_deliberation.py를 찾지 못했습니다.")
    spec = importlib.util.spec_from_file_location("resident_deliberation_engine", ENGINE_PATH)
    if not spec or not spec.loader:
        raise RuntimeError("토론 엔진을 불러올 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def repeat_summary(current_discussion: dict[str, Any], current_report: dict[str, Any]) -> dict[str, Any]:
    """Summarize only genuinely linked repeat runs; old standalone sessions stay isolated."""
    metadata = current_discussion.get("metadata", {})
    group = str(metadata.get("run_group") or "")
    if not group or group == "단일 실행":
        outcome = current_report.get("outcome", {})
        return {"group": "단일 실행", "runs": [{"run_index": 1, "seed": metadata.get("seed"), "label": outcome.get("label"), "acceptance": f"{outcome.get('acceptance_count', 0)}/{outcome.get('panel_size', 0)}", "turns": len(current_discussion.get("turns", []))}], "distribution": {outcome.get("label", "분석 대기"): 1}}
    rows: list[dict[str, Any]] = []
    engine = load_engine()
    for candidate in SESSION_ROOT.iterdir() if SESSION_ROOT.exists() else []:
        if not candidate.is_dir():
            continue
        discussion = read_json(candidate / "discussion.json", {})
        candidate_meta = discussion.get("metadata", {})
        if candidate_meta.get("run_group") != group:
            continue
        outcome = discussion.get("analysis_outcome") or engine.derive_analysis_outcome(discussion)
        rows.append({"run_index": int(candidate_meta.get("run_index", 1)), "seed": candidate_meta.get("seed", "미기록"), "label": outcome.get("label", "분석 대기"), "acceptance": f"{outcome.get('acceptance_count', 0)}/{outcome.get('panel_size', 0)}", "turns": len(discussion.get("turns", [])), "session_id": candidate.name})
    rows.sort(key=lambda item: item["run_index"])
    distribution: dict[str, int] = {}
    for row in rows:
        distribution[row["label"]] = distribution.get(row["label"], 0) + 1
    return {"group": group, "runs": rows, "distribution": distribution}


def discussion_worker(panel_path: Path, scenario_path: Path, live_path: Path, output_path: Path, console_path: Path, error_path: Path, stop_path: Path, seed: int = 26080601, run_group: str = "단일 실행", run_index: int = 1) -> None:
    with ENGINE_LOCK, console_path.open("w", encoding="utf-8") as stdout, error_path.open("w", encoding="utf-8") as stderr:
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                engine = load_engine()
                engine.run_deliberation(panel_path, 120, None, scenario_path, live_path, output_path, "qwen3:30b", stop_path, 0, 0, 0, seed, run_group, run_index)
        except Exception as error:  # Keep an inspectable traceback in the app's log panel.
            traceback.print_exc(file=stderr)
            current = read_json(live_path, {"percent": 0, "experiment": {}})
            current.update({"status": "error", "action": f"토론 엔진 오류: {error}", "error": str(error)})
            atomic_json(live_path, current)


def update_panel_live(path: Path, status: str, percent: int, action: str, **extra: Any) -> None:
    atomic_json(path, {"status": status, "percent": max(0, min(100, percent)), "action": action,
                       "updated_at": datetime.now().isoformat(timespec="seconds"), **extra})


def panel_worker(session_id: str) -> None:
    """Build a panel off the request thread so slow background lookup stays observable."""
    folder = session_dir(session_id)
    live_path, error_path = folder / "panel_live.json", folder / "panel_error.log"
    try:
        update_panel_live(live_path, "running", 8, "설문 군집 결과와 대상 지역을 확인하는 중")
        config = load_config(session_id)
        analysis = read_json(folder / "analysis.json", {})
        if not analysis.get("agents"):
            raise ValueError("먼저 설문 분석을 실행하세요.")
        update_panel_live(live_path, "running", 25, "군집별 설문 비율을 계산하고 대표 주민을 고르는 중")
        personas = analysis.get("personas", [])
        update_panel_live(live_path, "running", 45, f"{len(personas)}개 군집의 패널 배분을 준비하는 중")
        update_panel_live(live_path, "running", 60, "NVIDIA 지역 배경 데이터셋에서 일치하는 주민 배경을 조회하는 중")
        panel = build_panel(
            analysis,
            config["district"],
            {"name": config["facility_name"], "concerns": config["concerns"]},
            config.get("province", ""),
        )
        update_panel_live(live_path, "running", 85, "주민별 배경과 발언 초점을 정리하는 중")
        atomic_json(folder / "panel.json", panel)
        write_scenario(folder, config, panel)
        update_panel_live(live_path, "completed", 100, "주민 패널 구성이 완료되었습니다.", panel_size=len(panel.get("panel", [])), background_status=panel.get("metadata", {}).get("background_status", {}))
    except Exception as error:
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        update_panel_live(live_path, "error", 0, f"주민 패널 생성 오류: {error}", error=str(error))


def session_dir(session_id: str) -> Path:
    path = SESSION_ROOT / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_storage_label(session_id: str, folder: Path, started_at: str | None = None) -> str:
    """Create one stable, human-sortable label for a discussion session."""
    config = read_json(folder / "config.json", {})
    existing = str(config.get("storage_label", "")).strip()
    if existing:
        return existing
    timestamp = started_at or str(config.get("discussion_started_at") or config.get("created_at") or datetime.now().isoformat(timespec="seconds"))
    try:
        start = datetime.fromisoformat(timestamp)
    except ValueError:
        start = datetime.now()
    date_key = start.strftime("%Y%m%d")
    same_day: list[tuple[datetime, str]] = []
    for candidate in SESSION_ROOT.iterdir() if SESSION_ROOT.exists() else []:
        if not candidate.is_dir() or candidate.name == session_id:
            continue
        other = read_json(candidate / "config.json", {})
        other_started = str(other.get("discussion_started_at", ""))
        try:
            other_time = datetime.fromisoformat(other_started)
        except ValueError:
            continue
        if other_time.strftime("%Y%m%d") == date_key:
            same_day.append((other_time, candidate.name))
    ordered = sorted([*same_day, (start, session_id)])
    sequence = ordered.index((start, session_id)) + 1
    label = f"{date_key}_{sequence:02d}차_{session_id}"
    config["discussion_started_at"] = start.isoformat(timespec="seconds")
    config["daily_discussion_number"] = sequence
    config["storage_label"] = label
    atomic_json(folder / "config.json", config)
    return label


def deliberation_guides() -> dict[str, dict[str, Any]]:
    return read_json(GUIDE_PATH, {"default": {}})


def guide_for(facility_id: str, concerns: list[str] | None = None) -> dict[str, Any]:
    guides = deliberation_guides()
    guide = copy.deepcopy(guides.get(facility_id, guides.get("default", {})))
    if concerns and not guide.get("issue_framework"):
        guide["issue_framework"] = [
            {
                "label": concern,
                "perspectives": [
                    f"{concern}을 공개 기준·독립 검증·이행 조건으로 관리할 수 있다는 관점",
                    f"{concern}의 불확실성과 지역 주민 부담이 충분히 해소되지 않았다는 관점",
                ],
                "evidence_question": f"{concern}의 판단 기준, 자료 출처, 지역 영향, 책임 주체와 미확정 사항은 무엇인가?",
            }
            for concern in concerns
        ]
    return guide


def templates() -> list[dict[str, Any]]:
    items = read_json(TEMPLATE_PATH, [])
    for item in items:
        item["deliberation_guide"] = guide_for(str(item.get("id", "default")), item.get("concerns", []))
    return items


def default_analysis() -> dict[str, Any]:
    personas = read_json(PROJECT_ROOT / "agents" / "clustered_personas.json", {"personas": []}).get("personas", [])
    validation = read_json(PROJECT_ROOT / "reports" / "cluster_validation.json", {})
    source = PROJECT_ROOT / "agents" / "clustered_synthetic_agents.csv"
    agents = pd.read_csv(source, encoding="utf-8-sig").to_dict(orient="records") if source.exists() else []
    for agent in agents:
        for key, value in list(agent.items()):
            if key not in {"agent_id", "cluster_id"}:
                agent[key] = float(value)
        agent["cluster_id"] = int(agent["cluster_id"])
    return {"input": {"valid_respondents": len(agents), "source": "기존 1,000명 가상 설문", "mapping": {}}, "validation": validation, "personas": personas, "agents": agents}


def new_session() -> str:
    session_id = new_session_id()
    folder = session_dir(session_id)
    initial_template = templates()[0]
    config = {
        "session_id": session_id, "created_at": datetime.now().isoformat(timespec="seconds"),
        "facility_id": initial_template["id"], "facility_name": initial_template["name"],
        "facility_summary": initial_template["summary"], "concerns": initial_template["concerns"],
        "initial_offer": initial_template["initial_offer"], "deliberation_guide": initial_template["deliberation_guide"],
        "province": "경기", "district": "경기-화성", "region_display": "경기도 화성시",
        "mode_label": "전국 표본 기반 지역 맥락 시뮬레이션", "model": "qwen3:30b",
    }
    atomic_json(folder / "config.json", config)
    atomic_json(folder / "analysis.json", default_analysis())
    return session_id


def load_config(session_id: str) -> dict[str, Any]:
    config = read_json(session_dir(session_id) / "config.json")
    saved_region = find_region(str(config.get("district", ""))) if config else None
    if saved_region:
        config["province"] = saved_region["province"]
        config["district"] = saved_region["district"]
        config["region_display"] = saved_region["display"]
    if not config:
        raise ValueError("연구 세션을 찾지 못했습니다.")
    config["deliberation_guide"] = guide_for(str(config.get("facility_id", "default")), config.get("concerns", []))
    return config


def write_scenario(folder: Path, config: dict[str, Any], panel_data: dict[str, Any]) -> Path:
    panel = panel_data["panel"]
    facility = config["facility_name"]
    scenario = {
        "target_living_area": f"{config['province']} {config['district']} 가상 대상 생활권",
        "agenda": f"{config['district']} 생활권의 {facility} 계획 초안을 받아들일지, 어떤 조건을 붙일지",
        "initial_package": config["initial_offer"],
        "speaker_foci": panel_data["speaker_foci"],
        "speaker_lenses": {item["speaker_id"]: f"{facility} 계획에서 자신의 생활 영향과 수용 조건을 분명히 말하는 주민" for item in panel},
        "facility_id": config.get("facility_id", "custom"),
        "facility_name": facility,
        "deliberation_guide": config.get("deliberation_guide", guide_for(str(config.get("facility_id", "default")), config.get("concerns", []))),
    }
    path = folder / "scenario.json"
    atomic_json(path, scenario)
    return path


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(APP_ROOT / "templates"), static_folder=str(APP_ROOT / "static"))
    app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/bootstrap")
    def bootstrap():
        requested_session = request.args.get("session")
        session_id = requested_session if requested_session and (SESSION_ROOT / requested_session / "config.json").exists() else new_session()
        folder = session_dir(session_id)
        return jsonify({"session_id": session_id, "templates": templates(), "regions": region_catalog(), "config": load_config(session_id),
                        "analysis": read_json(folder / "analysis.json", {}), "panel": read_json(folder / "panel.json", {}),
                        "panel_live": read_json(folder / "panel_live.json", {"status": "idle", "percent": 0, "action": "주민 패널을 생성하세요."}),
                        "discussion": read_json(folder / "live.json", {})})

    @app.get("/api/data-sources/health")
    def data_sources_health():
        return jsonify({"nvidia_personas": background_data_source_health()})

    @app.post("/api/config")
    def update_config():
        payload = request.get_json(force=True)
        config = load_config(payload["session_id"])
        template_id = payload.get("facility_id", "custom")
        chosen = next((item for item in templates() if item["id"] == template_id), None)
        custom_name = str(payload.get("custom_facility", "")).strip()
        if template_id == "custom":
            if not custom_name:
                return jsonify({"error": "직접 입력 시설명을 작성하세요."}), 400
            config.update({"facility_id": "custom", "facility_name": custom_name,
                           "facility_summary": "사용자가 검토·승인한 탐색 시나리오", "concerns": payload.get("concerns", []),
                           "initial_offer": payload.get("initial_offer", []),
                           "deliberation_guide": guide_for("default", payload.get("concerns", []))})
        elif chosen:
            selected_concerns = payload.get("concerns", chosen["concerns"])
            config.update({"facility_id": chosen["id"], "facility_name": chosen["name"], "facility_summary": chosen["summary"],
                           "concerns": selected_concerns, "initial_offer": payload.get("initial_offer", chosen["initial_offer"]),
                           "deliberation_guide": guide_for(chosen["id"], selected_concerns)})
        else:
            return jsonify({"error": "시설 템플릿을 찾지 못했습니다."}), 400
        region_id = str(payload.get("region_id", "")).strip()
        selected_region = find_region(region_id) if region_id else None
        if selected_region:
            config["province"] = selected_region["province"]
            config["district"] = selected_region["district"]
            config["region_display"] = selected_region["display"]
        else:
            # Existing sessions created before the list selector are still readable,
            # but any newly saved configuration must use a canonical list choice.
            legacy_region = find_region(str(payload.get("district", config.get("district", ""))).strip())
            if legacy_region:
                config["province"] = legacy_region["province"]
                config["district"] = legacy_region["district"]
                config["region_display"] = legacy_region["display"]
            else:
                return jsonify({"error": "목록에서 대상 지역을 선택해 주세요."}), 400
        config["mode_label"] = "전국 표본 기반 지역 맥락 시뮬레이션"
        atomic_json(session_dir(config["session_id"]) / "config.json", config)
        return jsonify({"config": config})

    @app.post("/api/survey/preview")
    def survey_preview():
        file = request.files.get("file")
        session_id = request.form.get("session_id", "")
        if not file or not session_id:
            return jsonify({"error": "세션과 설문 파일이 필요합니다."}), 400
        suffix = Path(file.filename or "survey.xlsx").suffix.lower()
        if suffix not in {".csv", ".xlsx", ".xls"}:
            return jsonify({"error": "CSV 또는 Excel 파일만 지원합니다."}), 400
        folder = session_dir(session_id)
        upload_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = folder / f"survey_{upload_id}{suffix}"
        file.save(path)
        frame = read_table(path)
        headers = [str(column) for column in frame.columns]
        return jsonify({"upload_id": path.name, "headers": headers, "rows": int(len(frame)), "auto_mapping": auto_mapping(headers), "auto_recommendation": automatic_analysis_plan(frame),
                        "required": FEATURES, "optional": COMPENSATION, "display": DISPLAY})

    @app.post("/api/survey/analyze")
    def survey_analyze():
        payload = request.get_json(force=True)
        folder = session_dir(payload["session_id"])
        path = folder / payload["upload_id"]
        if not path.exists():
            return jsonify({"error": "업로드한 설문 파일을 찾지 못했습니다."}), 400
        try:
            analysis = analyze_survey(read_table(path), payload.get("mapping", {}), int(payload.get("min_k", 2)), int(payload.get("max_k", 6)), int(payload.get("seed", 2606211000)))
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        analysis["input"]["source_file"] = path.name
        atomic_json(folder / "analysis.json", analysis)
        return jsonify({"analysis": analysis})

    @app.post("/api/survey/analyze/automatic")
    def survey_analyze_automatic():
        payload = request.get_json(force=True)
        folder = session_dir(payload["session_id"])
        path = folder / payload["upload_id"]
        if not path.exists():
            return jsonify({"error": "업로드한 설문 파일을 찾지 못했습니다."}), 400
        try:
            analysis = analyze_survey_automatically(
                read_table(path), int(payload.get("min_k", 2)), int(payload.get("max_k", 6)), int(payload.get("seed", 2606211000))
            )
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        analysis["input"]["source_file"] = path.name
        atomic_json(folder / "analysis.json", analysis)
        return jsonify({"analysis": analysis})

    @app.post("/api/panel/generate")
    def panel_generate():
        payload = request.get_json(force=True)
        session_id = payload["session_id"]
        folder = session_dir(session_id)
        existing = PANEL_RUNNING.get(session_id)
        if existing and existing.is_alive():
            return jsonify({"error": "이 세션의 주민 패널이 이미 생성 중입니다."}), 409
        analysis = read_json(folder / "analysis.json", {})
        if not analysis.get("agents"):
            return jsonify({"error": "먼저 설문 분석을 실행하세요."}), 400
        update_panel_live(folder / "panel_live.json", "running", 3, "주민 패널 생성 작업을 시작하는 중")
        worker = threading.Thread(target=panel_worker, args=(session_id,), daemon=True)
        PANEL_RUNNING[session_id] = worker
        worker.start()
        return jsonify({"started": True})

    @app.get("/api/panel/status")
    def panel_status():
        session_id = request.args["session"]
        folder = session_dir(session_id)
        live = read_json(folder / "panel_live.json", {"status": "idle", "percent": 0, "action": "주민 패널을 생성하세요."})
        worker = PANEL_RUNNING.get(session_id)
        if worker and not worker.is_alive() and live.get("status") == "running":
            live = {**live, "status": "error", "action": "주민 패널 생성 작업이 중단되었습니다. 오류를 확인하세요."}
        error_path = folder / "panel_error.log"
        return jsonify({"live": live, "running": bool(worker and worker.is_alive()), "panel": read_json(folder / "panel.json", {}),
                        "error_log": error_path.read_text(encoding="utf-8", errors="replace")[-4000:] if error_path.exists() else ""})

    @app.post("/api/discussion/start")
    def discussion_start():
        payload = request.get_json(force=True)
        session_id = payload["session_id"]
        folder = session_dir(session_id)
        existing = RUNNING.get(session_id)
        if existing and existing.is_alive():
            return jsonify({"error": "이 세션의 토론이 이미 진행 중입니다."}), 409
        panel_path = folder / "panel.json"
        if not panel_path.exists():
            return jsonify({"error": "먼저 주민 패널을 생성하세요."}), 400
        ensure_storage_label(session_id, folder, datetime.now().isoformat(timespec="seconds"))
        scenario_path = folder / "scenario.json"
        live_path = folder / "live.json"
        output_path = folder / "discussion.json"
        console_path = folder / "discussion_console.log"
        error_path = folder / "discussion_error.log"
        stop_path = folder / "stop.request"
        if stop_path.exists():
            stop_path.unlink()
        seed = int(payload.get("seed", 26080601))
        run_group = str(payload.get("run_group") or session_id)
        run_index = max(1, int(payload.get("run_index", 1)))
        try:
            worker = threading.Thread(target=discussion_worker, args=(panel_path, scenario_path, live_path, output_path, console_path, error_path, stop_path, seed, run_group, run_index), daemon=True)
            RUNNING[session_id] = worker
            worker.start()
        except RuntimeError as error:
            return jsonify({"error": str(error)}), 500
        return jsonify({"started": True})

    @app.post("/api/discussion/repeat")
    def discussion_repeat():
        """Clone prepared inputs and run the same design with a fresh documented seed."""
        source_id = request.get_json(force=True)["session_id"]
        source = session_dir(source_id)
        if not (source / "panel.json").exists() or not (source / "scenario.json").exists():
            return jsonify({"error": "반복 실행에는 기존 주민 패널과 시나리오가 필요합니다."}), 400
        source_discussion = read_json(source / "discussion.json", {})
        source_meta = source_discussion.get("metadata", {})
        run_group = str(source_meta.get("run_group") or source_id)
        existing = 0
        for candidate in SESSION_ROOT.iterdir() if SESSION_ROOT.exists() else []:
            record = read_json(candidate / "discussion.json", {}) if candidate.is_dir() else {}
            if record.get("metadata", {}).get("run_group") == run_group:
                existing += 1
        run_index = existing + 1
        seed = 26080601 + run_index * 1009
        session_id = new_session_id()
        target = session_dir(session_id)
        for name in ("config.json", "analysis.json", "panel.json", "scenario.json"):
            shutil.copy2(source / name, target / name)
        config = read_json(target / "config.json", {})
        config["repeat_of"] = source_id
        config["run_group"] = run_group
        config["run_index"] = run_index
        atomic_json(target / "config.json", config)
        ensure_storage_label(session_id, target, datetime.now().isoformat(timespec="seconds"))
        panel_path, scenario_path = target / "panel.json", target / "scenario.json"
        live_path, output_path = target / "live.json", target / "discussion.json"
        console_path, error_path, stop_path = target / "discussion_console.log", target / "discussion_error.log", target / "stop.request"
        worker = threading.Thread(target=discussion_worker, args=(panel_path, scenario_path, live_path, output_path, console_path, error_path, stop_path, seed, run_group, run_index), daemon=True)
        RUNNING[session_id] = worker
        worker.start()
        return jsonify({"started": True, "session_id": session_id, "seed": seed, "run_group": run_group, "run_index": run_index})

    @app.post("/api/discussion/stop")
    def discussion_stop():
        session_id = request.get_json(force=True)["session_id"]
        worker = RUNNING.get(session_id)
        if not worker or not worker.is_alive():
            return jsonify({"error": "진행 중인 토론이 없습니다."}), 409
        (session_dir(session_id) / "stop.request").write_text("requested", encoding="utf-8")
        return jsonify({"stopping": True})

    @app.get("/api/discussion/status")
    def discussion_status():
        session_id = request.args["session"]
        folder = session_dir(session_id)
        live = read_json(folder / "live.json", {"status": "idle", "percent": 0, "action": "토론을 시작하세요."})
        worker = RUNNING.get(session_id)
        if worker and not worker.is_alive() and live.get("status") == "running":
            live["status"] = "error"
            live["action"] = "토론 프로세스가 중단되었습니다. 오류 로그를 확인하세요."
        error_file = folder / "discussion_error.log"
        console_file = folder / "discussion_console.log"
        return jsonify({
            "live": live,
            "running": bool(worker and worker.is_alive()),
            "error_log": error_file.read_text(encoding="utf-8", errors="replace")[-6000:] if error_file.exists() else "",
            "console_log": console_file.read_text(encoding="utf-8", errors="replace")[-3000:] if console_file.exists() else "",
        })

    @app.get("/api/results")
    def results():
        session_id = request.args["session"]
        folder = session_dir(session_id)
        discussion = read_json(folder / "discussion.json", {})
        if discussion:
            try:
                discussion["analysis_outcome"] = load_engine().derive_analysis_outcome(discussion)
            except Exception:
                pass
        analysis = read_json(folder / "analysis.json", {})
        config = load_config(session_id)
        report = build_result_report(discussion, analysis, config) if discussion else {}
        if discussion:
            report["repeat_summary"] = repeat_summary(discussion, report)
        return jsonify({"discussion": discussion, "analysis": analysis, "config": config, "report": report})

    @app.get("/api/results/report.docx")
    def results_report_docx():
        session_id = request.args["session"]
        folder = session_dir(session_id)
        discussion = read_json(folder / "discussion.json", {})
        if not discussion:
            return jsonify({"error": "완료된 토론 기록이 없습니다."}), 404
        discussion["analysis_outcome"] = load_engine().derive_analysis_outcome(discussion)
        analysis = read_json(folder / "analysis.json", {})
        config = load_config(session_id)
        report = build_result_report(discussion, analysis, config)
        report["repeat_summary"] = repeat_summary(discussion, report)
        label = ensure_storage_label(session_id, folder, discussion.get("metadata", {}).get("created_at"))
        export_path = folder / f"{label}_보고서.docx"
        create_report_docx(export_path, discussion, config, report)
        response = send_file(export_path, as_attachment=True, download_name=f"{label}_보고서.docx", max_age=0)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["X-Report-Bytes"] = str(export_path.stat().st_size)
        return response

    @app.get("/api/survey/template")
    def survey_template():
        path = SESSION_ROOT / "standard_survey_template.csv"
        if not path.exists():
            with path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["response_id", *FEATURES, *COMPENSATION])
                writer.writerow(["RESP-0001", *([4] * len(FEATURES)), *([4] * len(COMPENSATION))])
        return send_file(path, as_attachment=True, download_name="비선호시설_군집분석_표준입력양식.csv")

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8765, debug=False)
