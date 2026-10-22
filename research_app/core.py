"""Reusable research-session services for the resident deliberation desktop app."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests
import duckdb
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.metrics import adjusted_rand_score, calinski_harabasz_score, davies_bouldin_score, silhouette_score
from sklearn.preprocessing import StandardScaler


FEATURES = [
    "risk_perception", "benefit_perception", "distributive_fairness", "procedural_fairness",
    "trust", "place_attachment", "social_influence", "conditional_acceptance", "conflict_action_likelihood",
]
COMPENSATION = [
    "electricity_discount", "one_time_cash", "long_term_living_support", "community_fund",
    "local_participation", "loss_compensation", "formal_council", "monitoring_committee",
]
ALL_FIELDS = FEATURES + COMPENSATION
DISPLAY = {
    "risk_perception": "위험 인식", "benefit_perception": "편익 인식", "distributive_fairness": "분배 공정성",
    "procedural_fairness": "절차 공정성", "trust": "신뢰", "place_attachment": "장소 애착",
    "social_influence": "사회적 영향", "conditional_acceptance": "조건부 수용", "conflict_action_likelihood": "갈등행동 가능성",
    "electricity_discount": "전기요금 감면", "one_time_cash": "일시금", "long_term_living_support": "장기 생활지원",
    "community_fund": "지역공동기금", "local_participation": "주민참여", "loss_compensation": "손실 보전",
    "formal_council": "공식 협의체", "monitoring_committee": "주민 감시기구",
}
HEADER_ALIASES = {key: [key, DISPLAY[key]] for key in ALL_FIELDS}

# Semantic rules for commonly used Korean NIMBY survey questionnaires. Each
# tuple is (all phrases that must appear in a question, reverse scored).
AUTO_QUESTION_RULES: dict[str, list[tuple[tuple[str, ...], bool]]] = {
    "risk_perception": [
        (("건강이나 안전", "부정적 영향"), False),
        (("경관이나 생활환경", "부정적 영향"), False),
    ],
    "benefit_perception": [
        (("전력 안정",), False),
        (("국가 에너지 안보",), False),
        (("지역경제나 일자리",), False),
    ],
    "distributive_fairness": [
        (("편익은 외부 지역", "부담을 더",), True),
    ],
    "procedural_fairness": [
        (("사업 초기부터 주민참여",), False),
    ],
    "trust": [
        (("주민 우려를 진지하게 고려",), False),
        (("정보를 투명하게 공개",), False),
        (("전문가나 연구기관", "신뢰",), False),
    ],
    "place_attachment": [
        (("강한 애착",), False),
        (("계속 거주",), False),
        (("자연환경과 경관", "보호",), False),
        (("지역의 정체성 변화",), False),
    ],
    "social_influence": [
        (("반대되는 입장", "의견을",), False),
    ],
    "conditional_acceptance": [
        (("적절한 조건", "일부 조정",), False),
        (("독립적 검증 결과", "의견을 조정",), False),
        (("절차 개선과 지역 편익",), False),
        (("변전소 설치", "더 수용적으로",), False),
    ],
    "conflict_action_likelihood": [
        (("입장은 쉽게 바뀌지",), False),
        (("아무리 보상이 커도", "수용할 수 없다"), False),
    ],
    "electricity_discount": [(("전기요금 할인",), False)],
    "one_time_cash": [(("1회성 현금보상",), False)],
    "long_term_living_support": [(("장기적 생활지원금",), False)],
    "community_fund": [(("장기적 지역공동기금",), False)],
    "local_participation": [(("주민 우선채용", "지역업체 우선계약"), False)],
    "loss_compensation": [(("재산가치 하락", "손실보전"), False)],
    "formal_council": [(("주민 대표", "공식 협의체"), False)],
    "monitoring_committee": [(("감시", "모니터링 위원회"), False)],
}


def atomic_json(path: Path, data: Any) -> None:
    """Replace JSON safely on Windows, where an observer can briefly lock the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(8):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.08 * (attempt + 1))


