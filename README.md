# UDL_P.P 로컬 LLM 주민 에이전트 MVP

## 프로젝트 목적

이 프로젝트는 비선호 공공시설 입지 갈등 분석 및 주민수용성 연구를 위한 로컬 LLM 기반 주민 에이전트 공청회 시뮬레이터의 1차 MVP입니다.

현재는 완성형 앱, 웹사이트, DB, FastAPI, LangGraph, Streamlit을 만들지 않습니다. Ollama의 `qwen3:8b` 모델을 사용해 가상 주민 에이전트가 공청회 상황에서 어떤 발언을 할 수 있는지 실험합니다.

## 현재 MVP 목표

- `qwen3:8b` 모델 설정을 파일로 관리합니다.
- 실제 개인정보나 실제 설문 원자료 없이 가상 주민 에이전트 상태값을 정의합니다.
- 여러 공청회 시나리오를 JSON으로 관리합니다.
- 설문 집계값을 에이전트 상태값 CSV로 정리하고 JSON으로 변환할 수 있습니다.
- 실행 중 진행률을 `%` 형태로 출력합니다.
- 실행 결과를 `logs/` 폴더에 JSON으로 저장합니다.

## Windows 프로젝트 위치

사용자 요청에 따라 프로젝트 폴더는 배경화면의 다음 위치를 사용합니다.

```powershell
C:\Users\user\Desktop\UDL_P.P
```

## 폴더 구조

```text
UDL_P.P
├─ README.md
├─ .gitignore
├─ requirements.txt
├─ ollama-models.txt
├─ config\
│  └─ llm_config.yaml
├─ scenarios\
│  └─ sample_scenarios.json
├─ prompts\
│  ├─ resident_agent_prompt.txt
│  ├─ moderator_prompt.txt
│  └─ evaluator_prompt.txt
├─ agents\
│  ├─ sample_resident_agents.json
│  └─ generated_resident_agents.json
├─ scripts\
│  ├─ test_ollama.py
│  ├─ convert_survey_template_to_agents.py
│  └─ run_resident_simulation.py
├─ logs\
│  └─ .gitkeep
├─ data_sandbox\
│  ├─ .gitkeep
│  └─ survey_agent_template.csv
└─ docs\
   ├─ windows_env_note.md
   └─ survey_data_guide.md
```

`generated_resident_agents.json`은 변환 스크립트를 실행하면 생성됩니다.

## Python 가상환경

이미 `.venv`를 만들었다면 다시 만들 필요는 없습니다. 새로 만들 때는 아래 명령어를 사용합니다.

```powershell
cd C:\Users\user\Desktop\UDL_P.P
python -m venv .venv
```

`python` 명령이 Microsoft Store로 연결되거나 찾을 수 없다고 나오면 Windows Python Launcher를 사용하세요.

```powershell
py -3 -m venv .venv
```

가상환경 활성화가 막히면 직접 `.venv`의 Python을 실행해도 됩니다.

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

PowerShell 실행 정책 오류를 해결하려면 아래 명령어를 한 번 실행합니다.

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Ollama qwen3:8b 모델 준비

```powershell
ollama pull qwen3:8b
ollama list
ollama run qwen3:8b
```

`ollama run qwen3:8b`에서 대화가 시작되면 모델 실행이 되는 상태입니다. 종료하려면 `/bye`를 입력합니다.

## 1회 연결 테스트

```powershell
cd C:\Users\user\Desktop\UDL_P.P
.\.venv\Scripts\python.exe scripts\test_ollama.py
```

## 여러 시나리오 실행

아래 명령어는 `agents/generated_resident_agents.json`이 있으면 그것을 우선 사용하고, 없으면 `agents/sample_resident_agents.json`을 사용합니다.

```powershell
.\.venv\Scripts\python.exe scripts\run_resident_simulation.py
```

실행 중에는 아래처럼 진행률이 출력됩니다.

```text
[  5%] 설정 파일 읽는 중
[ 15%] 에이전트 파일 읽는 중
[ 45%] 실행 대상 준비 완료: 에이전트 2명 x 시나리오 3개 = 6회
[ 62%] Ollama 응답 대기 중입니다. 모델 로딩 또는 생성이 진행 중일 수 있습니다.
[100%] 전체 완료. 로그 저장 위치: ...
```

입력 파일만 검증하고 Ollama 호출을 생략하려면 dry-run을 사용합니다.

```powershell
.\.venv\Scripts\python.exe scripts\run_resident_simulation.py --dry-run
```

시나리오 1개만 빠르게 실행하려면 다음처럼 제한할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe scripts\run_resident_simulation.py --max-scenarios 1
```

## 시나리오 추가 방법

`scenarios/sample_scenarios.json`에 항목을 추가합니다.

```json
{
  "scenario_id": "new_scenario_1",
  "title": "새 공청회 상황 제목",
  "description": "주민 에이전트가 발언해야 할 공청회 상황 설명"
}
```

`scenario_id`는 중복되지 않게 작성하세요.

## 설문 내용 추가 방법

실제 설문 원자료를 바로 넣지 말고, 먼저 1~5점 척도 집계값으로 정리합니다. 자세한 기준은 `docs/survey_data_guide.md`를 확인하세요.

1. `data_sandbox/survey_agent_template.csv`를 엽니다.
2. 실제 개인 응답이 아니라 군집별 평균값 또는 비식별 집계값만 입력합니다.
3. 아래 명령어로 JSON 에이전트 파일을 생성합니다.

```powershell
.\.venv\Scripts\python.exe scripts\convert_survey_template_to_agents.py
```

생성 결과:

```text
agents\generated_resident_agents.json
```

그 다음 실행합니다.

```powershell
.\.venv\Scripts\python.exe scripts\run_resident_simulation.py
```

## 성공 기준

- 터미널에 `%` 진행률이 단계별로 출력됩니다.
- 주민 에이전트 발언이 시나리오별로 출력됩니다.
- `logs/` 폴더에 `simulation_run_날짜시간.json` 파일이 생성됩니다.
- 로그 JSON에는 `agent`, `scenario`, `prompt`, `response`, `raw_result`가 포함됩니다.

## 자주 나는 오류와 해결법

### Ollama 서버에 연결할 수 없음

Ollama 앱이 실행 중인지 확인하세요. 필요하면 PowerShell에서 아래 명령어를 실행합니다.

```powershell
ollama serve
```

### 모델을 찾을 수 없음

```powershell
ollama pull qwen3:8b
ollama list
```

`ollama list` 결과에 `qwen3:8b`가 있어야 합니다.

### Python 또는 pip 명령을 찾을 수 없음

가상환경 Python을 직접 사용하세요.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\run_resident_simulation.py
```

### CSV 변환 오류

- 필수 컬럼 이름이 바뀌지 않았는지 확인하세요.
- 점수는 1~5 사이 숫자여야 합니다.
- 실제 개인정보나 원문 자유응답은 넣지 마세요.

## 데이터 보안 주의

실제 주민 설문 데이터, 개인정보, 원자료는 이 MVP에 넣지 마세요. 실제 자료가 필요해지는 단계에서는 `data_private/` 폴더에 따로 보관하고 Git에 올리지 마세요. `data_private/`는 `.gitignore`에 포함되어 있습니다.

## 다음 개발 단계 예고

다음 단계에서는 moderator/evaluator 프롬프트를 실제 실행 흐름에 연결하고, 에이전트별 발언을 평가 JSON으로 정리할 수 있습니다. FastAPI, LangGraph, DB, UI는 아직 만들지 않습니다.