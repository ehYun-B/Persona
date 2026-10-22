"""Run a reproducible, cluster-weighted LLM deliberation on a substation compensation package.

This is an exploratory policy simulation. It must not be interpreted as a vote by
real residents or as evidence of a monetary willingness-to-accept threshold.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PERSONAS_PATH = PROJECT_ROOT / "agents" / "clustered_personas.json"
LIVE_STATUS_PATH = PROJECT_ROOT / "logs" / "clustered_deliberation_live.json"
SEED = 2606211000
PASS_THRESHOLD = 2 / 3
MINIMUM_ROUNDS = 3
MAXIMUM_ROUNDS = 6

COMPONENTS = {
    "independent_safety": {
        "title": "독립 안전검증 및 상시 공개",
        "description": "사업자와 분리된 전문기관의 사전·운영 중 안전검증, 원자료 공개, 주민 설명회",
    },
    "loss_compensation": {
        "title": "피해·재산가치 손실보전",
        "description": "객관적 감정과 이의신청 절차를 갖춘 실질 손실보전 장치",
    },
    "long_term_living_support": {
        "title": "장기 생활지원",
        "description": "영향권 가구를 위한 정기적·장기적 생활지원",
    },
    "local_participation": {
        "title": "지역 우선 참여",
        "description": "주민 우선채용과 지역업체 우선계약을 위한 공개 기준",
    },
    "community_fund": {
        "title": "지역공동기금",
        "description": "주민이 사용처를 공동 결정하는 장기 지역 편익기금",
    },
    "electricity_discount": {
        "title": "전기요금 장기 할인",
        "description": "영향권 가구 대상의 장기 전기요금 할인",
    },
    "formal_council": {
        "title": "주민 공식협의체",
        "description": "주민 대표가 계획·공사·운영 단계에 참여하는 공식 협의체",
    },
    "monitoring_committee": {
        "title": "주민참여 모니터링위원회",
        "description": "주민과 독립전문가가 함께 측정·점검·공개하는 감시 체계",
    },
    "one_time_cash": {
        "title": "1회성 현금보상",
        "description": "영향권 가구에 대한 일회성 현금 지급",
    },
}


def atomic_json(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp") as file:
        json.dump(content, file, ensure_ascii=False, indent=2)
        temporary = Path(file.name)
    temporary.replace(path)


def proposal_view(component_ids: list[str]) -> dict[str, Any]:
    return {
        "component_ids": component_ids,
        "components": [{"id": item, **COMPONENTS[item]} for item in component_ids],
    }


def update_live(status: str, percent: int, action: str, **extra: Any) -> None:
    atomic_json(LIVE_STATUS_PATH, {"status": status, "percent": percent, "action": action, "updated_at": datetime.now().isoformat(timespec="seconds"), **extra})
    print(f"[{percent:>3}%] {action}", flush=True)


def parse_response(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if payload.get("stance") not in {"approve", "conditional", "oppose"}:
        return None
    ids = payload.get("required_component_ids", [])
    payload["required_component_ids"] = [item for item in ids if item in COMPONENTS]
    payload["statement"] = str(payload.get("statement", "")).strip()
    return payload


def call_ollama(prompt: str, seed: int, temperature: float = 0.25) -> tuple[dict[str, Any] | None, str]:
    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": "qwen3:8b",
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {"seed": seed, "temperature": temperature, "num_predict": 420},
        },
        timeout=180,
    )
    response.raise_for_status()
    raw = str(response.json().get("response", ""))
    return parse_response(raw), raw


def deterministic_fallback(persona: dict[str, Any], component_ids: list[str]) -> dict[str, Any]:
    profile = persona["state_profile"]
    preferences = persona["compensation_preferences"]
    needed: list[str] = []
    if profile["risk_perception"] >= 3.8:
        needed.extend(["independent_safety", "monitoring_committee"])
    if profile["trust"] <= 3.1:
        needed.append("formal_council")
    needed.append(max(preferences, key=preferences.get))
    needed = list(dict.fromkeys(needed))
    satisfied = all(item in component_ids for item in needed)
    stance = "approve" if satisfied else ("oppose" if profile["conditional_acceptance"] < 2.5 else "conditional")
    return {
        "stance": stance,
        "required_component_ids": needed,
        "statement": "모델 응답을 확보하지 못해 사전 등록한 성향 규칙으로 생성된 보조 판단입니다.",
    }


def agent_prompt(persona: dict[str, Any], proposal: dict[str, Any], round_number: int) -> str:
    components = "\n".join(f"- {item['id']}: {item['title']} ({item['description']})" for item in proposal["components"])
    profile = json.dumps(persona["state_profile"], ensure_ascii=False)
    preferences = json.dumps(persona["compensation_preferences"], ensure_ascii=False)
    allowed = ", ".join(COMPONENTS)
    absent = [item for item in COMPONENTS if item not in proposal["component_ids"]]
    return f"""당신은 변전소 비선호 입지 보상안의 가상 숙의 패널입니다. 이는 실제 주민의견이나 실제 투표가 아닌 탐색적 연구 시뮬레이션입니다.