def read_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        try:
            return pd.read_csv(path, encoding="utf-8-sig")
        except UnicodeDecodeError:
            return pd.read_csv(path, encoding="cp949")
    return pd.read_excel(path)


def auto_mapping(headers: list[str]) -> dict[str, str]:
    normalized = {str(header).strip().lower(): str(header) for header in headers}
    mapping: dict[str, str] = {}
    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            found = normalized.get(alias.lower())
            if found:
                mapping[field] = found
                break
    return mapping


def _question_matches(header: str, phrases: tuple[str, ...]) -> bool:
    normalized = re.sub(r"\s+", "", str(header)).lower()
    return all(re.sub(r"\s+", "", phrase).lower() in normalized for phrase in phrases)


def _is_mostly_numeric(series: pd.Series) -> bool:
    values = pd.to_numeric(series, errors="coerce")
    return len(values) > 0 and float(values.notna().mean()) >= 0.7


def automatic_analysis_plan(frame: pd.DataFrame) -> dict[str, Any]:
    """Recommend transparent construct recipes from question wording.

    This is deliberately deterministic rather than letting a language model
    silently choose variables. Researchers can inspect every selected question
    and switch to manual mapping when the recommendation is unsuitable.
    """
    headers = [str(column) for column in frame.columns]
    direct = auto_mapping(headers)
    recipes: dict[str, list[dict[str, Any]]] = {}
    missing: list[str] = []
    for field in ALL_FIELDS:
        parts: list[dict[str, Any]] = []
        direct_header = direct.get(field)
        if direct_header and _is_mostly_numeric(frame[direct_header]):
            parts.append({"header": direct_header, "reverse": False})
        else:
            seen: set[str] = set()
            for phrases, reverse in AUTO_QUESTION_RULES.get(field, []):
                match = next((header for header in headers if header not in seen and _question_matches(header, phrases) and _is_mostly_numeric(frame[header])), None)
                if match:
                    parts.append({"header": match, "reverse": reverse})
                    seen.add(match)
        if parts:
            recipes[field] = parts
        elif field in FEATURES:
            missing.append(field)
    coverage = [
        {
            "field": field,
            "label": DISPLAY[field],
            "headers": [part["header"] for part in recipes.get(field, [])],
            "reverse_scored": any(part["reverse"] for part in recipes.get(field, [])),
        }
        for field in ALL_FIELDS
        if recipes.get(field)
    ]
    return {
        "ready": not missing,
        "recipes": recipes,
        "coverage": coverage,
        "missing": missing,
        "method_note": "질문 문구의 의미 규칙으로 같은 개념의 1~7점 문항을 묶어 평균 지표를 만듭니다. 선택 문항과 역채점 여부는 결과에 저장됩니다.",
    }


def automatic_feature_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    plan = automatic_analysis_plan(frame)
    if not plan["ready"]:
        names = ", ".join(DISPLAY[field] for field in plan["missing"])
        raise ValueError(f"자동 문항 해석으로 필수 지표를 찾지 못했습니다: {names}. 수동 선택 모드에서 직접 연결해 주세요.")
    prepared = pd.DataFrame(index=frame.index)
    for field in FEATURES:
        values: list[pd.Series] = []
        for part in plan["recipes"][field]:
            numeric = pd.to_numeric(frame[part["header"]], errors="coerce")
            values.append(8 - numeric if part["reverse"] else numeric)
        prepared[field] = pd.concat(values, axis=1).mean(axis=1)
    for field in COMPENSATION:
        parts = plan["recipes"].get(field, [])
        if not parts:
            continue
        values = [pd.to_numeric(frame[part["header"]], errors="coerce") for part in parts]
        prepared[field] = pd.concat(values, axis=1).mean(axis=1)
    return prepared, plan


def analyze_survey_automatically(frame: pd.DataFrame, min_k: int, max_k: int, seed: int) -> dict[str, Any]:
    prepared, plan = automatic_feature_frame(frame)
    analysis = analyze_survey(prepared, {field: field for field in prepared.columns}, min_k, max_k, seed)
    analysis["input"].update({
        "analysis_mode": "자동 질문 해석",
        "automatic_recipe": plan["recipes"],
        "automatic_coverage": plan["coverage"],
        "automatic_method_note": plan["method_note"],
        "original_columns": int(len(frame.columns)),
    })
    return analysis


