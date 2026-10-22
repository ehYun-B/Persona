"""Naturalistic, moderated resident deliberation with explicit consensus closure.

Speech is generated as conversational Korean. JSON is used only as an internal
recording envelope, so the displayed transcript remains a natural dialogue.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import tempfile
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = PROJECT_ROOT / "agents" / "natural_deliberation_panel.json"
LIVE_PATH = PROJECT_ROOT / "logs" / "natural_deliberation_live.json"
SEED = 26080601
DEFAULT_MODEL = "qwen3:30b"
DEFAULT_MAX_TURNS = 120
DEFAULT_MIN_TURNS_BEFORE_UNRESOLVED = 120
DEFAULT_MIN_TURNS_BEFORE_QUALIFIED_CONSENSUS = 90
PROMPT_VERSION = "2026.08.19.autonomy.1"
FACT_BOUNDARY_VERSION = "scenario-only-v2"
MODEL = DEFAULT_MODEL
FACILITY_NAME = "변전소"
TARGET_LIVING_AREA = "경기도 화성시 가상 대상 생활권"
AGENDA = f"{TARGET_LIVING_AREA}의 주거지 인근 변전소 사업자 초안을 받아들일지, 어떤 조건을 붙일지"
INITIAL_PACKAGE = [
    "사업자 초안: 주거지 경계로부터 최소 600m 지점에 변전소 설치, 공사기간 18개월",
    "안전 검토: 사업자가 선정한 기관의 결과 요약본만 공개",
    "보상: 공사 완료 뒤 확인되는 피해만 개별 심사, 생활지원은 5년 한정",
    "주민 참여: 분기별 설명회는 열되, 주민 의견은 자문으로만 반영",
]
DELIBERATION_GUIDE: dict[str, Any] = {
    "methodology_label": "일반 시설 입지 주민 숙의",
    "source_title": "시설별 사용자 입력 시나리오",
    "source_note": "시설별 주요 쟁점과 최초 제안은 사용자가 검토한 시나리오를 기준으로 합니다.",
    "process_principles": ["사실, 주장, 불확실성을 구분한다."],
    "issue_framework": [],
    "evidence_questions": [],
    "deliberation_stages": [
        {"name": "안건 확인", "goal": "결정 범위를 확인한다."},
        {"name": "쟁점 교환", "goal": "필요성과 부담을 함께 듣는다."},
        {"name": "근거 확인", "goal": "확인할 질문을 기록한다."},
        {"name": "조건 조정", "goal": "수정안을 조정한다."},
        {"name": "전원 확인", "goal": "합의 여부를 확인한다."},
    ],
    "scenario_limits": ["입력 자료에 없는 수치를 만들지 않는다."],
}

# Conversation lenses preserve variation within survey-derived cluster profiles.
# They do not replace survey attitudes or add individual survey facts.
SPEAKER_LENSES = {
    "R01": "안전 검증 전에는 보상액 논의가 앞서간다고 보는 강경한 안전 우선 입장",
    "R02": "사업자 선정 검증의 독립성과 공개 범위를 기술적으로 따져 묻는 입장",
    "R03": "일상생활 피해를 걱정하지만 검증 절차와 대응 창구가 갖춰지면 조건부로 검토할 수 있는 입장",
    "R04": "주민이 설명만 듣는 구조에는 반대하며 실질적 공동결정권을 요구하는 입장",
    "R05": "추상적 약속보다 이행 주체와 위반 시 책임을 따져 묻는 절차 불신의 입장",
    "R06": "공공 필요성은 이해하지만 주민 통제 장치와 실무 보완책을 찾는 입장",
    "R07": "장기 거주자의 생활 안정과 취약가구 보호를 우선하는 신중한 보상·돌봄 요구 입장",
    "R08": "피해가 생긴 뒤에만 판단하는 보상 방식에 반대하고 선제적·형평적 보전을 요구하는 강경 입장",
    "R09": "청년과 지역 생활의 지속가능성을 따지며 지역 환원과 미래 부담을 묻는 조건부 입장",
}

SPEAKER_FOCUSES = {
    "R01": "공사 전 독립 안전검증과 검증 실패 시 착공 보류",
    "R02": "검증기관 선정 절차, 원자료와 전문 공개 범위",
    "R03": "공사기간 일상 피해의 사전 보호와 민원 대응",
    "R04": "주민대표가 참여하는 공동감시단의 권한과 의사결정 절차",
    "R05": "약속 불이행 때 공사를 멈추게 할 계약상 책임과 이행 보증",
    "R06": "사업 필요성과 주민 통제 장치를 함께 성립시키는 현실적 절차",
    "R07": "공사기간 소음·진동 측정, 장기 생활지원, 고령·취약가구 보호",
    "R08": "피해 발생 뒤 심사가 아닌 선제적·형평적 보상과 재산 손실 보전",
    "R09": "청년과 지역 생활을 위한 지역기금·생활환경 개선·장기 환원",
}

SPEECH_ACTS = ("입장 표명", "질문", "반박", "조건부 양보", "대안 제시", "절차 이의")

COMPENSATION_LABELS = {
    "electricity_discount": "전기요금 감면",
    "one_time_cash": "일시금 지급",
    "long_term_living_support": "장기 생활지원",
    "community_fund": "지역공동기금",
    "local_participation": "주민참여 방식",
    "loss_compensation": "재산·생활 손실 보전",
    "formal_council": "공식 주민협의체",
    "monitoring_committee": "주민 감시기구",
}

def contains_unsupported_detail(text: str) -> bool:
    """Block fabricated monetary, distance, duration, and place-specific conditions."""
    compact = re.sub(r"\s+", "", text)
    initial_text = " ".join(INITIAL_PACKAGE)
    allowed_numbers = set(re.findall(r"\d[\d,.]*(?:\s*(?:km|m|개월|년|원|명|%|회|일))?", initial_text, flags=re.IGNORECASE))
    for allowed in sorted(allowed_numbers, key=len, reverse=True):
        compact = compact.replace(re.sub(r"\s+", "", allowed), "")
    unsupported_institutional_terms = ("법정", "법률", "현행 규정", "법적 의무", "국가 기준")
    initial_text_compact = re.sub(r"\s+", "", initial_text)
    has_unprovided_institutional_term = any(
        term in text and re.sub(r"\s+", "", term) not in initial_text_compact
        for term in unsupported_institutional_terms
    )
    return bool(re.search(r"\d", compact)) or "학교" in text or "학군" in text or has_unprovided_institutional_term


def is_repetitive_speech(text: str, previous_turns: list[dict[str, Any]]) -> bool:
    """Reject copied or near-copied resident wording before it reaches the transcript."""
    candidate = re.sub(r"[^가-힣a-z0-9]", "", text.lower())
    if len(candidate) < 18:
        return False
    for turn in previous_turns[-12:]:
        prior = re.sub(r"[^가-힣a-z0-9]", "", str(turn.get("spoken_text", "")).lower())
        if len(prior) < 18:
            continue
        if candidate == prior or candidate in prior or prior in candidate:
            return True
        if SequenceMatcher(None, candidate, prior).ratio() >= 0.84:
            return True
    return False


def guide_issue_text() -> str:
    issues = DELIBERATION_GUIDE.get("issue_framework", [])
    if not issues:
        return "사용자가 입력한 시설 쟁점을 기준으로 논의합니다."
    rows = []
    for item in issues:
        perspectives = " / ".join(str(value) for value in item.get("perspectives", []))
        rows.append(f"- {item.get('label', '쟁점')}: {perspectives} 확인 질문: {item.get('evidence_question', '')}")
    return "\n".join(rows)


def focus_guide_text(focus: str) -> str:
    for item in DELIBERATION_GUIDE.get("issue_framework", []):
        if str(item.get("label", "")) == focus:
            perspectives = " / ".join(str(value) for value in item.get("perspectives", []))
            return f"맞서는 관점: {perspectives}\n확인할 질문: {item.get('evidence_question', '')}"
    return "서로 다른 관점을 확인하고, 근거가 없으면 질문으로 남기세요."


def guide_rules_text() -> str:
    values = (
        DELIBERATION_GUIDE.get("process_principles", [])
        + DELIBERATION_GUIDE.get("dialogue_rules", [])
        + DELIBERATION_GUIDE.get("scenario_limits", [])
    )
    return "\n".join(f"- {value}" for value in values)


def dialogue_rules_text() -> str:
    values = DELIBERATION_GUIDE.get("dialogue_rules", [])
    return "\n".join(f"- {value}" for value in values) if values else "- 앞선 발언의 구체적 주장에 자연스럽게 반응하세요."


def evidence_questions_text() -> str:
    values = DELIBERATION_GUIDE.get("evidence_questions", [])
    return "\n".join(f"- {value}" for value in values) if values else "- 주장에 사용한 자료의 출처와 확인 여부"


def stage_index(experiment: dict[str, Any]) -> int:
    """Retained only for backwards-compatible logs; it no longer drives dialogue."""
    return 0


def stage_text(experiment: dict[str, Any]) -> str:
    return "자율 논의 - 주민 발언에서 쟁점과 조건이 형성되는 중"


def usable_revised_package(candidate: Any) -> list[str]:
    if not isinstance(candidate, list):
        return []
    cleaned: list[str] = []
    for item in candidate:
        clause = str(item).strip()
        if clause and clause not in cleaned and not contains_unsupported_detail(clause):
            cleaned.append(clause)
    return cleaned if 3 <= len(cleaned) <= 6 else []


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        temp = Path(file.name)
    for attempt in range(8):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.08 * (attempt + 1))


def update_live(percent: int, action: str, experiment: dict[str, Any], **extra: Any) -> None:
    atomic_json(
        LIVE_PATH,
        {
            "status": "running",
            "percent": percent,
            "action": action,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_seconds": elapsed_seconds(experiment),
            "experiment": experiment,
            **extra,
        },
    )
    print(f"[{percent:>3}%] {action}", flush=True)


def elapsed_seconds(experiment: dict[str, Any]) -> int:
    """Report wall-clock session duration without treating pauses as new sessions."""
    created = experiment.get("metadata", {}).get("created_at")
    ended = experiment.get("completed_at")
    if not created:
        return 0
    try:
        start = datetime.fromisoformat(str(created))
        finish = datetime.fromisoformat(str(ended)) if ended else datetime.now()
    except ValueError:
        return 0
    return max(0, int((finish - start).total_seconds()))


def extract_json(text: str) -> dict[str, Any] | None:
    """Return the last complete JSON object if a model prepends extra text."""
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    return candidates[-1] if candidates else None


def ask(
    prompt: str,
    seed: int,
    max_tokens: int = 620,
    required_keys: tuple[str, ...] = (),
    validator: Any = None,
    validation_note: str = "",
) -> tuple[dict[str, Any], str]:
    raw = ""
    last_candidate: dict[str, Any] | None = None
    for attempt in range(5):
        retry_note = "" if attempt == 0 else (
            "\n직전 출력은 JSON이 완성되지 않았거나 필수 항목이 빠졌습니다. "
            "문장 설명 없이, 모든 키와 닫는 }를 포함한 JSON 객체 하나만 짧게 출력하세요."
            + (f" {validation_note}" if validation_note else "")
        )
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": MODEL,
                "prompt": prompt + retry_note,
                "stream": False,
                "think": False,
                "format": "json",
                "options": {"seed": seed + attempt, "temperature": 0.78 if attempt == 0 else 0.5, "top_p": 0.92, "num_predict": max_tokens + attempt * 500},
            },
            timeout=180,
        )
        response.raise_for_status()
        raw = str(response.json().get("response", ""))
        parsed = extract_json(raw)
        if parsed and all(key in parsed for key in required_keys):
            last_candidate = parsed
            if validator is None or bool(validator(parsed)):
                return parsed, raw
    if last_candidate is not None:
        # The resident's actual wording is more important than a soft quality
        # check. The caller replaces only an unverified-detail candidate below.
        last_candidate["_validation_warning"] = validation_note or "응답 형식 보완 필요"
        return last_candidate, raw
    missing = ", ".join(required_keys) if required_keys else "JSON 객체"
    raise ValueError(f"구조화된 응답 또는 필수 항목({missing})을 받지 못했습니다: {raw[:200]}")


def fallback_moderator(experiment: dict[str, Any], must_mediate: bool, error: Exception) -> tuple[dict[str, Any], str]:
    """Keep a failed moderator turn contextual instead of replaying a stock line."""
    turns = experiment.get("turns", [])
    event_count = len(experiment.get("moderator_events", []))
    latest = turns[-1] if turns else {}
    speaker = str(latest.get("speaker_name", "방금 발언한 주민"))
    focus = SPEAKER_FOCUSES.get(str(latest.get("speaker_id", "")), "남아 있는 확인 조건")
    interventions = (
        "잠깐만요, 한 분씩 말씀하고 방금 질문부터 답해 봅시다.",
        "말이 겹치네요. 지금 나온 질문 하나만 먼저 정리하고 이어가죠.",
        "잠깐, 서로 말이 빨라졌어요. 방금 쟁점부터 차례로 답해 주세요.",
    )
    summaries = (
        f"지금까지 얘기 들어보면 {speaker}님은 {focus}이 먼저라고 보셨어요. 다음 분은 그 부분을 누가 어떻게 확인할 수 있는지에만 답해 볼게요.",
        f"그럼 이제 {speaker}님이 짚은 {focus}을 그냥 넘기지 말고 정리해 볼게요. 다음 말씀에서는 지금 안에 빠진 절차가 뭔지만 말해 주세요.",
        f"지금까지 얘기 들어보면 방금 나온 핵심은 {focus}이에요. 다음 분은 이 조건이 반영되면 어디까지 검토할 수 있는지 말씀해 주세요.",
    )
    intervention = interventions[event_count % len(interventions)] if must_mediate else ""
    return {
        "intervene": must_mediate,
        "intervention_text": intervention,
        "facilitator_summary": summaries[event_count % len(summaries)],
        "revised_package": [],
        "ready_for_confirmation": False,
        "draft_agreement": [],
        "covered_issues": [],
        "evidence_notes": ["사회자 응답 형식 오류로 이번 근거 점검을 기록하지 못함"],
        "unresolved_issues": ["사회자 응답 형식 오류로 이번 차례 수정안 보류"],
        "generation_warning": str(error),
    }, ""


def naturalize_facilitator_summary(value: Any, experiment: dict[str, Any]) -> str:
    """Keep moderator summaries conversational and never leave a blank log row."""
    text = str(value or "").strip()
    if not text:
        latest = (experiment.get("turns") or [{}])[-1]
        speaker = str(latest.get("speaker_name", "방금 발언한 주민"))
        focus = SPEAKER_FOCUSES.get(str(latest.get("speaker_id", "")), "남아 있는 확인 조건")
        return f"지금까지 얘기 들어보면 {speaker}님이 {focus}을 짚으셨어요. 다음 분은 그 조건을 어떻게 확인할지 말씀해 주세요."
    replacements = {
        "주민님들께서는": "다들",
        "요구사항": "말씀",
        "핵심 쟁점": "걸리는 부분",
        "공통점": "같이 확인한 부분",
        "다음 질문": "다음으로 볼 건",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    return text


def derive_analysis_outcome(experiment: dict[str, Any]) -> dict[str, Any]:
    """Classify a session without treating an analytical threshold as a decision rule."""
    panel_size = len(experiment.get("panel", []))
    if experiment.get("status") == "consensus_reached":
        return {
            "classification": "unanimous_consensus",
            "label": "전원 확인 합의",
            "acceptance_count": panel_size,
            "panel_size": panel_size,
            "package": experiment.get("final_agreement", []),
            "remaining_requests": [],
            "rule": "주민 발언에서 문안이 나온 뒤, 문안 검토와 전원 최종 확인이 모두 수용으로 기록됨.",
        }
    eligible: list[dict[str, Any]] = []
    observed: list[dict[str, Any]] = []
    for check_index, check in enumerate(experiment.get("interim_checks", []), start=1):
        responses = check.get("responses", [])
        accepted = sum(item.get("decision") == "accept" for item in responses)
        rejected = sum(item.get("decision") == "reject" for item in responses)
        remaining = [
            {"speaker_name": item.get("speaker_name"), "requested_change": item.get("requested_change")}
            for item in responses if item.get("decision") != "accept"
        ]
        observed.append({
            "acceptance_count": accepted,
            "rejection_count": rejected,
            "panel_size": panel_size,
            "package": check.get("revised_package", []),
            "remaining_requests": remaining,
            "source_check": check_index,
        })
        # This is an analytical label only. It never ends the conversation.
        if panel_size and accepted / panel_size >= 0.75 and rejected == 0:
            eligible.append({
                "classification": "conditional_alignment",
                "label": "조건부 정렬",
                "acceptance_count": accepted,
                "panel_size": panel_size,
                "package": check.get("revised_package", []),
                "remaining_requests": remaining,
                "source_check": check_index,
                "rule": "주민 발언에서 자발적으로 나온 문안을 검토한 결과, 75% 이상이 수용하고 명시적 거부가 없었던 관찰값. 자동 종료나 정책 가결을 뜻하지 않음.",
            })
    if eligible:
        return max(eligible, key=lambda item: (item["acceptance_count"], item["source_check"]))
    if observed:
        best = max(observed, key=lambda item: (item["acceptance_count"], -item["rejection_count"], item["source_check"]))
        return {
            "classification": "proposal_with_residual_disagreement",
            "label": "주민 문안 도출·이견 잔존",
            "acceptance_count": best["acceptance_count"],
            "panel_size": panel_size,
            "package": best["package"],
            "remaining_requests": best["remaining_requests"],
            "source_check": best["source_check"],
            "rule": "주민 발언에서 나온 문안 중 수용 인원이 가장 많았던 문안을 분석 대상으로 기록. 합의 실패가 아니라 이견의 내용도 결과로 보존함.",
        }
    return {
        "classification": "unresolved",
        "label": "문안 미형성 또는 이견 지속",
        "acceptance_count": 0,
        "panel_size": panel_size,
        "package": [],
        "remaining_requests": [],
        "rule": "안전 발언 한도 안에 주민 다수의 문안이 형성되지 않았거나, 문안 검토가 이루어지지 않음.",
    }


def has_qualified_consensus(responses: list[dict[str, Any]], panel_size: int) -> bool:
    """Return whether a pulse reached the preregistered conditional-consensus rule."""
    if not panel_size:
        return False
    accepted = sum(item.get("decision") == "accept" for item in responses)
    rejected = sum(item.get("decision") == "reject" for item in responses)
    return accepted / panel_size >= 0.75 and rejected == 0


def transcript_tail(turns: list[dict[str, Any]], limit: int = 6) -> str:
    if not turns:
        return "아직 발언이 없습니다."
    return "\n".join(
        f"[{turn['utterance_id']}] {turn['speaker_name']}: {turn['spoken_text']}"
        for turn in turns[-limit:]
    )


def latest_facilitator_summary(experiment: dict[str, Any]) -> str:
    """Expose a prior intervention only when one actually occurred."""
    events = experiment.get("moderator_events", [])
    if not events:
        return "사회자의 별도 진행 멘트는 없습니다. 회의록을 보고 스스로 반응할지 판단하세요."
    summary = str(events[-1].get("facilitator_summary", "")).strip()
    return summary or "사회자의 별도 진행 멘트는 없습니다."


def response_anchor(turns: list[dict[str, Any]], is_opening: bool) -> str:
    """Offer context without forcing an artificial adjacent-turn exchange."""
    if is_opening or not turns:
        return "첫 발언입니다. 누구에게 답하는 말이 아니라면 reply_to는 null로 두세요."
    return "최근 회의록을 보고, 실제로 답하고 싶은 이전 발언이 있을 때만 그 주민의 코드로 reply_to를 남기세요. 새 쟁점이나 자신의 판단을 말할 때는 null이어도 됩니다."


def repetition_guard(turns: list[dict[str, Any]]) -> str:
    """Give a short, concrete anti-copying cue without scripting the resident's view."""
    if not turns:
        return "아직 반복을 피할 최근 발언이 없습니다."
    recent = [str(turn.get("spoken_text", "")).replace("\n", " ")[:95] for turn in turns[-3:]]
    return " / ".join(f"'{text}'" for text in recent if text) or "최근 발언 없음"


