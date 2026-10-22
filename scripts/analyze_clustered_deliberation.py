"""Create a human-readable research memo from a clustered deliberation log."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def latest_log() -> Path:
    logs = sorted((PROJECT_ROOT / "logs").glob("clustered_compensation_deliberation_*.json"))
    if not logs:
        raise FileNotFoundError("군집 숙의 로그가 없습니다. 먼저 run_clustered_compensation_deliberation.py를 실행하세요.")
    return logs[-1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, default=None)
    args = parser.parse_args()
    source = args.log or latest_log()
    data = json.loads(source.read_text(encoding="utf-8"))
    output = PROJECT_ROOT / "reports" / f"{source.stem}_analysis.md"
    final = data["final_result"]
    lines = [
        "# 군집 기반 변전소 보상안 숙의 시뮬레이션 결과",
        "",
        "## 연구상 지위와 한계",
        "이 결과는 실제 주민 투표나 실제 금전 최소보상액이 아니라, 102명 원자료에서 생성된 1,000명 가상 응답을 사용한 탐색적 LLM 숙의 시뮬레이션이다. 따라서 정책 가설 생성과 프로토콜 검증에 활용하고, 모집단 추론·실제 합의·금액 산정의 근거로 직접 사용하지 않는다.",
        "",
        "## 의사결정 규칙",
        f"군집별 1개 대표 페르소나의 발언을 해당 군집 비중으로 가중했다. 유효 찬성 가중치가 {data['metadata']['pass_threshold']:.3f} 이상이고 반대 가중치가 1/3 미만일 때 가결로 판정했다. 조건부 찬성은 유효 찬성으로 계산하지 않았으며, 현재 안에 없는 보완 요소를 명시해 다음 라운드의 개정 근거로만 사용했다.",
        "",
        "## 군집 패널",
    ]
    for persona in data["personas"]:
        lines.append(f"- {persona['persona_name']}: {persona['member_count']}명, {persona['member_share']:.1%}, 대표 응답자 {persona['representative_agent_id']}")
    lines.extend(["", "## 초기 보상안"])
    for item in data["initial_proposal"]["components"]:
        lines.append(f"- {item['title']}: {item['description']}")
    lines.extend(["", "## 라운드별 토론과 보상안 변화"])
    for record in data["rounds"]:
        vote = record["weighted_vote"]
        lines.extend([f"### 라운드 {record['round']}", "현재 보상안:"])
        for item in record["proposal"]["components"]:
            lines.append(f"- {item['title']}")
        lines.append(f"가중 투표: 찬성 {vote['approve_weight']:.1%}, 조건부 찬성 {vote['conditional_weight']:.1%}, 반대 {vote['oppose_weight']:.1%}, 유효 찬성 {vote['effective_support_weight']:.1%}, 가결판정 {vote['passed']}")
        lines.append(f"진행자 요약: {record['moderator_summary']}")
        for message in record["messages"]:
            fallback = " [규칙 기반 보조판단]" if message["fallback_used"] else ""
            lines.append(f"- {message['persona_name']} ({message['member_share']:.1%}, {message['stance']}){fallback}: {message['statement']}")
        lines.append("")
    lines.extend(["## 보상안 변경 이력"])
    if data["proposal_changes"]:
        for change in data["proposal_changes"]:
            lines.append(f"- 라운드 {change['after_round']} 후 `{change['added_component']}` 추가: {change['reason']}")
    else:
        lines.append("- 초기안 이후 변경 없음")
    vote = final["weighted_vote"]
    lines.extend([
        "",
        "## 최종 결론",
        f"최종 판정: **{final['decision']}**. 유효 찬성 가중치 {vote['effective_support_weight']:.1%}, 반대 가중치 {vote['oppose_weight']:.1%}.",
        "최종 보상안 구성요소:",
    ])
    for item in final["proposal"]["components"]:
        lines.append(f"- {item['title']}: {item['description']}")
    lines.extend([
        "",
        "## 해석 유의사항",
        "- ‘최소’는 이 실험의 이산적인 보상 구성요소 조합 가운데 최초로 규칙을 통과한 안을 뜻한다. 원 단위 금액의 최소보상액을 뜻하지 않는다.",
        "- LLM 발언은 사전 정의한 군집 프로필에 따른 생성물이며, 실제 면담자료를 대체하지 않는다.",
        "- 군집 타당도·신뢰도는 `reports/cluster_validation.json` 및 `reports/cluster_selection_metrics.csv`와 함께 제시해야 한다.",
    ])
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"REPORT={output}")


if __name__ == "__main__":
    main()