def validate_mapping(mapping: dict[str, str], headers: list[str]) -> None:
    missing = [field for field in FEATURES if not mapping.get(field)]
    unknown = [source for source in mapping.values() if source and source not in headers]
    if missing:
        raise ValueError("군집분석 필수 항목이 매핑되지 않았습니다: " + ", ".join(DISPLAY[item] for item in missing))
    if unknown:
        raise ValueError("선택한 원본 열을 찾지 못했습니다: " + ", ".join(unknown))


def rank_desc(values: list[float]) -> list[int]:
    order = sorted(range(len(values)), key=lambda index: values[index], reverse=True)
    result = [0] * len(values)
    for rank, index in enumerate(order, start=1):
        result[index] = rank
    return result


def rank_asc(values: list[float]) -> list[int]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    result = [0] * len(values)
    for rank, index in enumerate(order, start=1):
        result[index] = rank
    return result


def persona_label(profile: dict[str, float], overall: dict[str, float]) -> str:
    if profile["risk_perception"] >= overall["risk_perception"] + 0.2 and profile["trust"] <= overall["trust"] - 0.15:
        return "안전검증 우선형"
    if profile["conditional_acceptance"] <= overall["conditional_acceptance"] - 0.2:
        return "수용조건 엄격형"
    if profile["procedural_fairness"] >= overall["procedural_fairness"] + 0.15:
        return "참여협의 우선형"
    if profile["benefit_perception"] >= overall["benefit_perception"] + 0.15:
        return "공공편익 검토형"
    return "신중검토형"


