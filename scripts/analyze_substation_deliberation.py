"""Create a compact, auditable analysis memo from a substation deliberation log."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = PROJECT_ROOT / "logs"
REPORTS_DIR = PROJECT_ROOT / "reports"

TOPIC_KEYWORDS = {
    "안전·영향 검증": ["안전", "건강", "환경", "영향", "전자파", "화재", "검증"],
    "주민 참여·절차": ["주민", "참여", "절차", "협의", "투명", "의사결정"],
    "후보지·대안 비교": ["후보지", "대안", "선정 기준", "노선"],
    "보상·지역 편익": ["보상", "손실", "편익", "기금", "지원", "재산"],
    "정보 공개·감시": ["공개", "자료", "원자료", "모니터링", "감시"],
}
STANCE_ORDER = ["수용", "조건부 수용", "유보", "반대", "미분류"]


def progress(percent: int, message: str) -> None:
    print(f"[{percent:>3}%] {message}", flush=True)


def latest_log() -> Path:
    candidates = sorted(LOGS_DIR.glob("substation_deliberation_*.json"), key=lambda path: path.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError("변전소 토론 로그를 찾지 못했습니다.")
    return candidates[-1]


def stance_for(text: str) -> str:
    match = re.search(r"현재\s*입장[^\n]{0,80}(조건부\s*수용|수용|유보|반대)", text)
    if not match:
        return "미분류"
    return re.sub(r"\s+", " ", match.group(1))


def topic_counts(statements: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for topic, keywords in TOPIC_KEYWORDS.items():
        counts[topic] = sum(
            any(keyword in str(statement["text"]) for keyword in keywords)
            for statement in statements
        )
    return counts


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    header_line = "| " + " | ".join(headers) + " |"
    divider_line = "| " + " | ".join("---" for _ in headers) + " |"
    body_lines = ["| " + " | ".join(str(value) for value in row) + " |" for row in rows]
    return "\n".join([header_line, divider_line, *body_lines])


def main() -> None:
    parser = argparse.ArgumentParser(description="변전소 토론 로그를 분석 메모로 정리합니다.")
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    log_path = args.log or latest_log()
    output_path = args.output or REPORTS_DIR / f"{log_path.stem}_analysis.md"
    progress(10, "변전소 토론 로그 읽는 중")
    data = json.loads(log_path.read_text(encoding="utf-8"))
    rounds = data["rounds"]
    panel_size = int(data["panel_size"])

    progress(35, "시나리오별 입장과 조건 집계 중")
    scenario_rows: list[list[object]] = []
    topic_totals: Counter[str] = Counter()
    round_sections: list[str] = []
    total_statements = 0
    for round_data in rounds:
        scenario = round_data["scenario"]
        statements = round_data["statements"]
        stance_counts = Counter(stance_for(str(statement["text"])) for statement in statements)
        counts = topic_counts(statements)
        topic_totals.update(counts)
        total_statements += len(statements)
        scenario_rows.append(
            [
                scenario["title"],
                stance_counts["수용"],
                stance_counts["조건부 수용"],
                stance_counts["유보"],
                stance_counts["반대"],
                stance_counts["미분류"],
            ]
        )
        topic_rows = [[topic, f"{count}/{len(statements)}명"] for topic, count in counts.items()]
        round_sections.append(
            "\n".join(
                [
                    f"## {scenario['title']}",
                    "",
                    markdown_table(["주요 조건", "언급 에이전트"], topic_rows),
                    "",
                    "### 진행자 요약",
                    str(round_data["moderator_summary"]).strip(),
                ]
            )
        )

    progress(70, "분석 초안 작성 중")
    ranked_topics = topic_totals.most_common()
    ranked_text = ", ".join(f"{topic} ({count}/{total_statements})" for topic, count in ranked_topics)
    recommendation_rows = [
        ["입지 공개 전", "후보지 선정 기준과 대안 후보지를 같은 형식으로 공개하고, 주민이 비교 질문을 제출할 수 있게 한다."],
        ["안전·영향 검증", "독립 검토, 측정·검토 범위, 원자료 공개 범위, 재검증 요청 절차를 사전에 문서화한다."],
        ["참여 거버넌스", "주민 대표 협의체와 상시 모니터링의 권한·회의 주기·응답 기한을 명시한다."],
        ["부담·편익", "생활 피해·재산 영향 대응 기준과 지역 편익의 배분 원칙을 분리해 협의한다."],
    ]
    report = "\n".join(
        [
            "# 변전소 가상 주민 토론 결과 및 분석 초안",
            "",
            "## 범위와 해석 원칙",
            "",
            f"- 입력: 익명 가상 설문 기반 주민 에이전트 1,000명 중 성향군 비율을 반영해 추출한 {panel_size}명 패널",
            f"- 실행: 변전소 시나리오 {len(rounds)}개, 에이전트 발언 {total_statements}건, 진행자 요약 {len(rounds)}건",
            "- 주의: 아래 내용은 실제 주민 여론이나 사실 판단이 아니라, 가상 에이전트 조건에서 나타난 토론 신호다.",
            "",
            "## 시나리오별 입장 분포",
            "",
            markdown_table(
                ["시나리오", "수용", "조건부 수용", "유보", "반대", "미분류"],
                scenario_rows,
            ),
            "",
            "## 전 시나리오의 주요 조건 신호",
            "",
            f"언급 빈도 순서: {ranked_text}",
            "",
            *round_sections,
            "",
            "## 분석안: 다음 설계 원칙",
            "",
            markdown_table(["단계", "권고"], recommendation_rows),
            "",
            "## 다음 분석 단계",
            "",
            "1. 토론 로그의 조건을 안전 검증, 절차 참여, 부담·편익, 정보 공개로 코딩해 성향군별 차이를 비교한다.",
            "2. 후보지 공개·독립 검증·참여 및 지역 편익 시나리오의 순서가 조건부 수용성에 미치는 변화를 추가 실험한다.",
            "3. 실제 조사 결과와 대조할 때에는 가상 데이터와 실제 응답을 분리해 저장하고, 가상 토론 결과를 모집단 추정 근거로 사용하지 않는다.",
            "",
        ]
    )

    progress(90, "분석 메모 저장 중")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    progress(100, f"완료: {output_path}")


if __name__ == "__main__":
    main()