def speech_act_instruction(experiment: dict[str, Any], is_opening: bool) -> str:
    """Leave the speech act to the resident instead of rotating a scripted move."""
    if is_opening:
        return "처음 듣고 든 생각을 말하세요. 찬반, 질문, 유보 중 어느 쪽이든 괜찮습니다."
    return "회의록을 보고 지금 자신이 할 말이 있다고 느끼는 방식으로 말하세요. 질문·반박·수용·조건 제안·짧은 유보 모두 가능합니다."


def resident_mood(resident: dict[str, Any], experiment: dict[str, Any]) -> str:
    mood = experiment.get("resident_moods", {}).get(resident["speaker_id"], {})
    return f"{mood.get('state', '신중함')} - {mood.get('reason', '아직 자신의 요구가 수정안에 반영됐는지 확인하는 중') }"


def update_resident_moods(experiment: dict[str, Any], responses: list[dict[str, Any]]) -> None:
    moods = experiment.setdefault("resident_moods", {})
    for response in responses:
        decision = response["decision"]
        if decision == "accept":
            state, reason = "차분해짐", "직전 수정안에 자신의 핵심 요구가 어느 정도 반영됨"
        elif decision == "revise":
            state, reason = "답답함", "수정안에 아직 고쳐야 할 조건이 남아 있음"
        else:
            state, reason = "격앙됨", "핵심 요구가 빠졌다고 판단해 강하게 반발하는 중"
        moods[response["speaker_id"]] = {"state": state, "reason": reason, "after_decision": decision}