def analyze_survey(frame: pd.DataFrame, mapping: dict[str, str], min_k: int, max_k: int, seed: int) -> dict[str, Any]:
    headers = [str(column) for column in frame.columns]
    validate_mapping(mapping, headers)
    if max_k > PANEL_SIZE:
        raise ValueError(f"최대 k는 토론 패널 수({PANEL_SIZE}명)를 넘을 수 없습니다. 모든 군집을 대표하려면 2~{PANEL_SIZE} 범위에서 선택해 주세요.")
    numeric = pd.DataFrame({field: pd.to_numeric(frame[mapping[field]], errors="coerce") for field in FEATURES})
    before = len(numeric)
    numeric = numeric.dropna()
    if len(numeric) < 20:
        raise ValueError("결측값을 제외한 유효 응답이 20명 미만입니다.")
    if ((numeric < 1) | (numeric > 7)).any().any():
        raise ValueError("군집분석 입력은 1~5점 또는 1~7점 범위여야 합니다.")
    # Preserve precomputed 1~5 indicators; standardize common 1~7 survey items.
    input_scale = "1~5점 지표" if numeric.max().max() <= 5 else "1~7점 설문"
    features = numeric.copy() if input_scale == "1~5점 지표" else 1 + (numeric - 1) * 4 / 6
    compensation = pd.DataFrame(index=features.index)
    for field in COMPENSATION:
        source = mapping.get(field)
        if source:
            values = pd.to_numeric(frame.loc[features.index, source], errors="coerce")
            compensation[field] = values if input_scale == "1~5점 지표" else 1 + (values - 1) * 4 / 6
        else:
            compensation[field] = 3.0
    compensation = compensation.fillna(3.0).clip(1, 5)
    matrix = StandardScaler().fit_transform(features.to_numpy())
    upper = min(max_k, len(features) - 1)
    lower = max(2, min_k)
    if lower > upper:
        raise ValueError("응답 수에 비해 설정한 군집 수 범위가 너무 큽니다.")
    candidates: list[dict[str, Any]] = []
    for k in range(lower, upper + 1):
        model = KMeans(n_clusters=k, n_init=50, random_state=seed)
        labels = model.fit_predict(matrix)
        candidates.append({
            "k": k, "silhouette": float(silhouette_score(matrix, labels)),
            "calinski_harabasz": float(calinski_harabasz_score(matrix, labels)),
            "davies_bouldin": float(davies_bouldin_score(matrix, labels)), "inertia": float(model.inertia_),
        })
    sd = rank_desc([item["silhouette"] for item in candidates])
    ch = rank_desc([item["calinski_harabasz"] for item in candidates])
    db = rank_asc([item["davies_bouldin"] for item in candidates])
    for index, item in enumerate(candidates):
        item["composite_rank"] = sd[index] + ch[index] + db[index]
    selected = min(candidates, key=lambda item: (item["composite_rank"], -item["silhouette"], item["k"]))
    k = int(selected["k"])
    model = KMeans(n_clusters=k, n_init=100, random_state=seed)
    labels = model.fit_predict(matrix)
    ward = AgglomerativeClustering(n_clusters=k, linkage="ward").fit_predict(matrix)
    # Records whether the selected partition remains similar under resampling.
    rng = np.random.default_rng(seed)
    bootstrap_scores: list[float] = []
    for _ in range(20):
        sample = rng.choice(len(matrix), size=len(matrix), replace=True)
        sampled_model = KMeans(n_clusters=k, n_init=30, random_state=int(rng.integers(1, 2**31 - 1)))
        sampled_model.fit(matrix[sample])
        bootstrap_scores.append(float(adjusted_rand_score(labels, sampled_model.predict(matrix))))
    order = sorted(range(k), key=lambda group: features.iloc[labels == group]["conditional_acceptance"].mean())
    label_map = {old: new + 1 for new, old in enumerate(order)}
    clusters = np.array([label_map[int(label)] for label in labels])
    overall = features.mean().to_dict()
    agents: list[dict[str, Any]] = []
    personas: list[dict[str, Any]] = []
    for cluster_id in range(1, k + 1):
        positions = np.where(clusters == cluster_id)[0]
        profile = features.iloc[positions].mean().round(3).to_dict()
        preferences = compensation.iloc[positions].mean().round(3).to_dict()
        scaled_positions = matrix[positions]
        medoid = int(positions[np.argmin(np.linalg.norm(scaled_positions - scaled_positions.mean(axis=0), axis=1))])
        personas.append({
            "cluster_id": cluster_id, "persona_name": f"페르소나 {cluster_id}: {persona_label(profile, overall)}",
            "member_count": int(len(positions)), "member_share": round(float(len(positions) / len(features)), 4),
            "representative_agent_id": f"RESP-{medoid + 1:04d}", "state_profile": profile,
            "compensation_preferences": preferences,
            "profile_summary": "높은 특성: " + ", ".join(DISPLAY[key] for key, value in profile.items() if value >= overall[key] + 0.2) + ".",
        })
    for position, (_, row) in enumerate(features.iterrows()):
        agent = {"agent_id": f"RESP-{position + 1:04d}", "cluster_id": int(clusters[position])}
        agent.update({field: round(float(row[field]), 3) for field in FEATURES})
        agent.update({field: round(float(compensation.iloc[position][field]), 3) for field in COMPENSATION})
        agents.append(agent)
    return {
        "input": {"valid_respondents": len(features), "dropped_rows": before - len(features), "mapping": mapping, "seed": seed, "input_scale": input_scale},
        "validation": {"selected_k": k, "selected_metrics": selected, "all_candidates": candidates,
                       "ward_kmeans_adjusted_rand_index": round(float(adjusted_rand_score(labels, ward)), 4),
                       "bootstrap_mean_adjusted_rand_index": round(float(np.mean(bootstrap_scores)), 4),
                       "interpretation": "군집은 탐색적 세분화이며, 단일 지표가 아닌 군집 수 후보 비교·Ward 일치도를 함께 해석해야 합니다."},
        "personas": personas, "agents": agents,
    }


