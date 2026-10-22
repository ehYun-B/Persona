from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "llm_config.yaml"
AGENTS_PATH = PROJECT_ROOT / "agents" / "sample_resident_agents.json"
PROMPT_PATH = PROJECT_ROOT / "prompts" / "resident_agent_prompt.txt"
LOGS_DIR = PROJECT_ROOT / "logs"


class OllamaRequestError(RuntimeError):
    """Raised when the local Ollama request cannot be completed."""


def read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"YAML 설정을 읽을 수 없습니다: {path}")
    return data


def read_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list) or not data:
        raise ValueError(f"주민 에이전트 JSON은 비어 있지 않은 리스트여야 합니다: {path}")
    return data


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build_prompt(prompt_template: str, agent: dict[str, Any], scenario: str) -> str:
    return prompt_template.format(scenario=scenario, **agent)


def request_ollama(config: dict[str, Any], prompt: str) -> dict[str, Any]:
    model = config.get("model")
    base_url = config.get("base_url")
    temperature = config.get("temperature", {}).get("resident", 0.6)

    if model != "qwen3:8b":
        raise ValueError('현재 MVP는 model이 반드시 "qwen3:8b"여야 합니다.')

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
        },
    }

    try:
        response = requests.post(base_url, json=payload, timeout=120)
        response.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        raise OllamaRequestError(
            "Ollama 서버에 연결할 수 없습니다. PowerShell에서 `ollama serve` 또는 "
            "Ollama 앱 실행 상태를 확인한 뒤 다시 시도하세요."
        ) from exc
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        raise OllamaRequestError(
            "Ollama API 요청이 실패했습니다. 모델명이 qwen3:8b인지 확인하고 "
            "`ollama pull qwen3:8b`를 먼저 실행하세요.\n"
            f"상세 응답: {body}"
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise OllamaRequestError(
            "Ollama 응답 시간이 초과되었습니다. 모델 로딩 중이면 잠시 후 다시 실행하세요."
        ) from exc

    result = response.json()
    if not isinstance(result, dict):
        raise OllamaRequestError("Ollama 응답 형식이 예상과 다릅니다.")
    return result


def save_log(
    model: str,
    agent: dict[str, Any],
    scenario: str,
    prompt: str,
    response_text: str,
    raw_result: dict[str, Any],
) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    safe_timestamp = timestamp.replace(":", "-")
    log_path = LOGS_DIR / f"ollama_test_{safe_timestamp}.json"

    log_data = {
        "timestamp": timestamp,
        "model": model,
        "agent": agent,
        "scenario": scenario,
        "prompt": prompt,
        "response": response_text,
        "raw_result": raw_result,
    }

    log_path.write_text(
        json.dumps(log_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return log_path


def main() -> None:
    config = read_yaml(CONFIG_PATH)
    agents = read_json(AGENTS_PATH)
    prompt_template = read_text(PROMPT_PATH)

    agent = agents[0]
    scenario = (
        "지자체가 지역 내 비선호 공공시설 입지 후보지를 설명하고, "
        "주민 의견을 듣기 위한 1차 공청회를 열었다. "
        "아직 세부 안전 대책과 보상 기준은 확정되지 않았다."
    )

    prompt = build_prompt(prompt_template, agent, scenario)
    result = request_ollama(config, prompt)
    response_text = str(result.get("response", "")).strip()

    print("\n=== 주민 에이전트 발언 ===")
    print(response_text)

    log_path = save_log(
        model=str(config["model"]),
        agent=agent,
        scenario=scenario,
        prompt=prompt,
        response_text=response_text,
        raw_result=result,
    )
    print(f"\n로그 저장 완료: {log_path}")


if __name__ == "__main__":
    try:
        main()
    except OllamaRequestError as error:
        print(f"\n[Ollama 오류] {error}")
    except FileNotFoundError as error:
        print(f"\n[파일 오류] 필요한 파일을 찾을 수 없습니다: {error.filename}")
    except Exception as error:
        print(f"\n[실행 오류] {error}")
