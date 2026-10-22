"""Build a nine-resident panel by combining survey clusters with synthetic Korean life contexts.

The external persona data only supplies a non-decisive life context. Cluster
features and survey-derived red lines remain the sole basis for siting attitudes.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENTS_PATH = PROJECT_ROOT / "agents" / "clustered_synthetic_agents.csv"
OUTPUT_PATH = PROJECT_ROOT / "agents" / "natural_deliberation_panel.json"
SEED = 26080601
DATASET = "nvidia/Nemotron-Personas-Korea"
TARGET_DISTRICT = "경기-화성시"
TARGET_LIVING_AREA = "경기도 화성시 가상 대상 생활권"


def load_agents() -> list[dict[str, Any]]:
    with AGENTS_PATH.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    for row in rows:
        row["cluster_id"] = int(row["cluster_id"])
        for key, value in list(row.items()):
            if key not in {"agent_id", "cluster_id"}:
                row[key] = float(value)
    return rows


def select_cluster_members(rows: list[dict[str, Any]], cluster_id: int) -> list[dict[str, Any]]:
    members = [row for row in rows if row["cluster_id"] == cluster_id]
    features = [key for key in members[0] if key not in {"agent_id", "cluster_id"} and key not in COMPENSATION_KEYS]
    matrix = np.array([[row[key] for key in features] for row in members])
    center = matrix.mean(axis=0)
    distance = np.linalg.norm(matrix - center, axis=1)
    medoid = members[int(np.argmin(distance))]
    high_conflict = max(members, key=lambda row: row["conflict_action_likelihood"] + row["risk_perception"] - row["conditional_acceptance"])
    flexible = max(members, key=lambda row: row["conditional_acceptance"] + row["social_influence"] + row["procedural_fairness"])
    chosen = []
    for member in (medoid, high_conflict, flexible):
        if member["agent_id"] not in {item["agent_id"] for item in chosen}:
            chosen.append(member)
    for member in sorted(members, key=lambda row: row["agent_id"]):
        if len(chosen) == 3:
            break
        if member["agent_id"] not in {item["agent_id"] for item in chosen}:
            chosen.append(member)
    return chosen


def fetch_backgrounds(count: int) -> list[dict[str, Any]]:
    """Fetch only records from one declared living area, then diversify life context."""
    candidates: list[dict[str, Any]] = []
    last_error: requests.RequestException | None = None
    for length in (100, 50, 25, 12):
        try:
            response = requests.get(
                "https://datasets-server.huggingface.co/filter",
                params={
                    "dataset": DATASET,
                    "config": "default",
                    "split": "train",
                    "where": f'"district" = \'{TARGET_DISTRICT}\'',
                    "offset": 0,
                    "length": length,
                },
                timeout=45,
            )
            response.raise_for_status()
            candidates = [item["row"] for item in response.json()["rows"]]
            break
        except requests.RequestException as error:
            last_error = error
    if not candidates and last_error:
        raise last_error
    if len(candidates) < count:
        raise ValueError(f"{TARGET_DISTRICT} 배경 후보가 {count}명보다 적습니다.")

    # The locality is fixed; occupation, housing, and household type are spread
    # out so a nine-person panel does not collapse into a single life story.
    selected: list[dict[str, Any]] = []
    used = {"occupation": set(), "housing_type": set(), "family_type": set()}
    remaining = sorted(candidates, key=lambda row: str(row["uuid"]))
    while remaining and len(selected) < count:
        best = max(
            remaining,
            key=lambda row: sum(row[key] not in used[key] for key in used),
        )
        selected.append(best)
        for key in used:
            used[key].add(best[key])
        remaining.remove(best)
    return selected


COMPENSATION_KEYS = {
    "electricity_discount",
    "one_time_cash",
    "long_term_living_support",
    "community_fund",
    "local_participation",
    "loss_compensation",
    "formal_council",
    "monitoring_committee",
}


def trait_thresholds(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Calculate all behavior proxies from the N=1,000 synthetic sample."""
    def quantile(key: str, value: float) -> float:
        return float(np.quantile([row[key] for row in rows], value))

    return {
        "risk_high": quantile("risk_perception", 2 / 3),
        "trust_low": quantile("trust", 1 / 3),
        "acceptance_low": quantile("conditional_acceptance", 1 / 3),
        "acceptance_high": quantile("conditional_acceptance", 2 / 3),
        "conflict_high": quantile("conflict_action_likelihood", 2 / 3),
        "conflict_very_high": quantile("conflict_action_likelihood", 0.8),
    }