BACKGROUND_DATASET = "nvidia/Nemotron-Personas-Korea"
BACKGROUND_ENDPOINT = "https://datasets-server.huggingface.co/filter"
BACKGROUND_PARQUET_ENDPOINT = "https://datasets-server.huggingface.co/parquet"
BACKGROUND_REQUIRED_FIELDS = {"age", "sex", "occupation", "district", "province"}
BACKGROUND_COLUMNS = ["age", "sex", "occupation", "district", "province", "housing_type", "family_type", "persona", "cultural_background", "family_persona"]
_BACKGROUND_PARQUET_URLS: list[str] | None = None


def _query_background_rows(where: str, count: int) -> list[dict[str, Any]]:
    """Query the dataset server with a small retry for transient network failures."""
    params = {
        "dataset": BACKGROUND_DATASET,
        "config": "default",
        "split": "train",
        "offset": 0,
        "length": max(12, count),
        "where": where,
    }
    last_error: requests.RequestException | None = None
    for attempt in range(3):
        try:
            response = requests.get(BACKGROUND_ENDPOINT, params=params, timeout=20)
            response.raise_for_status()
            return [item["row"] for item in response.json().get("rows", [])]
        except requests.RequestException as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))
    raise last_error or requests.RequestException("NVIDIA background request failed")


def _background_parquet_urls() -> list[str]:
    global _BACKGROUND_PARQUET_URLS
    if _BACKGROUND_PARQUET_URLS is None:
        response = requests.get(BACKGROUND_PARQUET_ENDPOINT, params={"dataset": BACKGROUND_DATASET}, timeout=30)
        response.raise_for_status()
        _BACKGROUND_PARQUET_URLS = [item["url"] for item in response.json().get("parquet_files", [])]
    if not _BACKGROUND_PARQUET_URLS:
        raise requests.RequestException("NVIDIA Parquet file list is empty")
    return _BACKGROUND_PARQUET_URLS


def _query_background_rows_from_parquet(column: str, value: str, count: int) -> list[dict[str, Any]]:
    """Reliable fallback when Dataset Viewer filtering is unavailable or delayed."""
    if column not in {"district", "province"}:
        raise ValueError(f"Unsupported background lookup column: {column}")
    connection = duckdb.connect()
    try:
        connection.execute("SET enable_progress_bar=false")
        columns = ", ".join(BACKGROUND_COLUMNS)
        rows = connection.execute(
            f"SELECT {columns} FROM read_parquet(?) WHERE {column} = ? ORDER BY uuid LIMIT ?",
            [_background_parquet_urls(), value, max(12, count)],
        ).fetchall()
        return [dict(zip(BACKGROUND_COLUMNS, row)) for row in rows]
    finally:
        connection.close()


def _lookup_backgrounds(column: str, value: str, count: int) -> tuple[list[dict[str, Any]], str]:
    """Use Parquet first; the public Dataset Viewer filter is intermittently 500ing."""
    where = f'"{column}" = \'{value}\''
    try:
        rows = _query_background_rows_from_parquet(column, value, count)
        return rows, "parquet"
    except (requests.RequestException, duckdb.Error):
        rows = _query_background_rows(where, count)
        return rows, "filter"


def background_data_source_health() -> dict[str, Any]:
    """Expose a visible health signal instead of silently falling back to blanks."""
    try:
        response = requests.get(
            "https://datasets-server.huggingface.co/rows",
            params={"dataset": BACKGROUND_DATASET, "config": "default", "split": "train", "offset": 0, "length": 1},
            timeout=10,
        )
        response.raise_for_status()
        fields = {item.get("name") for item in response.json().get("features", [])}
        missing = sorted(BACKGROUND_REQUIRED_FIELDS - fields)
        if missing:
            return {"status": "schema_error", "dataset": BACKGROUND_DATASET, "message": f"필수 필드 누락: {', '.join(missing)}"}
        parquet_response = requests.get(BACKGROUND_PARQUET_ENDPOINT, params={"dataset": BACKGROUND_DATASET}, timeout=15)
        parquet_response.raise_for_status()
        file_count = len(parquet_response.json().get("parquet_files", []))
        if not file_count:
            return {"status": "schema_error", "dataset": BACKGROUND_DATASET, "message": "NVIDIA Parquet 원본 파일 목록이 비어 있습니다."}
        return {"status": "connected", "dataset": BACKGROUND_DATASET, "message": f"NVIDIA 주민 배경 데이터셋 연결·필수 필드·Parquet 원본 {file_count}개 확인 완료"}
    except requests.RequestException as error:
        return {"status": "unavailable", "dataset": BACKGROUND_DATASET, "message": f"NVIDIA 주민 배경 데이터셋 연결 실패: {error}"}