현재 군집 페르소나: {persona['persona_name']}
군집 비중: {persona['member_share']:.1%} ({persona['member_count']}명)
군집 지표(1~5): {profile}
보상안 선호 강도(1~5): {preferences}

라운드 {round_number}의 보상안:
{components}

현재 안에 대해서만 판단하되, 안전·절차·형평성을 함께 고려하세요.
판정 계약: 현재 안으로 수용할 수 있으면 stance는 반드시 approve이고 required_component_ids는 빈 배열입니다. 현재 안으로 수용할 수 없고 보완하면 가능하면 conditional이며, required_component_ids에는 현재 안에 없는 추가 항목만 넣습니다. 어떤 보완으로도 수용할 수 없으면 oppose입니다.
현재 안에 없는 항목: {absent}
허용되는 전체 id 목록: {allowed}
다른 설명 없이 아래 JSON 하나만 출력하세요.
{{"stance":"approve|conditional|oppose","required_component_ids":["허용된 id"],"statement":"한국어 2~3문장: 현재 판단과 반드시 필요한 변경 이유"}}"""


def moderator_summary(messages: list[dict[str, Any]], proposal: dict[str, Any], round_number: int) -> str:
    digest = "\n".join(f"- {item['persona_name']}: {item['stance']} | {item['statement']}" for item in messages)
    components = ", ".join(item["title"] for item in proposal["components"])
    prompt = f"""당신은 중립적인 숙의 진행자입니다. 실제 주민 의견으로 일반화하지 말고 가상 패널 시뮬레이션이라고 명시하지도 마세요.
