"""Run a compact, stratified virtual-resident deliberation with Ollama."""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AGENTS = PROJECT_ROOT / "agents" / "generated_substation_agents.json"
DEFAULT_SCENARIOS = PROJECT_ROOT / "scenarios" / "substation_scenarios.json"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "llm_config.yaml"
RESIDENT_PROMPT = PROJECT_ROOT / "prompts" / "substation_resident_agent_prompt.txt"
MODERATOR_PROMPT = PROJECT_ROOT / "prompts" / "substation_moderator_prompt.txt"


def report_progress(percent: int, message: str) -> None:
    print(f"[{percent:>3}%] {message}", flush=True)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def select_panel(agents: list[dict[str, Any]], panel_size: int, seed: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for agent in agents:
        grouped[agent["cluster_name"]].append(agent)
    if panel_size < len(grouped):
        raise ValueError(f"패널 수는 최소 {len(grouped)}명이어야 합니다.")

    generator = random.Random(seed)
    total_agents = len(agents)
    quotas = {
        name: max(1, round(len(group) / total_agents * panel_size))
        for name, group in grouped.items()
    }
    while sum(quotas.values()) > panel_size:
        name = max(quotas, key=lambda key: (quotas[key], len(grouped[key])))
        if quotas[name] > 1:
            quotas[name] -= 1
        else:
            break
    while sum(quotas.values()) < panel_size:
        name = max(grouped, key=lambda key: len(grouped[key]) / max(quotas[key], 1))
        quotas[name] += 1

    panel: list[dict[str, Any]] = []
    for name in sorted(grouped):
        candidates = grouped[name]
        panel.extend(generator.sample(candidates, min(quotas[name], len(candidates))))
    return panel


def generate_with_progress(
    config: dict[str, Any],
    prompt: str,
    progress_percent: int,
    label: str,
    dry_run: bool,
    max_tokens: int | None = None,
) -> str:
    if dry_run:
        return f"[DRY-RUN] {label}: 가상 응답이 생성되었습니다."

    response_holder: dict[str, Any] = {}
    error_holder: dict[str, BaseException] = {}
    complete = threading.Event()

    def call_ollama() -> None:
        try:
            response = requests.post(
                config["api_url"],
                json={
                    "model": config["model"],
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": config.get("temperature", 0.4),
                        "num_predict": max_tokens or config.get("max_tokens", 350),
                    },
                },
                timeout=config.get("timeout_seconds", 180),
            )
            response.raise_for_status()
            response_holder["text"] = response.json().get("response", "").strip()
        except BaseException as error:  # Surface the original network/model failure to the caller.
            error_holder["error"] = error
        finally:
            complete.set()

    threading.Thread(target=call_ollama, daemon=True).start()
    waited_seconds = 0
    while not complete.wait(timeout=10):
        waited_seconds += 10
        report_progress(progress_percent, f"{label} 응답 대기 중... {waited_seconds}초 경과")
    if "error" in error_holder:
        raise RuntimeError(f"{label} 호출 실패: {error_holder['error']}") from error_holder["error"]
    response_text = str(response_holder.get("text") or "")
    if not response_text:
        raise RuntimeError(f"{label}에서 최종 발언이 생성되지 않았습니다.")
    return response_text


def main() -> None:
    parser = argparse.ArgumentParser(description="변전소 가상 주민 패널 토론을 실행합니다.")
    parser.add_argument("--agents", type=Path, default=DEFAULT_AGENTS)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--panel-size", type=int, default=12)
    parser.add_argument("--max-scenarios", type=int, default=None)
    parser.add_argument("--seed", type=int, default=2606211000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report_progress(5, "변전소 토론 설정 읽는 중")
    raw_config = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    config = {
        "api_url": raw_config["base_url"],
        "model": raw_config["model"],
        "timeout_seconds": 180,
        "temperature": raw_config.get("temperature", {}).get("resident", 0.4),
        "max_tokens": min(raw_config.get("max_tokens", {}).get("resident", 350), 350),
    }
    agent_data = load_json(args.agents)
    agents = agent_data["agents"]
    scenarios = load_json(args.scenarios)
    if args.max_scenarios is not None:
        scenarios = scenarios[: args.max_scenarios]
    resident_template = RESIDENT_PROMPT.read_text(encoding="utf-8")
    moderator_template = MODERATOR_PROMPT.read_text(encoding="utf-8")

    report_progress(15, f"1,000명 중 대표 패널 {args.panel_size}명 층화 추출 중")
    panel = select_panel(agents, args.panel_size, args.seed)
    total_calls = len(panel) * len(scenarios) + len(scenarios)
    completed_calls = 0
    moderator_context = "이전 라운드가 아직 없습니다."
    rounds: list[dict[str, Any]] = []

    report_progress(20, f"토론 시작: {len(scenarios)}개 시나리오, 총 {total_calls}회 생성")
    for scenario_number, scenario in enumerate(scenarios, start=1):
        statements: list[dict[str, str]] = []
        for panel_number, agent in enumerate(panel, start=1):
            state = agent["state"]
            prompt = resident_template.format(
                **state,
                cluster_name=agent["cluster_name"],
                scenario_text=scenario["text"],
                moderator_context=moderator_context,
            )
            percent = 20 + round(completed_calls / total_calls * 75)
            label = f"시나리오 {scenario_number}/{len(scenarios)}, 패널 {panel_number}/{len(panel)}"
            text = generate_with_progress(config, prompt, percent, label, args.dry_run)
            statements.append({"agent_id": agent["agent_id"], "cluster": agent["cluster_name"], "text": text})
            completed_calls += 1
            report_progress(20 + round(completed_calls / total_calls * 75), f"{label} 완료")

        discussion_text = "\n\n".join(
            f"[{statement['cluster']} / {statement['agent_id']}]\n{statement['text']}"
            for statement in statements
        )
        moderator_prompt = moderator_template.format(
            scenario_text=scenario["text"], discussion_text=discussion_text
        )
        percent = 20 + round(completed_calls / total_calls * 75)
        summary = generate_with_progress(
            config,
            moderator_prompt,
            percent,
            f"시나리오 {scenario_number} 진행자",
            args.dry_run,
            max_tokens=500,
        )
        completed_calls += 1
        moderator_context = summary
        rounds.append({"scenario": scenario, "statements": statements, "moderator_summary": summary})
        report_progress(20 + round(completed_calls / total_calls * 75), f"시나리오 {scenario_number} 요약 완료")

    report_progress(97, "토론 로그 저장 중")
    output_path = PROJECT_ROOT / "logs" / f"substation_deliberation_{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "facility_focus": "substation",
                "dry_run": args.dry_run,
                "panel_size": len(panel),
                "panel_clusters": [agent["cluster_name"] for agent in panel],
                "rounds": rounds,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report_progress(100, f"전체 완료. 로그 저장 위치: {output_path}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"\n[실행 오류] {error}")
        raise SystemExit(1) from error