def profile_text(resident: dict[str, Any]) -> str:
    traits = resident["conversation_traits"]
    background = resident["background"]
    preferences = resident["compensation_preferences"]
    top_preferences = sorted(preferences, key=lambda key: float(preferences[key]), reverse=True)[:3]
    preference_text = ", ".join(COMPENSATION_LABELS[key] for key in top_preferences)
    age = background.get("age") or "연령 정보 미연결"
    sex = background.get("sex") or "성별 정보 미연결"
    occupation = background.get("occupation") or "직업 정보 미연결"
    housing = background.get("housing_type") or "주거 정보 미연결"
    family = background.get("family_type") or "가구 정보 미연결"
    return f"""당신은 {resident['display_name']}입니다. 실제 사람이 아니라 주민 합의 예측을 위한 합성 주민 페르소나지만, 발언에서는 그 사실을 말하지 않습니다.
토론용 생활 맥락: 연령 {age}, 성별 {sex}, 직업 {occupation}, 주거 {housing}, 가구 {family}.
위 정보는 발언의 관심사와 말투를 자연스럽게 만드는 참고일 뿐입니다. 원본 배경 서사·지명·개인사는 대화에 꺼내지 말고, 이 정보를 실제 지역 사실이나 전문가 판단의 근거로 쓰지 마세요.
발언 운영성향: 주장 강도 {traits['assertiveness']}, 발언 경합 위험 {traits['turn_taking_risk']}, 양보 성향 {traits['concession_tendency']}.
절대 넘을 수 없는 선: {traits['red_line']}
협상 가능한 영역: {traits['negotiable']}
당신이 특히 중요하게 여기는 보전 방식: {preference_text}. 점수나 설문이라는 말은 하지 말고, 필요할 때만 자연스럽게 요구에 반영하세요."""


