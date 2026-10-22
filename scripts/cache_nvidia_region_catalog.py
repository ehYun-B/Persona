"""Build the exact regional lookup catalog from Nemotron-Personas-Korea.

Run this during app maintenance, not at normal app startup. DuckDB reads only
the province/district columns from the public Parquet shards and writes a small
checked-in JSON catalog used by the desktop app.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT = PROJECT_ROOT / "research_app" / "nvidia_region_catalog.json"
DATASET = "nvidia/Nemotron-Personas-Korea"
PROVINCE_DISPLAY = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
    "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
    "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
    "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도", "제주": "제주특별자치도",
}


def main() -> None:
    response = requests.get("https://datasets-server.huggingface.co/parquet", params={"dataset": DATASET}, timeout=30)
    response.raise_for_status()
    urls = [item["url"] for item in response.json().get("parquet_files", [])]
    if not urls:
        raise RuntimeError("NVIDIA Parquet 파일 목록을 찾지 못했습니다.")

    connection = duckdb.connect()
    connection.execute("SET enable_progress_bar=false")
    rows = connection.execute(
        "SELECT DISTINCT province, district FROM read_parquet(?) ORDER BY province, district",
        [urls],
    ).fetchall()
    items = []
    for province, district in rows:
        province = str(province)
        district = str(district)
        suffix = district.split("-", 1)[1] if "-" in district else district
        province_display = PROVINCE_DISPLAY.get(province, province)
        display = f"{province_display} {suffix}"
        items.append({
            "id": district,
            "display": display,
            "province": province,
            "province_display": province_display,
            "district": district,
            "district_display": suffix,
            "search_text": " ".join((display, province, district, suffix)),
        })
    OUT.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(items)}개 지역 키 저장: {OUT}")


if __name__ == "__main__":
    main()
