from __future__ import annotations

import argparse
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "llm_config.yaml"
SAMPLE_AGENTS_PATH = PROJECT_ROOT / "agents" / "sample_resident_agents.json"
GENERATED_AGENTS_PATH = PROJECT_ROOT / "agents" / "generated_resident_agents.json"
SCENARIOS_PATH = PROJECT_ROOT / "scenarios" / "sample_scenarios.json"
PROMPT_PATH = PROJECT_ROOT / "prompts" / "resident_agent_prompt.txt"
LOGS_DIR = PROJECT_ROOT / "logs"


class OllamaRequestError(RuntimeError):
    """Raised when the local Ollama request cannot be completed."""


def print_progress(percent: int, message: str) -> None:
    print(f"[{percent:3d}%] {message}", flush=True)


def wait_progress(stop_event: threading.Event, start_percent: int, end_percent: int) -> None:
    current = start_percent
    while not stop_event.wait(4):
        current = min(current + 3, end_percent)
        print_progress(current, "Ollama 응답 대기 중입니다. 모델 로딩 또는 생성이 진행 중일 수 있습니다.")


def read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"YAML 설정을 읽을 수 없습니다: {path}")
    return data


def read_json_list(path: Path, label: str) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list) or not data:
        raise ValueError(f"{label} 파일은 비어 있지 않은 JSON 리스트여야 합니다: {path}")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError(f"{label} 파일의 각 항목은 JSON 객체여야 합니다: {path}")
    return data


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def choose_agents_path(explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()
    if GENERATED_AGENTS_PATH.exists():
        return GENERATED_AGENTS_PATH
    return SAMPLE_AGENTS_PATH


def build_prompt(prompt_template: str, agent: dict[str, Any], scenario: dict[str, Any]) -> str:
    scenario_text = str(scenario["description"])
    return prompt_template.format(scenario=scenario_text, **agent)


def request_ollama(config: dict[str, Any], prompt: str, start_percent: int, end_percent: int) -> dict[str, Any]:
    model = config.get("model")
    base_url = config.get("base_url")
    temperature = config.get("temperature", {}).get("resident", 0.6)

    if model != "qwen3:8b":
        raise ValueError('현재 MVP는 model이 반드시 "qwen3:8b"여야 합니다.')

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }

    stop_event = threading.Event()
    reporter = threading.Thread(
        target=wait_progress,
        args=(stop_event, start_percent, max(start_percent, end_percent - 5)),
        daemon=True,
    )

    try:
        reporter.start()
        response = requests.post(base_url, json=payload, timeout=180)
        response.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        raise OllamaRequestError(
            "Ollama 서버에 연결할 수 없습니다. Ollama 앱 실행 상태를 확인하거나 "
            "PowerShell에서 `ollama serve`를 실행한 뒤 다시 시도하세요."
        ) from exc
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        raise OllamaRequestError(
            "Ollama API 요청이 실패했습니다. `ollama pull qwen3:8b`와 `ollama list`를 확인하세요.\n"
            f"상세 응답: {body}"
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise OllamaRequestError(
            "Ollama 응답 시간이 초과되었습니다. 모델 로딩이 오래 걸리면 다시 실행하거나 GPU/메모리 상태를 확인하세요."
        ) from exc
    finally:
        stop_event.set()
        reporter.join(timeout=1)

    result = response.json()
    if not isinstance(result, dict):
        raise OllamaRequestError("Ollama 응답 형식이 예상과 다릅니다.")
    return result


def save_run_log(records: list[dict[str, Any]], dry_run: bool) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    safe_timestamp = timestamp.replace(":", "-")
    log_path = LOGS_DIR / f"simulation_run_{safe_timestamp}.json"
    log_data = {
        "timestamp": timestamp,
        "dry_run": dry_run,
        "record_count": len(records),
        "records": records,
    }
    log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return log_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="주민 에이전트 공청회 시뮬레이션 실행")
    parser.add_argument(
        "--agents",
        default=None,
        help="사용할 에이전트 JSON 경로입니다. 생략하면 generated_resident_agents.json이 있으면 우선 사용합니다.",
    )
    parser.add_argument(
        "--max-scenarios",
        type=int,
        default=0,
        help="실행할 시나리오 개수 제한입니다. 0이면 전체 실행합니다.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ollama 요청 없이 입력 파일과 프롬프트 생성만 검증합니다.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print_progress(5, "설정 파일 읽는 중")
    config = read_yaml(CONFIG_PATH)

    agents_path = choose_agents_path(args.agents)
    print_progress(15, f"에이전트 파일 읽는 중: {agents_path}")
    agents = read_json_list(agents_path, "에이전트")

    print_progress(25, f"시나리오 파일 읽는 중: {SCENARIOS_PATH}")
    scenarios = read_json_list(SCENARIOS_PATH, "시나리오")
    if args.max_scenarios > 0:
        scenarios = scenarios[: args.max_scenarios]

    print_progress(35, "주민 에이전트 프롬프트 읽는 중")
    prompt_template = read_text(PROMPT_PATH)

    total_calls = len(agents) * len(scenarios)
    print_progress(45, f"실행 대상 준비 완료: 에이전트 {len(agents)}명 x 시나리오 {len(scenarios)}개 = {total_calls}회")

    records: list[dict[str, Any]] = []
    for index, (agent, scenario) in enumerate(
        ((agent, scenario) for agent in agents for scenario in scenarios),
        start=1,
    ):
        base_percent = 45 + int((index - 1) / max(total_calls, 1) * 50)
        done_percent = 45 + int(index / max(total_calls, 1) * 50)
        agent_id = str(agent.get("agent_id", "unknown_agent"))
        scenario_id = str(scenario.get("scenario_id", "unknown_scenario"))

        print_progress(base_percent, f"요청 준비 중 ({index}/{total_calls}): {agent_id} / {scenario_id}")
        prompt = build_prompt(prompt_template, agent, scenario)

        if args.dry_run:
            response_text = "[DRY RUN] Ollama 요청 없이 프롬프트 생성만 확인했습니다."
            raw_result: dict[str, Any] = {"dry_run": True}
        else:
            print_progress(base_percent + 1, "Ollama 요청 전송")
            raw_result = request_ollama(config, prompt, base_percent + 2, done_percent)
            response_text = str(raw_result.get("response", "")).strip()

        print_progress(done_percent, f"응답 처리 완료 ({index}/{total_calls})")
        print("\n=== 주민 에이전트 발언 ===")
        print(f"에이전트: {agent_id}")
        print(f"시나리오: {scenario.get('title', scenario_id)}")
        print(response_text)
        print()

        records.append(
            {
                "model": config.get("model"),
                "agent": agent,
                "scenario": scenario,
                "prompt": prompt,
                "response": response_text,
                "raw_result": raw_result,
            }
        )

    print_progress(97, "실행 로그 저장 중")
    log_path = save_run_log(records, args.dry_run)
    print_progress(100, f"전체 완료. 로그 저장 위치: {log_path}")


def run() -> None:
    try:
        main()
    except OllamaRequestError as error:
        print(f"\n[Ollama 오류] {error}")
        raise SystemExit(1) from error
    except FileNotFoundError as error:
        print(f"\n[파일 오류] 필요한 파일을 찾을 수 없습니다: {error.filename}")
        raise SystemExit(1) from error
    except Exception as error:
        print(f"\n[실행 오류] {error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    run()