def fetch_backgrounds(district: str, count: int, province: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Retrieve exact district backgrounds, with an explicit province-only fallback."""
    district = district.strip()
    province = province.strip()
    if not district:
        return [], {"status": "missing_region", "label": "지역 미선택", "requested_district": "", "matched_district": ""}
    try:
        rows, method = _lookup_backgrounds("district", district, count)
        if rows:
            return rows[:count], {"status": "exact", "label": "NVIDIA 지역 배경 정확 일치", "requested_district": district, "matched_district": district, "lookup_method": method}
        if province:
            rows, method = _lookup_backgrounds("province", province, count)
            if rows:
                return rows[:count], {"status": "province_fallback", "label": "NVIDIA 광역권 배경 보완", "requested_district": district, "matched_district": "", "matched_province": province, "lookup_method": method}
        return [], {"status": "not_found", "label": "NVIDIA 지역 배경 미발견", "requested_district": district, "matched_district": ""}
    except (requests.RequestException, duckdb.Error) as error:
        return [], {"status": "unavailable", "label": "NVIDIA 지역 배경 데이터셋 연결 실패", "requested_district": district, "matched_district": "", "error": str(error)}


PANEL_SIZE = 12


def proportional_panel_allocation(personas: list[dict[str, Any]], total_agents: int, panel_size: int = PANEL_SIZE) -> dict[int, int]:
    """Allocate the fixed discussion panel by cluster share (largest remainder)."""
    target = min(panel_size, total_agents)
    counts = {int(item["cluster_id"]): int(item.get("member_count", 0)) for item in personas}
    if not counts or target <= 0:
        return {}
    raw = {cluster_id: count / total_agents * target for cluster_id, count in counts.items()}
    allocation = {cluster_id: int(np.floor(value)) for cluster_id, value in raw.items()}
    if len(counts) <= target:
        for cluster_id in allocation:
            allocation[cluster_id] = max(1, allocation[cluster_id])
    while sum(allocation.values()) > target:
        removable = [cluster_id for cluster_id, value in allocation.items() if value > 1]
        if not removable:
            removable = [cluster_id for cluster_id, value in allocation.items() if value > 0]
        cluster_id = min(removable, key=lambda item: (raw[item] - allocation[item], counts[item]))
        allocation[cluster_id] -= 1
    while sum(allocation.values()) < target:
        cluster_id = max(counts, key=lambda item: (raw[item] - allocation[item], counts[item]))
        allocation[cluster_id] += 1
    return allocation


def diverse_cluster_selection(members: list[dict[str, Any]], slots: int) -> list[dict[str, Any]]:
    """Select a representative first, then substantively distinct members."""
    if slots <= 0:
        return []
    center = np.array([np.mean([agent[field] for agent in members]) for field in FEATURES])
    medoid = min(members, key=lambda agent: float(np.linalg.norm(np.array([agent[field] for field in FEATURES]) - center)))
    conflict = max(members, key=lambda agent: agent["conflict_action_likelihood"] + agent["risk_perception"])
    flexible = max(members, key=lambda agent: agent["conditional_acceptance"] + agent["procedural_fairness"])
    selected: list[dict[str, Any]] = []
    for candidate in (medoid, conflict, flexible):
        if candidate["agent_id"] not in {item["agent_id"] for item in selected}:
            selected.append(candidate)
        if len(selected) >= slots:
            return selected
    remaining = [item for item in members if item["agent_id"] not in {chosen["agent_id"] for chosen in selected}]
    while remaining and len(selected) < slots:
        candidate = max(remaining, key=lambda item: min(float(np.linalg.norm(np.array([item[field] for field in FEATURES]) - np.array([chosen[field] for field in FEATURES]))) for chosen in selected))
        selected.append(candidate)
        remaining = [item for item in remaining if item["agent_id"] != candidate["agent_id"]]
    return selected


def build_panel(analysis: dict[str, Any], district: str, facility: dict[str, Any], province: str = "") -> dict[str, Any]:
    agents = analysis["agents"]
    personas = analysis["personas"]
    allocation = proportional_panel_allocation(personas, len(agents))
    selected: list[dict[str, Any]] = []
    for persona in personas:
        cluster_id = int(persona["cluster_id"])
        members = [agent for agent in agents if agent["cluster_id"] == cluster_id]
        selected.extend(diverse_cluster_selection(members, allocation.get(cluster_id, 0)))
    backgrounds, background_status = fetch_backgrounds(district, len(selected), province)
    conflict_cut = float(np.quantile([agent["conflict_action_likelihood"] for agent in agents], 2 / 3))
    acceptance_low = float(np.quantile([agent["conditional_acceptance"] for agent in agents], 1 / 3))
    panel = []
    for index, agent in enumerate(selected, start=1):
        background = backgrounds[index - 1] if index <= len(backgrounds) else {}
        high_conflict = agent["conflict_action_likelihood"] >= conflict_cut
        hardline = high_conflict and agent["conditional_acceptance"] <= acceptance_low
        top_comp = sorted(COMPENSATION, key=lambda key: agent[key], reverse=True)[:3]
        panel.append({
            "speaker_id": f"R{index:02d}", "display_name": f"주민 {index}", "source_agent_id": agent["agent_id"], "cluster_id": agent["cluster_id"],
            "survey_profile": {key: agent[key] for key in FEATURES}, "compensation_preferences": {key: agent[key] for key in COMPENSATION},
            "conversation_traits": {"assertiveness": "높음" if high_conflict else "보통", "turn_taking_risk": "높음" if hardline else "보통",
                                    "concession_tendency": "낮음" if hardline else "보통", "hardline": hardline,
                                    "red_line": "안전·절차 문제가 해소되지 않으면 수용하지 않음" if hardline else "피해보전과 주민 참여가 불명확하면 수용하지 않음",
                                    "negotiable": "독립 검증, 참여권, 피해보전의 구체성"},
            "background": {"source": "nvidia/Nemotron-Personas-Korea (CC BY 4.0)" if background else "NVIDIA 지역 배경 미연결",
                           "age": background.get("age"), "sex": background.get("sex") if background else None, "occupation": background.get("occupation") if background else None,
                           "district": background.get("district") if background else district, "housing_type": background.get("housing_type") if background else None, "family_type": background.get("family_type") if background else None,
                           "persona": background.get("persona") if background else "지역 배경 데이터셋을 불러오지 못해 배경 서사를 적용하지 않았습니다.",
                           "cultural_background": background.get("cultural_background") if background else "지역 배경 데이터셋 연결 상태를 확인한 뒤 다시 패널을 생성해 주세요.",
                           "family_persona": background.get("family_persona") if background else ""},
            "top_compensation": [DISPLAY[key] for key in top_comp],
        })
    concerns = facility.get("concerns", []) or ["안전검증", "생활환경", "피해보전", "주민 참여"]
    cluster_allocation = [
        {"cluster_id": int(persona["cluster_id"]), "survey_members": int(persona.get("member_count", 0)),
         "survey_share": round(float(persona.get("member_count", 0)) / len(agents), 4) if agents else 0,
         "panel_members": allocation.get(int(persona["cluster_id"]), 0)}
        for persona in personas
    ]
    return {"metadata": {"target_district": district, "facility": facility.get("name"), "background_match": background_status["label"], "background_status": background_status,
                         "survey_respondents": len(agents), "panel_size": len(selected), "cluster_allocation": cluster_allocation,
                         "attribution": "NVIDIA, Nemotron-Personas-Korea, CC BY 4.0"}, "panel": panel,
            "speaker_foci": {member["speaker_id"]: concerns[(index - 1) % len(concerns)] for index, member in enumerate(panel, start=1)}}


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]
