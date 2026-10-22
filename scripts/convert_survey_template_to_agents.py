from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_CSV = PROJECT_ROOT / "data_sandbox" / "survey_agent_template.csv"
OUTPUT_JSON = PROJECT_ROOT / "agents" / "generated_resident_agents.json"

REQUIRED_COLUMNS = [
    "agent_id",
    "cluster_name",
    "risk_perception",
    "benefit_perception",
    "distributive_fairness",
    "procedural_fairness",
    "trust",
    "place_attachment",
    "social_influence",
    "conditional_acceptance",
    "conflict_action_likelihood",
]

NUMERIC_COLUMNS = REQUIRED_COLUMNS[2:]


def print_progress(percent: int, message: str) -> None:
    print(f"[{percent:3d}%] {message}", flush=True)


def read_csv(path: Path) -> list[dict[str, str]]:
    print_progress(10, f"설문 상태값 CSV 읽는 중: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
    if not rows:
        raise ValueError("CSV에 변환할 행이 없습니다.")
    return rows


def validate_columns(rows: list[dict[str, str]]) -> None:
    print_progress(35, "필수 컬럼 확인 중")
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in rows[0]]
    if missing_columns:
        raise ValueError(f"필수 컬럼이 없습니다: {', '.join(missing_columns)}")


def parse_score(value: str, column: str, row_number: int) -> float:
    try:
        score = float(value)
    except ValueError as exc:
        raise ValueError(f"{row_number}행 {column} 값이 숫자가 아닙니다: {value}") from exc

    if not 1.0 <= score <= 5.0:
        raise ValueError(f"{row_number}행 {column} 값은 1~5 사이여야 합니다: {score}")
    return score


def rows_to_agents(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    print_progress(60, "상태값 범위 검증 및 JSON 구조 변환 중")
    agents: list[dict[str, Any]] = []

    for index, row in enumerate(rows, start=2):
        agent_id = row["agent_id"].strip()
        cluster_name = row["cluster_name"].strip()

        if not agent_id:
            raise ValueError(f"{index}행 agent_id가 비어 있습니다.")
        if not cluster_name:
            raise ValueError(f"{index}행 cluster_name이 비어 있습니다.")

        agent: dict[str, Any] = {
            "agent_id": agent_id,
            "cluster_name": cluster_name,
        }
        for column in NUMERIC_COLUMNS:
            agent[column] = parse_score(row[column].strip(), column, index)
        agents.append(agent)

    return agents


def save_agents(path: Path, agents: list[dict[str, Any]]) -> None:
    print_progress(85, f"에이전트 JSON 저장 중: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(agents, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    try:
        rows = read_csv(INPUT_CSV)
        validate_columns(rows)
        agents = rows_to_agents(rows)
        save_agents(OUTPUT_JSON, agents)
        print_progress(100, f"완료: {len(agents)}개 에이전트를 생성했습니다.")
    except Exception as error:
        print(f"\n[변환 오류] {error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()