# 설문 내용을 에이전트에 추가하는 방법

이 MVP에는 실제 주민 개인정보나 실제 설문 원자료를 직접 넣지 않습니다. 설문 결과를 사용하려면 먼저 비식별 처리와 집계를 끝낸 뒤, 에이전트 상태값으로 요약해서 넣습니다.

## 권장 흐름

1. 실제 원자료는 `data_private/`에 따로 보관합니다.
2. `data_private/`는 Git에 올리지 않습니다.
3. 개인 응답 행 단위 데이터에서 이름, 주소, 연락처, 자유응답의 식별 가능 표현을 제거합니다.
4. 문항별 평균이나 군집별 평균을 계산합니다.
5. 1~5점 척도 상태값으로 정리합니다.
6. `data_sandbox/survey_agent_template.csv` 형식에 맞춰 집계값만 입력합니다.
7. 변환 스크립트로 `agents/generated_resident_agents.json`을 만듭니다.
8. 실행 스크립트가 생성된 에이전트 JSON을 읽어 시뮬레이션합니다.

## 상태값 컬럼 의미

| 컬럼 | 의미 |
| --- | --- |
| `agent_id` | 에이전트 식별자입니다. 실제 개인 ID가 아니라 `resident_cluster_1` 같은 가상 ID를 사용합니다. |
| `cluster_name` | 주민 유형 이름입니다. 예: `안전우려·불신형`, `조건검토형` |
| `risk_perception` | 위험 인식 수준입니다. 높을수록 안전 우려가 큽니다. |
| `benefit_perception` | 편익 인식 수준입니다. 높을수록 시설의 지역 편익을 크게 봅니다. |
| `distributive_fairness` | 부담과 보상이 공정하게 배분된다고 느끼는 정도입니다. |
| `procedural_fairness` | 의견수렴 절차가 공정하다고 느끼는 정도입니다. |
| `trust` | 지자체, 사업자, 전문가 설명에 대한 신뢰 수준입니다. |
| `place_attachment` | 거주지 또는 지역에 대한 애착 수준입니다. |
| `social_influence` | 주변 주민 의견의 영향을 받는 정도입니다. |
| `conditional_acceptance` | 조건이 충족될 경우 수용 가능성이 있는 정도입니다. |
| `conflict_action_likelihood` | 반대 행동이나 갈등 행동으로 이어질 가능성입니다. |

## 실제 설문 문항을 매핑하는 예

- 안전사고 우려, 건강 영향 우려, 환경 영향 우려 평균 → `risk_perception`
- 지역 보상 기대, 기반시설 개선 기대, 일자리 기대 평균 → `benefit_perception`
- 보상 배분 공정성, 부담 배분 공정성 평균 → `distributive_fairness`
- 설명회 충분성, 의견 반영 가능성, 정보 공개성 평균 → `procedural_fairness`
- 지자체 신뢰, 사업자 신뢰, 전문가 신뢰 평균 → `trust`
- 거주 지속 의향, 지역 정체성, 생활권 애착 평균 → `place_attachment`
- 이웃 의견 영향, 주민단체 의견 영향 평균 → `social_influence`
- 안전감시 조건부 수용, 보상 조건부 수용, 정보공개 조건부 수용 평균 → `conditional_acceptance`
- 민원 제기 의향, 집회 참여 의향, 반대 서명 의향 평균 → `conflict_action_likelihood`

## 주의

- 개인 한 명의 실제 응답을 그대로 에이전트로 만들지 마세요.
- 자유응답 원문은 식별 가능성이 있으므로 바로 프롬프트에 넣지 마세요.
- 이 MVP의 출력은 실제 예측값이 아니라 연구용 시뮬레이션 보조 결과입니다.