def resident_prompt(resident: dict[str, Any], experiment: dict[str, Any], is_opening: bool) -> str:
    turns = experiment["turns"]
    speakers = ", ".join(f"{person['speaker_id']}={person['display_name']}" for person in experiment["panel"])
    package = ", ".join(experiment["current_package"])
    identity_guard = """당신에게 배정된 배경은 말투와 관심사를 정하는 참고일 뿐, 회의록에서 증명처럼 꺼내는 일화가 아닙니다. 다른 주민의 직업·가족·가게·경험을 자기 일처럼 가져오지 마세요. 지역명만 보고 바다·음식·어린 시절·이웃 장면을 만들어내지 말고, 프로필에 있어도 특정 음식·가게·가족·관광지 이야기를 새로 꾸며내지 마세요. 최초 자료에 없는 법·규정·법정 기준·운영 연수·전기요금 변화도 사실처럼 단정하지 말고, 필요하면 '그건 자료로 확인해야 한다'고 질문하세요. 한 번에 한 쟁점만 다루고, 앞선 발언은 여섯 단어 이내로만 인용하세요. 직전 주민의 문장 구조나 결론을 바꿔 말하며 되풀이하지 마세요."""
    opening_rule = "회의의 첫 발언입니다. 초안을 어떻게 느끼는지 자유롭게 말하되, 아직 다른 주민의 개인 사정을 아는 척하지 마세요." if is_opening else "반드시 직전 발언에 답할 필요는 없습니다. 회의록에서 실제로 반응하고 싶은 말이나 자신의 남은 우려를 선택하세요."
    anchor = response_anchor(turns, is_opening)
    return f"""{profile_text(resident)}
{identity_guard}

회의 안건: {AGENDA}
현재 검토 중인 안: {package}
참석자: {speakers}
당신의 지속적인 관점: {SPEAKER_LENSES[resident['speaker_id']]}
당신이 특히 민감하게 보는 쟁점: {SPEAKER_FOCUSES[resident['speaker_id']]}
이번에 선택할 수 있는 발언 방식: {speech_act_instruction(experiment, is_opening)}
참고할 수 있는 균형 관점과 확인 질문: {focus_guide_text(SPEAKER_FOCUSES[resident['speaker_id']])}
현재 대화 정서: {resident_mood(resident, experiment)}
최근 사회자 개입(없을 수 있음): {latest_facilitator_summary(experiment)}
발언 연결 지시: {anchor}
공통 주민 대화 원칙:
{dialogue_rules_text()}
최근 대화:
{transcript_tail(turns)}
최근에 이미 나온 표현(그대로 반복 금지): {repetition_guard(turns)}

{opening_rule}
대화는 실제 주민 회의처럼 구어체로 말하세요. 말할 이유가 분명할 때만 1~4문장으로 말하고, 필요한 경우에만 다른 주민의 말에 직접 반응하세요. 모든 참석자는 {TARGET_LIVING_AREA}에 거주하는 것으로 설정되어 있습니다. 입력된 생활권 밖의 원래 거주지·하천·동네를 끌어오거나 개인 주택의 정확한 거리를 지어내지 마세요. 최초안에 없는 금액·거리·기간·반경·사고확률·방사선량·전기요금·발전비를 새 사실처럼 만들지 마세요. 확인되지 않은 내용은 단정하지 말고 "그 자료부터 확인해야 한다"는 질문이나 우려로 표현하세요. 자신의 생활 경험과 가치판단은 솔직히 말할 수 있지만, 전문가인 척 기술적 결론을 내리지 마세요. 검증 기준, 측정 결과, 자료 공개 범위, 주민 참여 권한은 서로 다른 항목입니다. "자료가 기준을 넘는다"처럼 서로 다른 범주를 한 조건으로 섞지 마세요. 공사 피해는 현장 측정, 기준 초과 시 작업 조정, 피해 대응과 연결하고 의학적 근거 없이 건강검진을 만능 해결책처럼 제시하지 마세요.

지나치게 점잖은 보고서 말투, 설문 수치, 군집, 프롬프트, AI라는 표현은 금지합니다. '저도 안전이 중요합니다'처럼 앞사람을 되풀이하는 말만 하지 말고, 자신이 지금 중요하다고 느끼는 손해·불신·확인 질문·양보안 중 하나를 말하세요. 다수 의견이 형성됐다는 이유만으로 입장을 바꾸지 말고, 자신의 핵심 가치나 확인된 내용이 달라졌을 때만 움직이세요. 자신의 요구가 반영된 뒤에는 그 점을 인정하고 차분해질 수 있지만, 아직 반영되지 않았다면 왜 답답한지 구체적으로 말하세요. 답답함은 '그 얘기만 반복하지 말고 누가 책임질지 말해 달라'처럼 쟁점을 향해야 하며, 사람 자체를 모욕하거나 위협하지는 마세요. 끼어들고 싶다면 실제 말처럼 '잠깐만요'나 '아니, 그 얘기는...'으로 시작해도 됩니다. 다른 발언을 수용하거나 반박할 때는 무엇에 반응하는지만 자연스럽게 밝혀 주세요.

아래 JSON 하나만 출력하세요. spoken_text에는 실제 회의록에 남길 말만 쓰세요. reply_to는 참석자 목록에 있는 코드 하나만 정확히 쓰세요. 예를 들어 R05이며 R005, U010, 주민 5는 금지입니다. 답할 대상이 없으면 null입니다.
{{"spoken_text":"구어체 1~4문장","reply_to":"R01 또는 null","stance":"찬성|조건부|반대|유보","tone":"차분함|단호함|격앙됨","tone_reason":"이번 발언이 그 톤인 대화 맥락","interrupts":true|false,"speech_act":"입장 표명|질문|반박|조건부 양보|대안 제시|절차 이의","proposal":"새 제안 또는 null","claim_type":"생활경험|가치판단|확인질문|근거주장|불확실성","evidence_needed":true|false}}"""


def normalize_reply_to(value: Any, previous_turns: list[dict[str, Any]], speaker_id: str) -> tuple[str | None, str | None, bool]:
    """Keep reply links machine-readable without concealing a model formatting repair."""
    raw_value = str(value or "").strip()
    if not raw_value or raw_value.lower() in {"null", "none", "없음"}:
        return None, None, False
    candidate = raw_value.upper().replace(" ", "")
    prior_speakers = {str(turn.get("speaker_id", "")) for turn in previous_turns}
    by_utterance = {str(turn.get("utterance_id", "")).upper(): str(turn.get("speaker_id", "")) for turn in previous_turns}
    if candidate in by_utterance:
        repaired = by_utterance[candidate]
        return (repaired if repaired != speaker_id else None), raw_value, repaired != raw_value
    match = re.fullmatch(r"R0*(\d{1,2})", candidate)
    if match:
        repaired = f"R{int(match.group(1)):02d}"
        if repaired in prior_speakers and repaired != speaker_id:
            return repaired, raw_value, repaired != raw_value
    if candidate in prior_speakers and candidate != speaker_id:
        return candidate, raw_value, candidate != raw_value
    return None, raw_value, True


def normalize_turn(payload: dict[str, Any], resident: dict[str, Any], turn_number: int, raw: str, previous_turns: list[dict[str, Any]]) -> dict[str, Any]:
    text = str(payload.get("spoken_text", "")).strip()
    if not text:
        raise ValueError("발언문이 비어 있습니다.")
    reply_to, reply_to_raw, reply_to_repaired = normalize_reply_to(payload.get("reply_to"), previous_turns, resident["speaker_id"])
    speech_act = str(payload.get("speech_act", "입장 표명")).strip()
    if speech_act not in SPEECH_ACTS:
        speech_act = "입장 표명"
    return {
        "utterance_id": f"U{turn_number:03d}",
        "speaker_id": resident["speaker_id"],
        "speaker_name": resident["display_name"],
        "cluster_id": resident["cluster_id"],
        "spoken_text": text,
        "reply_to": reply_to,
        "reply_to_raw": reply_to_raw,
        "reply_to_repaired": reply_to_repaired,
        "stance": payload.get("stance", "유보"),
        "tone": payload.get("tone", "차분함"),
        "tone_reason": str(payload.get("tone_reason", "발언의 즉시 맥락")),
        "interrupts": bool(payload.get("interrupts", False)),
        "speech_act": speech_act,
        "proposal": payload.get("proposal"),
        "claim_type": payload.get("claim_type", "가치판단"),
        "evidence_needed": bool(payload.get("evidence_needed", False)),
        "raw_response": raw,
    }


