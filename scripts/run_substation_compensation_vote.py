"""Run a synthetic-panel vote on the substation conditional compensation proposal."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from run_substation_deliberation import generate_with_progress, load_json, report_progress, select_panel


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AGENTS = PROJECT_ROOT / "agents" / "generated_substation_agents.json"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "llm_config.yaml"
DEFAULT_PLAN = PROJECT_ROOT / "scenarios" / "substation_compensation_plan.json"
PROMPT_PATH = PROJECT_ROOT / "prompts" / "substation_compensation_vote_prompt.txt"


def proposal_to_text(proposal: dict[str, Any]) -> str:
    sections = [
        ("선행 조건", proposal["preconditions"]),
        ("개별 피해 대응", proposal["individual_protection"]),
        ("지역 상생", proposal["community_benefit"]),
        ("운영·감시", proposal["governance"]),
    ]
    return "\n".join(f"[{title}]\n" + "\n".join(f"- {item}" for item in items) for title, items in sections)


def parse_vote(text: str) -> str:
    match = re.search(r"표결[^\n]{0,50}?(조건부\s*찬성|찬성|반대)", text)
    if not match:
        return "미분류"
    return re.sub(r"\s+", " ", match.group(1))


def main() -> None:
    parser = argparse.ArgumentParser(description="변전소 조건부 보상안 가상 패널 표결을 실행합니다.")
    parser.add_argument("--agents", type=Path, default=DEFAULT_AGENTS)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--panel-size", type=int, default=12)
    parser.add_argument("--seed", type=int, default=2606211000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report_progress(5, "보상안과 변전소 에이전트 읽는 중")
    raw_config = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    config = {
        "api_url": raw_config["base_url"],
        "model": raw_config["model"],
        "timeout_seconds": 180,
        "temperature": raw_config.get("temperature", {}).get("resident", 0.4),
        "max_tokens": 250,
    }
    agents = load_json(args.agents)["agents"]
    proposal = load_json(args.plan)
    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    proposal_text = proposal_to_text(proposal)

    report_progress(15, f"1,000명 중 대표 패널 {args.panel_size}명 층화 추출 중")
    panel = select_panel(agents, args.panel_size, args.seed)
    votes: list[dict[str, str]] = []
    for index, agent in enumerate(panel, start=1):
        prompt = prompt_template.format(
            cluster_name=agent["cluster_name"], proposal_text=proposal_text, **agent["state"]
        )
        percent = 20 + round((index - 1) / len(panel) * 70)
        text = generate_with_progress(config, prompt, percent, f"보상안 표결 {index}/{len(panel)}", args.dry_run)
        votes.append({"agent_id": agent["agent_id"], "cluster": agent["cluster_name"], "vote": parse_vote(text), "text": text})
        report_progress(20 + round(index / len(panel) * 70), f"보상안 표결 {index}/{len(panel)} 완료")

    tally = {label: sum(vote["vote"] == label for vote in votes) for label in ["찬성", "조건부 찬성", "반대", "미분류"]}
    affirmative = tally["찬성"] + tally["조건부 찬성"]
    threshold = (len(panel) * 2 + 2) // 3
    passed = affirmative >= threshold
    result = {
        "proposal": proposal,
        "panel_size": len(panel),
        "threshold": threshold,
        "tally": tally,
        "affirmative": affirmative,
        "passed": passed,
        "decision": "가상 패널 조건부 가결" if passed else "가상 패널 부결 또는 재논의",
        "votes": votes,
    }
    output_path = PROJECT_ROOT / "logs" / f"substation_compensation_vote_{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_progress(100, f"표결 완료: {result['decision']} / 로그: {output_path}")


if __name__ == "__main__":
    main()
