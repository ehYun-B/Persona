"""Create a research-facing conclusion and policy proposal from synthetic deliberation artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports"
LOGS_DIR = PROJECT_ROOT / "logs"


def latest_vote_log() -> Path:
    files = sorted(LOGS_DIR.glob("substation_compensation_vote_*.json"), key=lambda path: path.stat().st_mtime)
    if not files:
        raise FileNotFoundError("보상안 표결 로그를 찾지 못했습니다.")
    return files[-1]


def bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def main() -> None:
    parser = argparse.ArgumentParser(description="변전소 결론·정책제안·보상안 보고서를 생성합니다.")
    parser.add_argument("--vote-log", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=REPORTS_DIR / "substation_conclusion_policy_compensation.md")
    args = parser.parse_args()

    vote_log = args.vote_log or latest_vote_log()
    vote = json.loads(vote_log.read_text(encoding="utf-8"))
    proposal: dict[str, Any] = vote["proposal"]
    tally: dict[str, int] = vote["tally"]
    decision = vote["decision"]

    report = "\n".join(
        [
            "# 변전소 가상 토론 기반 결론·정책제안·조건부 보상안",
            "",
            "## 해석 범위",
            "",
            "이 문서는 익명 가상 설문 1,000명에서 층화 추출한 12명 패널의 LLM 토론과 표결을 정리한 연구용 초안이다. 실제 주민 의결, 보상 계약, 법률·안전 판단을 대체하지 않는다.",
            "",
            "## 결론",
            "",
            "변전소 입지의 수용성은 금전 보상만으로 형성되지 않는다. 입지 공개 이전의 후보지·대안 비교, 독립적인 안전·생활환경 검증, 주민이 권한을 가진 참여 절차가 먼저 갖춰질 때 보상과 지역 상생 논의가 의미를 가진다.",
            "",
            "토론에서는 안전·영향 검증과 주민 참여·절차가 모든 시나리오에서 반복적으로 언급됐다. 따라서 정책의 중심은 '보상으로 동의를 얻는 방식'이 아니라, 검증·참여·피해 대응·지역 편익을 함께 보장하는 조건부 합의 구조여야 한다.",
            "",
            "## 정책 제안",
            "",
            "### 1. 입지 공개 단계",
            bullets([
                "후보지 선정 기준, 대안 후보지, 비교 가능한 영향 항목을 같은 형식으로 공개한다.",
                "주민 질의와 대안 제안에 대한 공식 답변 기한을 정하고 공개 기록으로 남긴다.",
            ]),
            "",
            "### 2. 독립 검증과 정보 공개",
            bullets([
                "안전·생활환경 영향의 검토 범위, 검토 주체, 원자료 공개 범위, 재검증 요청 절차를 사전에 문서화한다.",
                "독립 검토 결과와 사업자 설명을 구분해 공개하고, 주민이 이해 가능한 요약본을 함께 제공한다.",
            ]),
            "",
            "### 3. 참여 거버넌스",
            bullets([
                "주민 대표 협의체와 상시 모니터링 체계를 설치하고 권한, 회의 주기, 민원 응답 기한을 명시한다.",
                "중대한 우려나 영향 변화가 확인되면 추가 검증과 개선 조치를 재논의하는 절차를 둔다.",
            ]),
            "",
            "### 4. 부담과 편익의 분리 관리",
            bullets([
                "개별 생활 피해·재산 영향 대응과 지역 공동기금의 운영을 별도 기준으로 관리한다.",
                "실제 금액·지원 기간은 사업비, 법적 근거, 영향 평가, 협의 결과에 따라 별도 산정하며 가상 토론 결과만으로 결정하지 않는다.",
            ]),
            "",
            "## 가상 패널 표결 결과",
            "",
            f"- 의결 방식: 12명 가상 패널, 찬성 또는 조건부 찬성 {vote['threshold']}명 이상이면 조건부 가결",
            f"- 표결: 찬성 {tally['찬성']}명, 조건부 찬성 {tally['조건부 찬성']}명, 반대 {tally['반대']}명, 미분류 {tally['미분류']}명",
            f"- 결과: **{decision}**",
            "",
            "## 조건부 가결 보상·상생안",
            "",
            f"> {proposal['interpretation']}",
            "",
            "### 선행 조건",
            bullets(proposal["preconditions"]),
            "",
            "### 개별 피해 대응",
            bullets(proposal["individual_protection"]),
            "",
            "### 지역 상생",
            bullets(proposal["community_benefit"]),
            "",
            "### 운영·감시",
            bullets(proposal["governance"]),
            "",
            "## 적용 시 유의점",
            "",
            bullets([
                "가상 패널의 가결은 실제 주민의 동의나 법적 효력을 뜻하지 않는다.",
                "개별 피해와 보전 기준은 독립 조사와 법률·행정 검토를 거쳐야 한다.",
                "실제 협의에서는 취약 집단, 생활권, 후보지별 영향 차이를 별도로 확인해야 한다.",
            ]),
            "",
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"[100%] 결론 보고서 저장: {args.output}")


if __name__ == "__main__":
    main()