def replace_unverified_detail(payload: dict[str, Any], resident: dict[str, Any], previous_turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Use a resident-specific safe fallback when generation keeps violating constraints."""
    repaired = dict(payload)
    safe_statements = {
        "R01": [
            ("독립 검증 결과를 주민이 직접 확인할 수 있어야 합니다. 그 전에는 다른 조건을 논의해도 저는 동의하기 어렵습니다.", "절차 이의", "독립 검증 결과의 주민 확인"),
            ("안전 검토를 사업자 설명에만 맡길 수는 없습니다. 주민이 확인할 수 있는 검증 과정이 먼저 열려야 합니다.", "질문", "독립 검증 과정 공개"),
            ("검증이 끝나기 전에는 착수 여부를 정하지 말아야 한다고 봅니다. 확인할 기회를 주민에게도 보장해 주세요.", "반박", "검증 전 착수 보류"),
        ],
        "R02": [
            ("검증을 누가 맡고 무엇을 공개하는지부터 분명히 해야 합니다. 요약만 보여주는 방식으로는 믿기 어렵습니다.", "질문", "검증기관 선정과 공개 범위 명시"),
            ("결과를 봤다는 말만으로는 부족합니다. 검증기관을 정한 이유와 자료를 주민도 살필 수 있어야 합니다.", "절차 이의", "검증기관 선정 근거 공개"),
            ("독립성은 이름만 붙인다고 생기지 않습니다. 자료 공개 범위와 검토 절차를 같이 밝혀 주세요.", "반박", "원자료와 검토 절차 공개"),
        ],
        "R03": [
            ("공사 중 불편이 생겼을 때 어디에 말하고 누가 바로 대응하는지가 보이지 않습니다. 사후 설명보다 미리 작동할 보호 절차가 필요합니다.", "대안 제시", "공사 중 민원 대응 절차"),
            ("생활 불편은 문제가 생긴 뒤에 알아보겠다는 식으로는 안 됩니다. 주민이 바로 알리고 조치받을 창구를 먼저 정해야 합니다.", "절차 이의", "상시 민원 대응 창구"),
            ("공사 동안 일상이 어떻게 보호되는지가 제일 걱정됩니다. 불편이 확인되면 바로 대응한다는 약속이 필요합니다.", "질문", "공사 중 즉시 대응 원칙"),
        ],
        "R04": [
            ("설명회만 열어서는 주민 의견이 실제 결정에 반영되는지 알 수 없습니다. 주민이 확인 과정에 참여할 권한을 분명히 해 주세요.", "절차 이의", "주민 참여 권한 명시"),
            ("듣기만 하는 자리에 주민을 부르는 건 참여가 아닙니다. 확인하고 의견을 남길 권한이 실제로 있어야 합니다.", "반박", "공동 확인 권한 보장"),
            ("주민 대표가 어느 단계에서 의견을 낼 수 있는지 빠져 있습니다. 결정 전에 참여하는 절차를 넣어 주세요.", "대안 제시", "결정 전 주민 참여 절차"),
        ],
        "R05": [
            ("약속을 지키지 않았을 때 누가 책임지고 어떤 조치를 하는지가 빠져 있습니다. 그 부분이 없으면 약속 자체를 믿기 어렵습니다.", "반박", "이행 책임과 조치 명시"),
            ("좋은 말은 누구나 할 수 있습니다. 약속이 지켜지지 않을 때 책임질 주체부터 분명히 해야 합니다.", "절차 이의", "불이행 책임 주체 명시"),
            ("이행을 확인할 방법이 없으면 문구만 남습니다. 주민이 문제를 제기했을 때 어떤 조치가 따르는지 적어 주세요.", "질문", "이행 점검과 이의제기 절차"),
        ],
        "R06": [
            ("사업 필요성만 말하면 주민 불안은 남습니다. 필요한 사업이라면 주민이 계속 확인할 수 있는 통로도 같이 마련해야 합니다.", "조건부 양보", "주민 통제 장치 마련"),
            ("필요한 사업이라는 설명은 들었습니다. 그렇다면 주민이 진행을 확인할 장치까지 같이 제시해야 납득할 수 있습니다.", "조건부 양보", "상시 주민 확인 통로"),
            ("사업을 무조건 막자는 건 아닙니다. 다만 진행 과정이 주민 눈에 보이도록 만드는 조건은 필요합니다.", "대안 제시", "진행 과정 공개와 주민 점검"),
        ],
        "R07": [
            ("공사 기간에 생활이 흔들리는 가구를 어떻게 먼저 살필지가 중요합니다. 불편이 생긴 뒤에만 대응하는 방식으로는 부족합니다.", "대안 제시", "취약가구 사전 보호"),
            ("생활 여건이 다른 주민을 한 방식으로 다룰 수는 없습니다. 먼저 살펴야 할 가구를 어떻게 보호할지 정해 주세요.", "질문", "생활 취약가구 보호 기준"),
            ("공사 중 불편을 견디기 어려운 주민도 있습니다. 그런 상황을 미리 확인하고 돕는 절차가 필요합니다.", "절차 이의", "사전 생활영향 확인"),
        ],
        "R08": [
            ("피해를 나중에 입증하라는 방식은 주민에게 너무 불리합니다. 어떤 생활 손실을 어떻게 보전할지 원칙부터 밝혀야 합니다.", "반박", "선제적 생활 손실 보전 원칙"),
            ("문제가 생긴 뒤에 알아보자는 건 주민에게 부담을 떠넘기는 일입니다. 보전 방식의 기준을 먼저 합의해야 합니다.", "절차 이의", "사전 보전 기준 합의"),
            ("누구는 인정받고 누구는 놓치는 방식이면 공정하지 않습니다. 생활 손실을 살피는 원칙을 분명히 해 주세요.", "대안 제시", "형평적 생활 손실 보전"),
        ],
        "R09": [
            ("이번 결정이 지역의 앞으로의 생활에 무엇을 남기는지도 봐야 합니다. 지역에 돌아오는 변화가 일회성 약속으로 끝나면 안 됩니다.", "대안 제시", "장기 지역 환원 방안"),
            ("당장 불편만 볼 게 아니라 앞으로 이 지역이 어떻게 달라지는지도 따져야 합니다. 주민에게 남는 변화가 있어야 합니다.", "질문", "장기 지역 변화 공개"),
            ("지역에 도움이 된다는 말은 구체적인 책임으로 이어져야 합니다. 장기적으로 주민이 확인할 수 있는 환원 방안을 넣어 주세요.", "조건부 양보", "지속적 지역 환원 원칙"),
        ],
    }
    lead_ins = ("잠깐만요, ", "저는 그 부분과 별개로, ", "방금 설명을 들었지만, ")
    statements = safe_statements.get(resident["speaker_id"], [
        ("지금 나온 설명만으로는 주민이 무엇을 확인하고 요구할 수 있는지 분명하지 않습니다. 그 절차를 먼저 정리해 주세요.", "질문", "주민 확인 절차 명시"),
        ("설명만 듣는 방식으로는 주민 불안이 남습니다. 확인하고 의견을 낼 통로를 분명히 해 주세요.", "절차 이의", "주민 의견 제출 통로"),
        ("필요한 조건이 무엇인지 주민도 알 수 있어야 합니다. 확인 과정부터 공개해 주세요.", "질문", "확인 과정 공개"),
    ])
    selected = statements[0]
    selected_lead = lead_ins[len(previous_turns) % len(lead_ins)]
    for offset in range(len(statements)):
        candidate = statements[(len(previous_turns) + int(resident["cluster_id"]) + offset) % len(statements)]
        lead_in = lead_ins[(len(previous_turns) + offset) % len(lead_ins)]
        if not is_repetitive_speech(lead_in + candidate[0], previous_turns):
            selected = candidate
            selected_lead = lead_in
            break
    statement, speech_act, proposal = selected
    repaired["spoken_text"] = selected_lead + statement
    repaired["reply_to"] = previous_turns[-1].get("speaker_id") if previous_turns else None
    repaired["speech_act"] = speech_act
    repaired["claim_type"] = "가치판단"
    repaired["evidence_needed"] = speech_act in {"질문", "절차 이의"}
    repaired["proposal"] = proposal
    return repaired


def replace_check_response(payload: dict[str, Any], resident: dict[str, Any], previous_turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Never turn an invalid check response into an artificial acceptance."""
    repaired = replace_unverified_detail(payload, resident, previous_turns)
    repaired["decision"] = "revise"
    repaired["requested_change"] = str(repaired.get("proposal") or "핵심 조건의 원칙 문구 보완")
    return repaired


def needs_mediation(turns: list[dict[str, Any]]) -> bool:
    recent = turns[-4:]
    interruptions = sum(1 for turn in recent if turn.get("interrupts", False))
    heated = sum(1 for turn in recent if turn.get("tone") == "격앙됨")
    speakers = [str(turn.get("speaker_id", "")) for turn in recent]
    repetition = len(speakers) != len(set(speakers)) and len(recent) >= 3
    direct_replies = sum(bool(turn.get("reply_to")) for turn in recent)
    disconnected = len(recent) >= 4 and direct_replies <= 1
    return interruptions >= 2 or heated >= 2 or repetition or disconnected


def autonomy_event_trigger(experiment: dict[str, Any]) -> str | None:
    """Invoke the facilitator only for an observable conversational event.

    This deliberately has no turn-count cadence. A possible resident draft must
    arise from multiple residents' own proposals, while flow intervention is
    reserved for interruption, escalation, or a visibly disconnected exchange.
    """
    turns = experiment.get("turns", [])
    if len(turns) < max(4, len(experiment.get("panel", [])) // 2):
        return None
    recent = turns[-8:]
    proposal_turns = [
        turn for turn in recent
        if str(turn.get("proposal") or "").strip().lower() not in {"", "null", "none", "없음"}
        and str(turn.get("stance") or "") in {"찬성", "조건부", "유보"}
    ]
    if len({str(turn.get("speaker_id")) for turn in proposal_turns}) >= 2:
        last_draft_turn = max(
            (int(event.get("after_turn", 0)) for event in experiment.get("moderator_events", []) if event.get("event_type") == "resident_draft"),
            default=-6,
        )
        if len(turns) - last_draft_turn >= 6:
            return "resident_draft"
    return "flow_intervention" if needs_mediation(turns) else None


def latest_pulse_summary(experiment: dict[str, Any]) -> str:
    checks = experiment.get("interim_checks", [])
    if not checks:
        return "아직 수정안에 대한 전원 중간점검이 없습니다."
    responses = checks[-1]["responses"]
    counts = {decision: sum(item["decision"] == decision for item in responses) for decision in ("accept", "revise", "reject")}
    requested = [item["requested_change"] for item in responses if item["decision"] != "accept" and item["requested_change"]]
    return f"직전 수정안 점검: 수용 {counts['accept']}명, 수정요청 {counts['revise']}명, 거부 {counts['reject']}명. 남은 요구: {' / '.join(requested[:4]) or '없음'}"


def mood_summary(experiment: dict[str, Any]) -> str:
    moods = experiment.get("resident_moods", {})
    grouped = {state: [] for state in ("차분해짐", "답답함", "격앙됨", "신중함")}
    names = {person["speaker_id"]: person["display_name"] for person in experiment["panel"]}
    for speaker_id, mood in moods.items():
        grouped.setdefault(mood.get("state", "신중함"), []).append(names.get(speaker_id, speaker_id))
    return " / ".join(f"{state}: {', '.join(people)}" for state, people in grouped.items() if people)


def moderator_prompt(experiment: dict[str, Any], event_type: str) -> str:
    draft_rule = "주민 둘 이상이 실제로 낸 양립 가능한 조건만 3~6개로 묶을 수 있습니다. 공통분모가 없으면 revised_package는 빈 배열로 두세요." if event_type == "resident_draft" else "이번에는 문안이나 수정안을 만들지 마세요. revised_package와 draft_agreement는 빈 배열로 두세요."
    flow_rule = "intervene은 true로 하고, 한두 문장으로 말 겹침·독점·공격적 흐름만 멈추세요. 쟁점의 결론이나 다음 순서의 주제를 정하지 마세요." if event_type == "flow_intervention" else "intervene은 false로 두고, 주민 발언에서 이미 나온 조건을 확인하는 한두 문장만 말하세요."
    return f"""당신은 {TARGET_LIVING_AREA} {FACILITY_NAME} 주민회의의 촉진자입니다. 당신의 목적은 합의를 만들거나 매번 요약하는 것이 아니라, 주민들이 스스로 논의하도록 필요한 순간에만 흐름을 회복시키는 것입니다.

안건: {AGENDA}
현재 사업자안 또는 주민 문안: {', '.join(experiment['current_package'])}
최근 회의록:
{transcript_tail(experiment['turns'], 10)}
자동 감지된 개입 사유: {event_type}

{flow_rule}
{draft_rule}

주민 발언에 없는 조건·수치·사실을 새로 만들지 마세요. 확인되지 않은 주장은 사실로 요약하지 말고 evidence_notes에 '확인 필요'로만 기록하세요. 주민의 찬반을 설득하거나 다수 의견을 근거로 입장을 바꾸게 하지 마세요. 사회자 말은 실제 회의처럼 짧은 구어체로 쓰되, 할 말이 없으면 facilitator_summary는 빈 문자열로 두세요.
아래 JSON 하나만 출력하세요.
{{"intervene":true|false,"intervention_text":"짧은 흐름 정리 또는 빈 문자열","facilitator_summary":"필요한 경우만 쓰는 짧은 확인 멘트 또는 빈 문자열","revised_package":["주민 발언에서 나온 문안"],"ready_for_confirmation":false,"draft_agreement":[],"covered_issues":["실제로 언급된 쟁점"],"evidence_notes":["확인 필요 또는 이견"],"unresolved_issues":["남은 쟁점"]}}"""


def prior_pulse_response(experiment: dict[str, Any], speaker_id: str) -> str:
    """Give each resident continuity without exposing another resident's private background."""
    for check in reversed(experiment.get("interim_checks", [])):
        for response in check.get("responses", []):
            if response.get("speaker_id") == speaker_id:
                change = str(response.get("requested_change", "")).strip()
                decision = str(response.get("decision", "revise"))
                return f"직전 주민 문안 검토에서 당신은 '{decision}'이라고 답했고, 남긴 조건은 '{change or '없음'}'입니다."
    return "이전 주민 문안 검토 응답은 아직 없습니다."


def pulse_prompt(resident: dict[str, Any], revised_package: list[str], experiment: dict[str, Any]) -> str:
    clauses = "\n".join(f"- {item}" for item in revised_package)
    return f"""{profile_text(resident)}
{prior_pulse_response(experiment, resident['speaker_id'])}
주민들 발언에서 다음 검토 문안이 나왔습니다. 이는 정기 점검이나 최종 합의가 아니라, 실제로 나온 문안을 각자가 검토하는 자리입니다.
{clauses}

자신의 핵심 조건을 기준으로 이 문안을 수용할지, 문구를 고쳐야 할지, 받아들일 수 없는지 답하세요. 보상이나 지원이 자신의 안전·장기책임·정보공개 우려를 저절로 해결한다고 보지 마세요. 다만 확인되지 않은 사실을 독립 검증하고, 공개하고, 확인 전에는 착수하지 않는 절차가 분명하다면 그 조건 자체를 평가할 수 있습니다. 한 문장으로 가장 중요한 이유를 말하세요.
아래 JSON 하나만 출력하세요.
{{"decision":"accept|revise|reject","spoken_text":"문안 검토 발언 1~2문장","requested_change":"없음 또는 구체적 수정"}}"""


def evidence_clarification_prompt(resident: dict[str, Any], requested_change: str) -> str:
    return f"""{profile_text(resident)}
당신은 방금 수정안에 '{requested_change}'를 요구했습니다. 하지만 이 회의의 설문과 최초안에는 새 금액·거리·기간 같은 수치의 근거가 없습니다. 사회자가 이렇게 확인합니다: '그 수치를 정하는 건 오늘 여기서 근거가 부족합니다. 대신 착수 전 확인과 계약상 이행 보장을 분명히 적는 방향이면 받아들이실 수 있나요?'

사회자의 확인에 실제 회의처럼 짧게 답하세요. 수치 대신 원칙 문구로 받아들일 수 있으면 accept, 아직 필요한 질적 조건이 있으면 revise, 원칙 자체를 받아들일 수 없으면 reject를 고르세요.
아래 JSON 하나만 출력하세요.
{{"decision":"accept|revise|reject","spoken_text":"사회자 확인에 대한 답변 1~2문장","requested_change":"없음 또는 수치 없는 질적 수정"}}"""


def synthesis_prompt(experiment: dict[str, Any]) -> str:
    return f"""당신은 {TARGET_LIVING_AREA} 주민회의의 사회자입니다. 직전 논의에서 수정안이 제시되지 않아 토론이 반복되고 있습니다. 이제 현재안과 마지막 중간점검의 남은 요구만 묶어 반드시 하나의 통합 수정안을 제시하세요.

현재안:
{chr(10).join('- ' + item for item in experiment['current_package'])}
{latest_pulse_summary(experiment)}

주민이 이미 수용한 조항은 유지하고, 남은 수정 요구는 서로 모순되지 않게 원칙 문구로 통합하세요. 최초안과 확인된 자료에 없는 금액·거리·기간·반경·사고확률·방사선량·전기요금 기준을 만들지 말고, 착수 전 독립 검증·투명한 공개·주민 참여·기준 초과 시 조치처럼 질적으로만 쓰세요. 보상만으로 안전·장기책임·절차 문제를 해결한 것처럼 쓰지 마세요. 중복 조항 없이 3~6개여야 합니다.
아래 JSON 하나만 출력하세요.
{{"facilitator_summary":"지금까지 얘기 들어보면으로 시작하는 1~2문장 구어체 진행 멘트","revised_package":["통합 수정안 조항"]}}"""


def normalize_check_decision(decision: Any, requested_change: str, allowed: set[str], accepted: str, revise: str) -> str:
    """A stated change request takes precedence over an inconsistent acceptance label."""
    normalized = str(decision).strip().lower()
    if normalized not in allowed:
        normalized = revise
    if normalized == accepted and requested_change.strip() not in {"", "없음", "없습니다", "none", "n/a"}:
        return revise
    return normalized


def confirm_prompt(resident: dict[str, Any], draft: list[str], experiment: dict[str, Any]) -> str:
    clauses = "\n".join(f"- {item}" for item in draft)
    return f"""{profile_text(resident)}
사회자가 다음 잠정 합의문을 읽었습니다.
{clauses}

당신의 핵심 입장과 맞는지 마지막으로 확인하세요. 전부 받아들일 수 있으면 confirm, 문구나 내용의 보완이 필요하면 revise, 받아들일 수 없으면 block을 선택하세요. 실제 회의에서 확인 답변하듯 짧고 구어체로 말하세요.
아래 JSON 하나만 출력하세요.
{{"decision":"confirm|revise|block","spoken_text":"확인 발언 1~2문장","requested_change":"없음 또는 구체적 수정"}}"""


def pick_speaker(panel: list[dict[str, Any]], turns: list[dict[str, Any]], index: int, seed: int) -> dict[str, Any]:
    """Probabilistically select a resident without a fixed speaking rotation.

    The scheduler does not prescribe content or agreement. It only prevents a
    single agent from absorbing the meeting by giving less-heard residents a
    greater chance to speak. The deterministic seed keeps repeated runs auditable.
    """
    counts = {person["speaker_id"]: 0 for person in panel}
    last_seen = {person["speaker_id"]: -1 for person in panel}
    for turn_index, turn in enumerate(turns):
        speaker_id = str(turn.get("speaker_id", ""))
        if speaker_id in counts:
            counts[speaker_id] += 1
            last_seen[speaker_id] = turn_index
    latest_speaker = str(turns[-1].get("speaker_id", "")) if turns else ""
    pool = [person for person in panel if person["speaker_id"] != latest_speaker] or list(panel)
    weights: list[float] = []
    for person in pool:
        speaker_id = person["speaker_id"]
        gap = len(turns) - last_seen[speaker_id] if last_seen[speaker_id] >= 0 else len(turns) + 2
        participation = 1.0 / (1 + counts[speaker_id])
        waiting = min(2.2, 0.7 + gap / max(1, len(panel)))
        assertiveness = {"낮음": 0.85, "보통": 1.0, "높음": 1.15}.get(str(person.get("conversation_traits", {}).get("assertiveness", "보통")), 1.0)
        weights.append(max(0.05, participation * waiting * assertiveness))
    return random.Random(seed + index * 7919).choices(pool, weights=weights, k=1)[0]


def opening_statement() -> str:
    issue_labels = [str(item.get("label")) for item in DELIBERATION_GUIDE.get("issue_framework", []) if item.get("label")]
    issue_text = "·".join(issue_labels) if issue_labels else "입지·안전검증·생활 영향·보상·주민 참여"
    return (
        f"지금부터 {TARGET_LIVING_AREA} {FACILITY_NAME} 주민회의를 시작하겠습니다. "
        f"오늘 처음 검토할 사업자 제안은 {', '.join(INITIAL_PACKAGE)}입니다. "
        f"오늘은 {issue_text}을 두고 자유롭게 이야기하되, 확인된 사실과 아직 자료가 필요한 주장은 구분해 보겠습니다. "
        "주민들 말에서 실제로 함께 검토할 문안이 나오면 그때만 그 문안을 확인하겠습니다."
    )


def run_deliberation(
    panel_path: Path = PANEL_PATH,
    max_turns: int = 0,
    resume_log: Path | None = None,
    scenario_file: Path | None = None,
    live_path: Path | None = None,
    output_log: Path | None = None,
    model: str = DEFAULT_MODEL,
    stop_path: Path | None = None,
    max_stalled_checks: int = 0,
    min_turns_before_unresolved: int = DEFAULT_MIN_TURNS_BEFORE_UNRESOLVED,
    min_turns_before_qualified_consensus: int = DEFAULT_MIN_TURNS_BEFORE_QUALIFIED_CONSENSUS,
    seed: int = SEED,
    run_group: str = "단일 실행",
    run_index: int = 1,
) -> None:
    """Run one session. Safe to call from the desktop app's worker thread."""
    if max_turns < 0:
        raise ValueError("max_turns는 0 이상이어야 합니다.")
    if min_turns_before_qualified_consensus < 0:
        raise ValueError("조건부 합의 전 최소 발언 수는 0 이상이어야 합니다.")
    if max_stalled_checks < 0:
        raise ValueError("max_stalled_checks는 0 이상이어야 합니다.")
    if min_turns_before_unresolved < 0:
        raise ValueError("min_turns_before_unresolved는 0 이상이어야 합니다.")
    global MODEL, FACILITY_NAME, AGENDA, TARGET_LIVING_AREA, INITIAL_PACKAGE, DELIBERATION_GUIDE, LIVE_PATH, SPEAKER_FOCUSES, SPEAKER_LENSES
    MODEL = model
    if scenario_file:
        scenario = json.loads(scenario_file.read_text(encoding="utf-8"))
        TARGET_LIVING_AREA = str(scenario.get("target_living_area", TARGET_LIVING_AREA))
        FACILITY_NAME = str(scenario.get("facility_name", FACILITY_NAME))
        AGENDA = str(scenario.get("agenda", AGENDA))
        initial = scenario.get("initial_package", INITIAL_PACKAGE)
        if isinstance(initial, list) and initial:
            INITIAL_PACKAGE = [str(item) for item in initial]
        SPEAKER_FOCUSES = {str(key): str(value) for key, value in scenario.get("speaker_foci", SPEAKER_FOCUSES).items()}
        SPEAKER_LENSES = {str(key): str(value) for key, value in scenario.get("speaker_lenses", SPEAKER_LENSES).items()}
        guide = scenario.get("deliberation_guide")
        if isinstance(guide, dict) and guide:
            DELIBERATION_GUIDE = guide
    if live_path:
        LIVE_PATH = live_path.resolve()
    if resume_log:
        log_path = resume_log.resolve()
        experiment = json.loads(log_path.read_text(encoding="utf-8"))
        panel = experiment["panel"]
        experiment["status"] = "in_progress"
        experiment.pop("error", None)
        experiment.setdefault("resident_moods", {person["speaker_id"]: {"state": "신중함", "reason": "재개 전 상태 미기록"} for person in panel})
        start_index = len(experiment["turns"])
        update_live(min(95, 5 + 2 * (start_index // 3)), f"중단된 회의록 {start_index}턴부터 재개", experiment)
    else:
        payload = json.loads(panel_path.read_text(encoding="utf-8"))
        panel = payload["panel"]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = output_log.resolve() if output_log else PROJECT_ROOT / "logs" / f"natural_deliberation_{timestamp}.json"
        experiment: dict[str, Any] = {
            "metadata": {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "seed": seed,
                "run_group": run_group,
                "run_index": run_index,
                "prompt_version": PROMPT_VERSION,
                "fact_boundary_version": FACT_BOUNDARY_VERSION,
                "fact_boundary": "시나리오·설문·사회자 제공 자료에 없는 지역 사실, 수치, 법정 기준, 거리, 기간, 금액은 사실처럼 생성하지 않고 확인 질문 또는 검증 절차로 전환한다.",
                "persona_boundary": "NVIDIA 배경은 연령·성별·직업·주거·가구의 토론용 맥락에만 사용하며, 원본 서사나 특정 지역 사실을 주민 발언의 증거로 사용하지 않는다.",
                "model": MODEL,
                "scope": "설문 기반 합성 주민의 자율 상호작용을 탐색하는 프로토타입. 실제 주민 회의·정책 의결·합의 예측을 대체하지 않음.",
                "persona_attribution": payload["metadata"]["attribution"],
                "target_living_area": payload["metadata"].get("target_living_area", TARGET_LIVING_AREA),
                "background_selection": payload["metadata"].get("background_selection", "이전 패널 구성"),
                "trait_operationalization": payload["metadata"].get("trait_operationalization", {}),
                "facility_name": FACILITY_NAME,
                "deliberation_method": DELIBERATION_GUIDE.get("methodology_label"),
                "deliberation_source": DELIBERATION_GUIDE.get("source_title"),
                "deliberation_source_note": DELIBERATION_GUIDE.get("source_note"),
                "deliberation_mode": "minimum-procedural-autonomy",
                "moderator_policy": "사건 기반 개입: 흐름 훼손 또는 주민 다수의 조건안이 감지될 때만 개입",
                "max_turns": max_turns or DEFAULT_MAX_TURNS,
                "min_turns_before_unresolved": "미사용: 안전 발언 한도만 적용",
                "min_turns_before_qualified_consensus": "미사용: 조건부 합의는 분석 분류일 뿐 자동 종료하지 않음",
                "early_stall_closure": False,
            },
            "agenda": AGENDA,
            "opening_statement": opening_statement(),
            "current_package": list(INITIAL_PACKAGE),
            "panel": panel,
            "turns": [],
            "moderator_events": [],
            "interim_checks": [],
            "resident_moods": {person["speaker_id"]: {"state": "신중함", "reason": "처음 제시된 사업자 초안을 검토하는 중"} for person in panel},
            "confirmation": [],
            "deliberation_stages": DELIBERATION_GUIDE.get("deliberation_stages", []),
            "status": "in_progress",
        }
        start_index = 0
        update_live(2, "사회자가 회의 배경과 최초 제안을 설명", experiment)
    effective_max_turns = max_turns or DEFAULT_MAX_TURNS
    turn_indices = range(start_index, effective_max_turns)
    for index in turn_indices:
        if stop_path and stop_path.exists():
            experiment["status"] = "unresolved_closed"
            experiment["termination_reason"] = "manual_stop"
            break
        resident = pick_speaker(panel, experiment["turns"], index, seed)
        percent = min(95, 5 + int((index + 1) / max(1, effective_max_turns) * 90))
        update_live(percent, f"{stage_text(experiment)} · 발언 {index + 1}: {resident['display_name']} 이야기 중", experiment, current_speaker=resident["display_name"])
        try:
            answer, raw = ask(
                resident_prompt(resident, experiment, not experiment["turns"]),
                seed + index,
                required_keys=("spoken_text",),
                validator=lambda value: (
                    not contains_unsupported_detail(str(value.get("spoken_text", "")))
                    and not is_repetitive_speech(str(value.get("spoken_text", "")), experiment["turns"])
                ),
                validation_note="방금 발언에는 회의 자료에 없는 수치·법정 기준·지역 사실이 있거나 최근 주민 발언과 너무 비슷한 표현이 있습니다. 수치와 지역 사실을 삭제하고, 자신의 의제 초점에서 다른 문장 구조와 다른 요구로 다시 말하세요.",
            )
            if answer.pop("_validation_warning", ""):
                answer = replace_unverified_detail(answer, resident, experiment["turns"])
            turn = normalize_turn(answer, resident, index + 1, raw, experiment["turns"])
        except (requests.RequestException, ValueError) as error:
            experiment["status"] = "error"
            experiment["error"] = str(error)
            atomic_json(log_path, experiment)
            atomic_json(LIVE_PATH, {"status": "error", "percent": percent, "action": f"발언 생성 실패: {error}", "experiment": experiment})
            raise
        experiment["turns"].append(turn)
        atomic_json(log_path, experiment)
        update_live(percent, f"{stage_text(experiment)} · 발언 {index + 1}: {resident['display_name']} 발언 기록", experiment, current_speaker=resident["display_name"])

        event_type = autonomy_event_trigger(experiment)
        if not event_type:
            continue
        action = "사회자가 대화 흐름을 짧게 정리하는 중" if event_type == "flow_intervention" else "주민들이 낸 조건안을 함께 확인하는 중"
        update_live(min(94, percent + 2), action, experiment)
        try:
            moderator, raw = ask(moderator_prompt(experiment, event_type), seed + 1000 + index, max_tokens=1000, required_keys=("intervene", "intervention_text", "facilitator_summary", "revised_package", "ready_for_confirmation", "draft_agreement", "covered_issues", "evidence_notes", "unresolved_issues"))
        except (requests.RequestException, ValueError) as error:
            moderator, raw = fallback_moderator(experiment, event_type == "flow_intervention", error)
        revised_package = usable_revised_package(moderator.get("revised_package", [])) if event_type == "resident_draft" else []
        if revised_package:
            experiment["current_package"] = revised_package
        event = {
            "after_utterance": turn["utterance_id"], "after_turn": index + 1, "event_type": event_type,
            "deliberation_stage": stage_text(experiment),
            "intervene": bool(moderator.get("intervene")) if event_type == "flow_intervention" else False,
            "intervention_text": str(moderator.get("intervention_text", "")).strip() if event_type == "flow_intervention" else "",
            "facilitator_summary": str(moderator.get("facilitator_summary", "")).strip(),
            "revised_package": revised_package, "ready_for_confirmation": False, "draft_agreement": [],
            "unresolved_issues": moderator.get("unresolved_issues", []), "covered_issues": moderator.get("covered_issues", []),
            "evidence_notes": moderator.get("evidence_notes", []), "generation_warning": moderator.get("generation_warning"), "raw_response": raw,
        }
        if revised_package:
            update_live(min(95, percent + 3), "주민 발언에서 나온 문안을 각자 검토하는 중", experiment, revised_package=revised_package)
            pulse_responses = []
            for pulse_index, person in enumerate(panel):
                prior_check_speech = experiment["turns"] + pulse_responses
                answer, pulse_raw = ask(
                    pulse_prompt(person, revised_package, experiment), seed + 3000 + index * 20 + pulse_index,
                    max_tokens=420, required_keys=("decision", "spoken_text", "requested_change"),
                    validator=lambda value: not contains_unsupported_detail(str(value.get("spoken_text", ""))) and not is_repetitive_speech(str(value.get("spoken_text", "")), prior_check_speech),
                    validation_note="문안 검토 발언에 근거 없는 수치·지역 사실 또는 앞선 주민의 표현을 그대로 되풀이했습니다. 자신의 핵심 조건을 다른 문장으로 짧게 말하세요.",
                )
                if answer.pop("_validation_warning", ""):
                    answer = replace_check_response(answer, person, prior_check_speech)
                requested_change = str(answer.get("requested_change", "")).strip()
                pulse_responses.append({"speaker_id": person["speaker_id"], "speaker_name": person["display_name"], "decision": normalize_check_decision(answer.get("decision", "reject"), requested_change, {"accept", "revise", "reject"}, "accept", "revise"), "spoken_text": str(answer.get("spoken_text", "")).strip(), "requested_change": requested_change, "raw_response": pulse_raw})
            pulse = {"after_utterance": turn["utterance_id"], "kind": "resident_draft_review", "revised_package": revised_package, "responses": pulse_responses}
            experiment["interim_checks"].append(pulse)
            update_resident_moods(experiment, pulse_responses)
            event["pulse_check"] = pulse_responses
            if all(item["decision"] == "accept" for item in pulse_responses):
                event["ready_for_confirmation"] = True
                event["draft_agreement"] = revised_package
        experiment["moderator_events"].append(event)
        atomic_json(log_path, experiment)
        if not event["ready_for_confirmation"] or not event["draft_agreement"]:
            continue

        update_live(min(94, percent + 4), "사회자가 잠정 합의문을 읽고 전원 확인을 요청", experiment, agreement_draft=event["draft_agreement"])
        confirmations = []
        for confirm_index, person in enumerate(panel):
            prior_confirmation_speech = experiment["turns"] + confirmations
            answer, raw = ask(
                confirm_prompt(person, event["draft_agreement"], experiment),
                seed + 2000 + index * 20 + confirm_index,
                max_tokens=420,
                required_keys=("decision", "spoken_text", "requested_change"),
                validator=lambda value: (
                    not contains_unsupported_detail(str(value.get("spoken_text", "")))
                    and not is_repetitive_speech(str(value.get("spoken_text", "")), prior_confirmation_speech)
                ),
                validation_note="최종 확인 발언에 근거 없는 수치·지역 사실이 있거나 앞선 주민의 표현을 되풀이했습니다. 자신의 핵심 조건을 다른 문장으로 짧게 말하세요.",
            )
            if answer.pop("_validation_warning", ""):
                answer = replace_check_response(answer, person, prior_confirmation_speech)
            requested_change = str(answer.get("requested_change", "")).strip()
            confirmations.append({"speaker_id": person["speaker_id"], "speaker_name": person["display_name"], "decision": normalize_check_decision(answer.get("decision", "block"), requested_change, {"confirm", "revise", "block"}, "confirm", "revise"), "spoken_text": str(answer.get("spoken_text", "")).strip(), "requested_change": requested_change, "raw_response": raw})
        experiment["confirmation"].append({"after_utterance": turn["utterance_id"], "draft_agreement": event["draft_agreement"], "responses": confirmations})
        atomic_json(log_path, experiment)
        if all(item["decision"] == "confirm" for item in confirmations):
            experiment["status"] = "consensus_reached"
            experiment["final_agreement"] = event["draft_agreement"]
            break

    if experiment["status"] == "in_progress":
        experiment["status"] = "unresolved_closed"
        experiment["final_agreement"] = []
        experiment["termination_reason"] = "safety_turn_limit"
    if experiment["status"] == "consensus_reached":
        final_action = "전원 확인을 거쳐 합의가 성립되어 토론 종료"
    elif experiment.get("termination_reason") == "manual_stop":
        final_action = "사용자 중지 요청으로 현재 쟁점을 기록하고 토론 종료"
    else:
        final_action = "안전 발언 한도에 도달해 합의 여부와 남은 쟁점을 기록하고 토론 종료"
    experiment["completed_at"] = datetime.now().isoformat(timespec="seconds")
    experiment["elapsed_seconds"] = elapsed_seconds(experiment)
    experiment["analysis_outcome"] = derive_analysis_outcome(experiment)
    atomic_json(log_path, experiment)
    atomic_json(LIVE_PATH, {"status": "completed", "percent": 100, "action": final_action, "updated_at": datetime.now().isoformat(timespec="seconds"), "elapsed_seconds": experiment["elapsed_seconds"], "experiment": experiment, "log_path": str(log_path)})
    print(f"RESULT_LOG={log_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="자연형 주민 숙의 시뮬레이션")
    parser.add_argument("--panel", type=Path, default=PANEL_PATH)
    parser.add_argument("--max-turns", type=int, default=0, help="최대 발언 수. 0이면 전원 합의 확인까지 계속 진행")
    parser.add_argument("--resume-log", type=Path, help="중단된 자연형 토론 로그를 이어서 실행")
    parser.add_argument("--scenario-file", type=Path, help="시설·지역·초기안이 담긴 앱 세션 시나리오 JSON")
    parser.add_argument("--live-path", type=Path, help="실시간 상태 JSON 저장 위치")
    parser.add_argument("--output-log", type=Path, help="토론 로그 JSON 저장 위치")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama 모델 태그")
    parser.add_argument("--stop-path", type=Path, help="수동 중지 요청 파일 위치")
    parser.add_argument("--max-stalled-checks", type=int, default=0, help="수용 인원 개선이 없으면 조기 종료할 중간점검 횟수. 0이면 조기 종료하지 않음")
    parser.add_argument("--min-turns-before-unresolved", type=int, default=DEFAULT_MIN_TURNS_BEFORE_UNRESOLVED, help="합의 미성립으로 조기 종료하기 전 최소 발언 수")
    parser.add_argument("--min-turns-before-qualified-consensus", type=int, default=DEFAULT_MIN_TURNS_BEFORE_QUALIFIED_CONSENSUS, help="조건부 합의로 종료하기 전 최소 발언 수")
    parser.add_argument("--seed", type=int, default=SEED, help="반복 실행을 식별하기 위한 난수 시드")
    parser.add_argument("--run-group", default="단일 실행", help="동일 설계 반복 실행 묶음 식별자")
    parser.add_argument("--run-index", type=int, default=1, help="반복 실행 회차")
    args = parser.parse_args()
    run_deliberation(args.panel, args.max_turns, args.resume_log, args.scenario_file, args.live_path, args.output_log, args.model, args.stop_path, args.max_stalled_checks, args.min_turns_before_unresolved, args.min_turns_before_qualified_consensus, args.seed, args.run_group, args.run_index)


if __name__ == "__main__":
    main()
