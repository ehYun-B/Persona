"""Transparent, log-derived indicators for resident deliberation sessions."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import re
from typing import Any


ISSUE_RULES = {
    "사고·방사선 안전": ("사고", "방사", "비상대응", "안전", "위험", "대피"),
    "사용후핵연료·폐로": ("사용후핵연료", "폐로", "폐기물", "처분", "저장", "미래 세대", "장기 책임"),
    "전력수급·대체전원": ("전력수급", "전력 공급", "대체 전원", "재생에너지", "수요관리", "기저"),
    "총비용·전기요금": ("총비용", "발전비", "전기요금", "건설비", "운영비", "비용"),
    "산업·일자리": ("산업", "일자리", "고용", "수출", "지역경제", "세수"),
    "환경·기후": ("환경", "기후", "탄소", "온배수", "오염", "배출"),
    "절차·주민 참여": ("주민", "참여", "협의", "공개", "열람", "질의", "감시", "대표", "절차", "팩트체크"),
    "생활피해·공사 관리": ("소음", "진동", "공사", "차량", "교통", "생활", "운영", "작업", "피해"),
    "보상·지역 환원": ("보상", "감면", "기금", "지원", "손실", "전기요금", "환원", "재산"),
}
FACILITY_TERMS = ("변전소", "화력발전소", "원자력", "원전", "데이터센터", "소각", "폐기물", "매립", "송전선", "풍력", "태양광", "하수처리")
PROVINCE_TERMS = ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주", "경상", "전라", "충청")


def _issue_counts(texts: list[str], configured_issues: list[str] | None = None) -> list[dict[str, Any]]:
    rules = ISSUE_RULES
    if configured_issues:
        rules = {label: tuple({label, *(token for token in re.split(r"[·ㆍ/\s()]+", label) if len(token) >= 2)}) for label in configured_issues}
    counts = Counter()
    for text in texts:
        for label, keywords in rules.items():
            if any(keyword in text for keyword in keywords):
                counts[label] += 1
    return [{"label": label, "count": counts[label]} for label in rules]


def _outcome(discussion: dict[str, Any]) -> dict[str, Any]:
    return discussion.get("analysis_outcome", {"classification": "unresolved", "label": "합의 미성립", "acceptance_count": 0, "panel_size": len(discussion.get("panel", [])), "package": [], "remaining_requests": [], "rule": "결과 분류 정보가 없습니다."})


def _best_check_index(outcome: dict[str, Any]) -> int | None:
    value = outcome.get("source_check")
    return int(value) - 1 if isinstance(value, int) and value > 0 else None


def _elapsed_seconds(discussion: dict[str, Any]) -> int | None:
    saved = discussion.get("elapsed_seconds")
    if isinstance(saved, (int, float)):
        return max(0, int(saved))
    metadata = discussion.get("metadata", {})
    try:
        return max(0, int((datetime.fromisoformat(metadata["completed_at"]) - datetime.fromisoformat(metadata["created_at"])).total_seconds()))
    except (KeyError, TypeError, ValueError):
        try:
            return max(0, int((datetime.fromisoformat(discussion["completed_at"]) - datetime.fromisoformat(metadata["created_at"])).total_seconds()))
        except (KeyError, TypeError, ValueError):
            return None


def _final_confirmation(discussion: dict[str, Any]) -> dict[str, Any]:
    """Expose the separate final-confirmation phase; it is not an interim pulse."""
    records = discussion.get("confirmation", [])
    latest = records[-1] if records else {}
    responses = latest.get("responses", [])
    counts = Counter(item.get("decision", "unknown") for item in responses)
    status = str(discussion.get("status", ""))
    if status == "consensus_reached" and responses and counts["confirm"] == len(responses):
        label = "전원 최종 확인 완료: 만장일치 가결"
        state = "confirmed"
    elif responses:
        label = f"잠정 합의문 최종 확인: 확인 {counts['confirm']} · 수정 {counts['revise']} · 보류 {counts['block']}"
        state = "not_confirmed"
    elif status == "qualified_consensus":
        label = "이전 버전 기록: 사전 규칙의 조건부 합의"
        state = "not_run"
    else:
        label = "잠정 합의문 전원 최종 확인이 실시되지 않았습니다."
        state = "not_run"
    return {"state": state, "label": label, "draft_agreement": latest.get("draft_agreement", []), "responses": responses, "confirm": counts["confirm"], "revise": counts["revise"], "block": counts["block"], "attempts": len(records)}


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[가-힣A-Za-z]{2,}", text) if token not in {"그리고", "하지만", "그래서", "그런데", "저는", "이건", "정말", "이렇게", "그냥", "주민"}}


def _transcript_audit(turns: list[dict[str, Any]], moderator_events: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """Flag reproducible transcript risks; never treat a semantic heuristic as fact."""
    flags: list[dict[str, str]] = []
    normalized: dict[str, list[str]] = {}
    for turn in turns:
        key = re.sub(r"[^가-힣A-Za-z0-9]", "", str(turn.get("spoken_text", ""))).lower()
        if len(key) >= 24:
            normalized.setdefault(key, []).append(str(turn.get("utterance_id", "-")))
    exact = [ids for ids in normalized.values() if len(ids) >= 2]
    for ids in exact:
        flags.append({"type": "정확 반복", "location": ", ".join(ids), "detail": "동일한 문장 정규화 문자열이 2회 이상 나타났습니다."})
    near_pairs: list[tuple[str, str, float]] = []
    for right_index, right in enumerate(turns):
        right_tokens = _tokens(str(right.get("spoken_text", "")))
        if len(right_tokens) < 4:
            continue
        best_pair: tuple[str, str, float] | None = None
        for left in turns[max(0, right_index - 12):right_index]:
            left_tokens = _tokens(str(left.get("spoken_text", "")))
            if len(left_tokens) < 4:
                continue
            score = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
            candidate = (str(left.get("utterance_id", "-")), str(right.get("utterance_id", "-")), score)
            if score >= 0.75 and (best_pair is None or score > best_pair[2]):
                best_pair = candidate
        if best_pair is not None:
            near_pairs.append(best_pair)
    for left, right, score in near_pairs[:20]:
        flags.append({"type": "높은 어휘 반복 후보", "location": f"{left} ↔ {right}", "detail": f"12개 발언 이내의 핵심 어휘 Jaccard 유사도 {score:.2f}. 문장 의미가 같은지는 원문 검토가 필요합니다."})
    reply_links = invalid_replies = adjacent_replies = 0
    prior_speakers: set[str] = set()
    for index, turn in enumerate(turns):
        reply_to = str(turn.get("reply_to") or "").strip()
        if not reply_to or reply_to.lower() == "null":
            prior_speakers.add(str(turn.get("speaker_id", "")))
            continue
        reply_links += 1
        if index and reply_to == str(turns[index - 1].get("speaker_id", "")):
            adjacent_replies += 1
        if reply_to not in prior_speakers or reply_to == str(turn.get("speaker_id", "")):
            invalid_replies += 1
            flags.append({"type": "대화 연결 오류", "location": str(turn.get("utterance_id", "-")), "detail": f"reply_to={reply_to}가 이전의 다른 패널 화자를 가리키지 않습니다."})
        prior_speakers.add(str(turn.get("speaker_id", "")))
    facility_name = str(config.get("facility_name", ""))
    region = " ".join((str(config.get("province", "")), str(config.get("district", "")), str(config.get("region_display", ""))))
    known_facility = {term for term in FACILITY_TERMS if term in facility_name}
    known_province = {term for term in PROVINCE_TERMS if term in region}
    mismatch_count = 0
    for turn in turns:
        text = str(turn.get("spoken_text", ""))
        foreign_facility = [term for term in FACILITY_TERMS if term in text and term not in known_facility]
        foreign_province = [term for term in PROVINCE_TERMS if term in text and term not in known_province]
        if foreign_facility or foreign_province:
            mismatch_count += 1
            parts = ([] if not foreign_facility else ["다른 시설명 후보: " + ", ".join(foreign_facility)]) + ([] if not foreign_province else ["대상 지역과 다른 광역권 표현 후보: " + ", ".join(foreign_province)])
            flags.append({"type": "시나리오 맥락 불일치 후보", "location": str(turn.get("utterance_id", "-")), "detail": "; ".join(parts) + ". 단어 기반 탐지이므로 원문 판단이 필요합니다."})
    warnings = [event for event in moderator_events if event.get("generation_warning")]
    for event in warnings:
        flags.append({"type": "사회자 생성 경고", "location": str(event.get("after_utterance", "사회자")), "detail": str(event.get("generation_warning"))[:240]})
    unmatched_turns = max(0, len(turns) - 1 - adjacent_replies)
    if len(turns) >= 8 and adjacent_replies < (len(turns) - 1) * 0.45:
        flags.append({"type": "대화 연결 약화 후보", "location": "전체", "detail": f"직전 발언 직접 응답이 {adjacent_replies}/{max(1, len(turns) - 1)}건입니다. 사회자 질문과 발언 순서를 원문에서 검토하세요."})
    return {"summary": {"total_turns": len(turns), "exact_repeat_count": len(exact), "near_repeat_count": len(near_pairs), "reply_link_count": reply_links, "adjacent_reply_count": adjacent_replies, "unmatched_turn_count": unmatched_turns, "invalid_reply_count": invalid_replies, "scenario_mismatch_count": mismatch_count, "generation_warning_count": len(warnings)}, "flags": flags}


def build_result_report(discussion: dict[str, Any], analysis: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Summarize saved artifacts and expose explicit limitations."""
    outcome = _outcome(discussion)
    panel, turns, checks = discussion.get("panel", []), discussion.get("turns", []), discussion.get("interim_checks", [])
    best_index = _best_check_index(outcome)
    best_check = checks[best_index] if best_index is not None and best_index < len(checks) else None
    trajectory = []
    for index, check in enumerate(checks, start=1):
        counts = Counter(item.get("decision", "unknown") for item in check.get("responses", []))
        trajectory.append({"check": index, "accept": counts["accept"], "revise": counts["revise"], "reject": counts["reject"], "panel_size": len(check.get("responses", [])) or len(panel), "is_best": index - 1 == best_index, "package": check.get("revised_package", [])})
    turns_by_speaker = Counter(turn.get("speaker_id") for turn in turns)
    interruptions = Counter(turn.get("speaker_id") for turn in turns if turn.get("interrupts"))
    tone_counts = Counter(turn.get("tone", "기록 없음") for turn in turns)
    claim_type_counts = Counter(turn.get("claim_type", "미분류") for turn in turns)
    speech_act_counts = Counter(turn.get("speech_act", "미분류") for turn in turns)
    evidence_request_turns = sum(1 for turn in turns if turn.get("evidence_needed"))
    best_responses = {item.get("speaker_id"): item for item in (best_check or {}).get("responses", [])}
    clusters: dict[int, dict[str, Any]] = {}
    for resident in panel:
        cluster_id = int(resident.get("cluster_id", 0))
        entry = clusters.setdefault(cluster_id, {"cluster_id": cluster_id, "panel_members": 0, "turns": 0, "accept": 0, "revise": 0, "reject": 0})
        entry["panel_members"] += 1
        entry["turns"] += turns_by_speaker[resident.get("speaker_id")]
        response = best_responses.get(resident.get("speaker_id"), {})
        if response: entry[str(response.get("decision", "revise"))] += 1
    persona_names = {int(item.get("cluster_id", 0)): item.get("persona_name", "군집") for item in analysis.get("personas", [])}
    cluster_rows = [{**item, "name": persona_names.get(cluster_id, f"군집 {cluster_id}"), "acceptance_rate": round(item["accept"] / item["panel_members"], 3) if item["panel_members"] else None} for cluster_id, item in sorted(clusters.items())]
    package_history = [{"stage": "최초 사업자안", "check": None, "package": config.get("initial_offer", []), "accept": None, "revise": None, "reject": None}]
    package_history.extend({"stage": f"주민 문안 {item['check']}", "check": item["check"], "package": item["package"], "accept": item["accept"], "revise": item["revise"], "reject": item["reject"]} for item in trajectory)
    texts = [str(turn.get("spoken_text", "")) for turn in turns] + [str(clause) for stage in package_history for clause in stage["package"]]
    validation, input_info, guide = analysis.get("validation", {}), analysis.get("input", {}), config.get("deliberation_guide", {})
    expected_issues = [str(item.get("label")) for item in guide.get("issue_framework", []) if item.get("label")]
    covered = {str(label) for event in discussion.get("moderator_events", []) for label in event.get("covered_issues", []) if label}
    covered_count = sum(1 for label in expected_issues if any(label in item or item in label for item in covered))
    evidence_notes = [str(note) for event in discussion.get("moderator_events", []) for note in event.get("evidence_notes", []) if note]
    audit = _transcript_audit(turns, discussion.get("moderator_events", []), config)
    confirmation = _final_confirmation(discussion)
    background_linked = sum(str(resident.get("background", {}).get("source", "")).startswith("nvidia/") for resident in panel)
    quality = [
        {"label": "설문 기반", "value": f"유효 응답 {input_info.get('valid_respondents', '미기록')}명", "state": "ok" if input_info.get("valid_respondents") else "warn"},
        {"label": "군집 검증", "value": f"k={validation.get('selected_k', '미기록')}, 실루엣={validation.get('selected_metrics', {}).get('silhouette', '미기록')}", "state": "ok" if validation.get("selected_k") else "warn"},
        {"label": "패널 구성", "value": f"{len(panel)}명, 군집 {len(clusters)}개 포함, NVIDIA 배경 {background_linked}명 연결", "state": "ok" if panel else "warn"},
        {"label": "결과 분류", "value": outcome.get("rule", "미기록"), "state": "ok" if outcome.get("classification") in {"unanimous_consensus", "conditional_alignment", "proposal_with_residual_disagreement"} else "warn"},
        {"label": "최종 확인", "value": confirmation["label"], "state": "ok" if confirmation["state"] == "confirmed" else "warn"},
        {"label": "쟁점 기록", "value": f"{covered_count}/{len(expected_issues)}개 시설 쟁점이 사건 기반 사회자 기록에 포함" if expected_issues else "시설별 쟁점 틀 미적용", "state": "ok" if not expected_issues or covered_count else "warn"},
        {"label": "근거 확인", "value": f"주민 확인요청 발언 {evidence_request_turns}건, 사회자 확인·이견 기록 {len(evidence_notes)}건", "state": "ok" if evidence_request_turns or evidence_notes else "warn"},
        {"label": "대화 연결", "value": f"직전 발언 직접 응답 {audit['summary']['adjacent_reply_count']}건 / 비연결 후보 {audit['summary']['unmatched_turn_count']}건 / reply_to 기록 {audit['summary']['reply_link_count']}건", "state": "ok" if audit['summary']['adjacent_reply_count'] >= max(1, len(turns) // 2) else "warn"},
        {"label": "회의록 자동 점검", "value": f"정확 반복 {audit['summary']['exact_repeat_count']}건, 높은 유사 반복 후보 {audit['summary']['near_repeat_count']}건, 맥락 불일치 후보 {audit['summary']['scenario_mismatch_count']}건", "state": "warn" if audit["flags"] else "ok"},
        {"label": "해석 범위", "value": "전국 표본 기반 지역 맥락 시뮬레이션이며 실제 정책 의결·금전 보상액 예측이 아님", "state": "warn"},
    ]
    metadata = discussion.get("metadata", {})
    reproducibility = {"facility": config.get("facility_name", "미기록"), "region": config.get("region_display") or f"{config.get('province', '')} {config.get('district', '')}".strip(), "model": metadata.get("model", config.get("model", "qwen3:30b")), "seed": metadata.get("seed", "미기록"), "run_group": metadata.get("run_group", "단일 실행"), "run_index": metadata.get("run_index", 1), "prompt_version": metadata.get("prompt_version", "이전 버전"), "fact_boundary_version": metadata.get("fact_boundary_version", "이전 버전"), "created_at": metadata.get("created_at", config.get("created_at", "미기록")), "survey_source": input_info.get("source_file", input_info.get("source", "미기록")), "selected_k": validation.get("selected_k", "미기록"), "silhouette": validation.get("selected_metrics", {}).get("silhouette", "미기록"), "ward_ari": validation.get("ward_kmeans_adjusted_rand_index", "미기록"), "bootstrap_ari": validation.get("bootstrap_mean_adjusted_rand_index", "미기록"), "termination_reason": discussion.get("termination_reason", "전원 확인 또는 실행 중"), "max_turns": metadata.get("max_turns", "미기록"), "min_turns_before_unresolved": metadata.get("min_turns_before_unresolved", "미기록"), "early_stall_closure": metadata.get("early_stall_closure", "미기록"), "min_turns_before_qualified_consensus": metadata.get("min_turns_before_qualified_consensus", "미기록"), "deliberation_mode": metadata.get("deliberation_mode", "이전 규칙형 기록"), "moderator_policy": metadata.get("moderator_policy", "이전 규칙형 기록"), "elapsed_seconds": _elapsed_seconds(discussion), "deliberation_method": metadata.get("deliberation_method", guide.get("methodology_label", "미기록")), "deliberation_source": metadata.get("deliberation_source", guide.get("source_title", "미기록")), "deliberation_source_note": metadata.get("deliberation_source_note", guide.get("source_note", "미기록")), "fact_boundary": metadata.get("fact_boundary", "이전 기록"), "persona_boundary": metadata.get("persona_boundary", "이전 기록")}
    panel_allocation = []
    for persona in analysis.get("personas", []):
        cluster_id = int(persona.get("cluster_id", 0))
        panel_allocation.append({"cluster_id": cluster_id, "name": persona.get("persona_name", f"군집 {cluster_id}"), "survey_members": persona.get("member_count"), "survey_share": persona.get("member_share"), "panel_members": sum(1 for resident in panel if int(resident.get("cluster_id", 0)) == cluster_id)})
    speaker_turns = [turns_by_speaker.get(item.get("speaker_id"), 0) for item in panel]
    return {"outcome": outcome, "final_confirmation": confirmation, "trajectory": trajectory, "clusters": cluster_rows, "issues": _issue_counts(texts, config.get("concerns", [])), "package_history": package_history, "panel_allocation": panel_allocation, "analysis_snapshot": {"input": input_info, "validation": validation}, "process": {"total_turns": len(turns), "moderator_events": len(discussion.get("moderator_events", [])), "interventions": sum(1 for item in discussion.get("moderator_events", []) if item.get("intervene")), "interim_checks": len(checks), "tone_counts": dict(tone_counts), "interruptions": sum(interruptions.values()), "turn_spread": max(speaker_turns, default=0) - min(speaker_turns, default=0), "claim_type_counts": dict(claim_type_counts), "speech_act_counts": dict(speech_act_counts), "adjacent_reply_count": audit["summary"]["adjacent_reply_count"], "reply_link_count": audit["summary"]["reply_link_count"], "unmatched_turn_count": audit["summary"]["unmatched_turn_count"], "evidence_request_turns": evidence_request_turns, "evidence_notes": evidence_notes, "stage_counts": dict(Counter(event.get("deliberation_stage", "단계 미기록") for event in discussion.get("moderator_events", []))), "elapsed_seconds": _elapsed_seconds(discussion), "transcript_audit": audit}, "quality": quality, "reproducibility": reproducibility}