라운드 {round_number}, 현재 안: {components}
발언:
{digest}
공통 쟁점, 남은 쟁점, 다음 보완의 방향을 한국어 3문장 이내로 요약하세요. 새로운 보상 항목을 발명하지 마세요."""
    try:
        response = requests.post("http://localhost:11434/api/generate", json={"model": "qwen3:8b", "prompt": prompt, "stream": False, "think": False, "options": {"seed": SEED + round_number, "temperature": 0.2, "num_predict": 260}}, timeout=180)
        response.raise_for_status()
        return str(response.json().get("response", "")).strip()
    except requests.RequestException:
        return "요약 생성 실패: 원문 발언과 가중 투표 결과를 직접 확인해야 합니다."


def select_next_component(personas: list[dict[str, Any]], component_ids: list[str], messages: list[dict[str, Any]]) -> tuple[str | None, str]:
    scores = {item: 0.0 for item in COMPONENTS if item not in component_ids}
    reasons: dict[str, list[str]] = {item: [] for item in scores}
    for persona, message in zip(personas, messages):
        weight = persona["member_share"]
        for item, preference in persona["compensation_preferences"].items():
            if item in scores:
                scores[item] += weight * preference
        for item in message["required_component_ids"]:
            if item in scores:
                scores[item] += weight * 2.0
                reasons[item].append(persona["persona_name"])
        state = persona["state_profile"]
        if state["risk_perception"] >= 3.7:
            for item in ("independent_safety", "monitoring_committee"):
                if item in scores:
                    scores[item] += weight * 1.5
        if state["trust"] <= 3.2 and "formal_council" in scores:
            scores["formal_council"] += weight * 1.5
    if not scores:
        return None, "추가 가능한 보상 요소가 없습니다."
    selected = max(scores, key=scores.get)
    requesters = ", ".join(reasons[selected]) or "가중 보상선호와 쟁점 점수"
    return selected, f"{requesters}의 조건 요청과 군집 가중 선호점수가 가장 높아 추가"


def evaluate(messages: list[dict[str, Any]], personas: list[dict[str, Any]], component_ids: list[str]) -> dict[str, float | bool]:
    approve = conditional = oppose = effective = 0.0
    for persona, message in zip(personas, messages):
        weight = float(persona["member_share"])
        stance = message["stance"]
        if stance == "approve":
            approve += weight
            effective += weight
        elif stance == "conditional":
            conditional += weight
        else:
            oppose += weight
    return {
        "approve_weight": round(approve, 4),
        "conditional_weight": round(conditional, 4),
        "oppose_weight": round(oppose, 4),
        "effective_support_weight": round(effective, 4),
        "passed": effective >= PASS_THRESHOLD and oppose < (1 - PASS_THRESHOLD),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="군집 가중 변전소 보상안 숙의 시뮬레이션")
    parser.add_argument("--personas", type=Path, default=PERSONAS_PATH)
    args = parser.parse_args()
    personas = json.loads(args.personas.read_text(encoding="utf-8"))["personas"]
    initial_rank = sorted(
        COMPONENTS,
        key=lambda item: sum(persona["member_share"] * persona["compensation_preferences"].get(item, 0) for persona in personas),
        reverse=True,
    )
    component_ids = list(dict.fromkeys(["independent_safety", initial_rank[0], initial_rank[1]]))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = PROJECT_ROOT / "logs" / f"clustered_compensation_deliberation_{timestamp}.json"
    experiment: dict[str, Any] = {
        "metadata": {"created_at": datetime.now().isoformat(timespec="seconds"), "seed": SEED, "population": 1000, "pass_threshold": PASS_THRESHOLD, "minimum_rounds": MINIMUM_ROUNDS, "maximum_rounds": MAXIMUM_ROUNDS, "scope": "탐색적 가상 패널 시뮬레이션; 실제 주민 투표·금전 최소보상 추정이 아님"},
        "personas": personas,
        "initial_proposal": proposal_view(component_ids),
        "rounds": [],
        "proposal_changes": [],
    }
    update_live("running", 2, "군집 페르소나와 초기 보상안 준비", experiment=experiment, current_proposal=proposal_view(component_ids))

    for round_number in range(1, MAXIMUM_ROUNDS + 1):
        proposal = proposal_view(component_ids)
        messages: list[dict[str, Any]] = []
        for index, persona in enumerate(personas, start=1):
            stage = (round_number - 1) * (len(personas) + 2) + index
            percent = 5 + int(stage / (MAXIMUM_ROUNDS * (len(personas) + 2)) * 84)
            update_live("running", percent, f"라운드 {round_number}: {persona['persona_name']} 발언 생성", experiment=experiment, current_round=round_number, current_proposal=proposal, messages=messages)
            raw = ""
            try:
                parsed, raw = call_ollama(agent_prompt(persona, proposal, round_number), SEED + round_number * 100 + index)
            except requests.RequestException as error:
                parsed = None
                raw = f"Ollama 요청 실패: {error}"
            judgement = parsed or deterministic_fallback(persona, component_ids)
            # A conditional response is only meaningful if it names a change not
            # already in the proposal. This keeps the voting rule auditable.
            missing = [item for item in judgement["required_component_ids"] if item not in component_ids]
            statement = judgement["statement"].lower()
            for item, definition in COMPONENTS.items():
                if item not in component_ids and (item.lower() in statement or definition["title"].lower() in statement):
                    missing.append(item)
            judgement["required_component_ids"] = list(dict.fromkeys(missing))
            if judgement["stance"] == "conditional" and not judgement["required_component_ids"]:
                judgement["stance"] = "oppose"
                judgement["statement"] += " (조건부 찬성의 미충족 항목이 구조화되어 있지 않아 보수적으로 반대로 판정함.)"
            messages.append({"cluster_id": persona["cluster_id"], "persona_name": persona["persona_name"], "member_share": persona["member_share"], **judgement, "raw_response": raw, "fallback_used": parsed is None})

        summary_stage = (round_number - 1) * (len(personas) + 2) + len(personas) + 1
        update_live("running", 5 + int(summary_stage / (MAXIMUM_ROUNDS * (len(personas) + 2)) * 84), f"라운드 {round_number}: 진행자 쟁점 요약", experiment=experiment, current_round=round_number, current_proposal=proposal, messages=messages)
        summary = moderator_summary(messages, proposal, round_number)
        vote = evaluate(messages, personas, component_ids)
        record = {"round": round_number, "proposal": proposal, "messages": messages, "moderator_summary": summary, "weighted_vote": vote}
        experiment["rounds"].append(record)
        passed = bool(vote["passed"])
        if passed and round_number >= MINIMUM_ROUNDS:
            break
        next_component, reason = select_next_component(personas, component_ids, messages)
        if next_component is None:
            break
        before = list(component_ids)
        component_ids.append(next_component)
        experiment["proposal_changes"].append({"after_round": round_number, "from_component_ids": before, "to_component_ids": list(component_ids), "added_component": next_component, "reason": reason})
        change_stage = (round_number - 1) * (len(personas) + 2) + len(personas) + 2
        update_live("running", 5 + int(change_stage / (MAXIMUM_ROUNDS * (len(personas) + 2)) * 84), f"라운드 {round_number}: 보상안 보완 - {COMPONENTS[next_component]['title']}", experiment=experiment, current_round=round_number, current_proposal=proposal_view(component_ids), messages=messages)

    final_round = experiment["rounds"][-1]
    experiment["final_result"] = {"proposal": final_round["proposal"], "weighted_vote": final_round["weighted_vote"], "decision": "가결" if final_round["weighted_vote"]["passed"] else "미가결"}
    atomic_json(log_path, experiment)
    update_live("completed", 100, f"완료: {experiment['final_result']['decision']}", experiment=experiment, log_path=str(log_path), current_round=final_round["round"], current_proposal=final_round["proposal"])
    print(f"RESULT_LOG={log_path}")


if __name__ == "__main__":
    main()