def conversation_traits(row: dict[str, Any], thresholds: dict[str, float]) -> dict[str, Any]:
    high_risk = row["risk_perception"] >= thresholds["risk_high"]
    low_trust = row["trust"] <= thresholds["trust_low"]
    low_acceptance = row["conditional_acceptance"] <= thresholds["acceptance_low"]
    high_conflict = row["conflict_action_likelihood"] >= thresholds["conflict_high"]
    hardline = (high_risk and low_acceptance) or row["conflict_action_likelihood"] >= thresholds["conflict_very_high"]
    return {
        "assertiveness": "높음" if high_conflict else "보통",
        "turn_taking_risk": "높음" if high_conflict and low_acceptance else "보통",
        "concession_tendency": "낮음" if hardline else ("높음" if row["conditional_acceptance"] >= thresholds["acceptance_high"] else "보통"),
        "hardline": hardline,
        "red_line": "건강·안전 우려가 해소되지 않으면 금전 보상만으로는 입장을 바꾸지 않음" if high_risk else ("주민의 실제 의사결정 권한이 보장되지 않으면 합의하지 않음" if low_trust else "피해보전과 지역 편익이 불명확하면 찬성하지 않음"),
        "negotiable": "독립 검증, 참여권, 피해보전의 구체성이 확보되면 일부 조건을 조정할 수 있음" if not hardline else "핵심 안전 기준 외의 실행 순서와 지역 편익 방식은 협상 가능",
    }


def main() -> None:
    agents = load_agents()
    backgrounds = fetch_backgrounds(count=9)
    thresholds = trait_thresholds(agents)
    selected = [member for cluster_id in (1, 2, 3) for member in select_cluster_members(agents, cluster_id)]
    panel = []
    for index, (agent, background) in enumerate(zip(selected, backgrounds), start=1):
        feature_keys = [key for key in agent if key not in {"agent_id", "cluster_id"} and key not in COMPENSATION_KEYS]
        compensation = {key: agent[key] for key in COMPENSATION_KEYS}
        traits = conversation_traits(agent, thresholds)
        panel.append(
            {
                "speaker_id": f"R{index:02d}",
                "display_name": f"주민 {index}",
                "source_agent_id": agent["agent_id"],
                "cluster_id": agent["cluster_id"],
                "survey_profile": {key: agent[key] for key in feature_keys},
                "compensation_preferences": compensation,
                "conversation_traits": traits,
                "background": {
                    "source": "nvidia/Nemotron-Personas-Korea (CC BY 4.0)",
                    "source_uuid": background["uuid"],
                    "age": background["age"],
                    "sex": background["sex"],
                    "occupation": background["occupation"],
                    "district": background["district"],
                    "housing_type": background["housing_type"],
                    "family_type": background["family_type"],
                    "persona": background["persona"],
                    "cultural_background": background["cultural_background"],
                    "family_persona": background["family_persona"],
                },
            }
        )
    output = {
        "metadata": {
            "created_by": "create_natural_deliberation_panel.py",
            "seed": SEED,
            "target_living_area": TARGET_LIVING_AREA,
            "background_selection": f"Nemotron-Personas-Korea에서 district가 정확히 '{TARGET_DISTRICT}'인 후보만 필터링한 뒤, 직업·주거형태·가구형태 다양성을 우선해 9명을 선정했습니다.",
            "design_note": "Nemotron 배경서사는 동일 생활권의 대화 맥락용이며, 입지 태도·보상 선호·고정 조건은 설문 기반 군집 특성에서만 산출했습니다.",
            "trait_operationalization": {
                "assertiveness": "가상표본의 conflict_action_likelihood 상위 1/3이면 높음, 그 외 보통",
                "concession_tendency": "conditional_acceptance 하위 1/3 및 고정입장 조건이면 낮음, 상위 1/3이면 높음, 그 외 보통",
                "turn_taking_risk": "갈등행동 가능성이 상위 1/3이고 조건부 수용성이 하위 1/3일 때만 높음. 실제 관찰 성격이 아닌 사회자 개입을 위한 운영 변수",
                "thresholds": thresholds,
            },
            "attribution": "NVIDIA, Nemotron-Personas-Korea, CC BY 4.0",
        },
        "panel": panel,
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PANEL={OUTPUT_PATH}")


if __name__ == "__main__":
    main()
