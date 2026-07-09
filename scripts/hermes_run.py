#!/usr/bin/env python3
"""Hermes Content Radar.

Scheduled GitHub Actions runner for collecting LinkedIn/note topic ideas.

Modes:
  - daily: daily tech / real-estate AI radar
  - jsai: JSAI and academic deep dive
  - weekly: weekly editorial planning from accumulated outputs

The script writes Markdown outputs under outputs/<mode>/YYYY-MM-DD.md and, when
GITHUB_TOKEN is available, creates a GitHub Issue for review.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import textwrap
from pathlib import Path
from typing import Iterable

import requests
import yaml
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
JST = dt.timezone(dt.timedelta(hours=9), name="JST")

MODE_CONFIG = {
    "daily": {
        "prompt": "prompts/daily_tech.md",
        "output_dir": "outputs/daily",
        "issue_prefix": "[Hermes Daily]",
        "sources": [
            "sources/tech_sources.yml",
            "sources/real_estate_ai_sources.yml",
        ],
    },
    "jsai": {
        "prompt": "prompts/jsai_deep_dive.md",
        "output_dir": "outputs/jsai",
        "issue_prefix": "[Hermes JSAI]",
        "sources": [
            "sources/jsai_sources.yml",
            "sources/real_estate_ai_sources.yml",
        ],
    },
    "weekly": {
        "prompt": "prompts/weekly_editorial.md",
        "output_dir": "outputs/weekly",
        "issue_prefix": "[Hermes Weekly]",
        "sources": [
            "sources/tech_sources.yml",
            "sources/jsai_sources.yml",
            "sources/real_estate_ai_sources.yml",
        ],
    },
}

MEMORY_FILES = [
    "memory/profile.md",
    "memory/editorial_policy.md",
    "memory/post_feedback.csv",
    "memory/source_scores.json",
    "memory/banned_phrases.md",
]


def read_text(path: str) -> str:
    p = ROOT / path
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def read_sources(paths: Iterable[str]) -> str:
    blocks: list[str] = []
    for path in paths:
        raw = read_text(path)
        if not raw:
            continue
        try:
            parsed = yaml.safe_load(raw)
            blocks.append(f"## {path}\n{yaml.safe_dump(parsed, allow_unicode=True, sort_keys=False)}")
        except Exception:
            blocks.append(f"## {path}\n{raw}")
    return "\n\n".join(blocks)


def recent_outputs(days: int = 7) -> str:
    cutoff = dt.datetime.now(JST).date() - dt.timedelta(days=days)
    blocks: list[str] = []
    for folder in [ROOT / "outputs" / "daily", ROOT / "outputs" / "jsai"]:
        if not folder.exists():
            continue
        for file in sorted(folder.glob("*.md")):
            try:
                file_date = dt.date.fromisoformat(file.stem)
            except ValueError:
                continue
            if file_date >= cutoff:
                body = file.read_text(encoding="utf-8")
                blocks.append(f"## {file.relative_to(ROOT)}\n{body[:6000]}")
    return "\n\n".join(blocks) if blocks else "No recent outputs yet."


def build_prompt(mode: str, today: dt.date) -> str:
    cfg = MODE_CONFIG[mode]
    system_prompt = read_text("prompts/system_hermes.md")
    task_prompt = read_text(cfg["prompt"])
    memory = "\n\n".join(
        f"## {path}\n{read_text(path)}" for path in MEMORY_FILES if read_text(path)
    )
    sources = read_sources(cfg["sources"])
    weekly_context = recent_outputs() if mode == "weekly" else ""

    return textwrap.dedent(
        f"""
        {system_prompt}

        # Today
        {today.isoformat()} JST

        # Task
        {task_prompt}

        # Hermes Memory
        {memory}

        # Source Hints
        {sources}

        # Recent Outputs for Weekly Review
        {weekly_context}

        # Required Markdown Output

        必ず日本語Markdownで出力してください。各ネタには可能な限りURLを付けてください。
        出典不明・要確認の情報は「保留・要確認」に回してください。
        自動投稿は行わず、LinkedIn/noteの投稿候補として出してください。
        """
    ).strip()


def call_openai(prompt: str) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Add it as a GitHub Actions secret.")

    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=api_key)

    try:
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
        )
    except Exception as exc:
        response = client.responses.create(
            model=model,
            input=(
                prompt
                + "\n\n注意: web_search_preview の呼び出しに失敗したため、利用可能な知識と与えられたソースヒントだけで出力してください。"
                + f"\nエラー概要: {type(exc).__name__}: {exc}"
            ),
        )

    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(text)
    if chunks:
        return "\n".join(chunks)
    return str(response)


def write_output(mode: str, today: dt.date, body: str) -> Path:
    cfg = MODE_CONFIG[mode]
    out_dir = ROOT / cfg["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{today.isoformat()}.md"
    header = textwrap.dedent(
        f"""
        # {cfg['issue_prefix']} {today.isoformat()}

        - Generated at: {dt.datetime.now(JST).isoformat()}
        - Mode: `{mode}`
        - Model: `{os.environ.get('OPENAI_MODEL', 'gpt-4.1-mini')}`
        - Human review required: yes

        ---
        """
    ).strip()
    path.write_text(header + "\n\n" + body.strip() + "\n", encoding="utf-8")
    return path


def create_issue(title: str, body: str) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("GITHUB_TOKEN or GITHUB_REPOSITORY is not set; skip issue creation.")
        return

    if os.environ.get("CREATE_GITHUB_ISSUE", "true").lower() not in {"1", "true", "yes"}:
        print("CREATE_GITHUB_ISSUE is false; skip issue creation.")
        return

    url = f"https://api.github.com/repos/{repo}/issues"
    res = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"title": title, "body": body[:60000]},
        timeout=30,
    )
    if res.status_code >= 300:
        raise RuntimeError(f"Failed to create issue: {res.status_code} {res.text}")
    print(f"Created issue: {res.json().get('html_url')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=sorted(MODE_CONFIG), required=True)
    args = parser.parse_args()

    today = dt.datetime.now(JST).date()
    prompt = build_prompt(args.mode, today)
    body = call_openai(prompt)
    out_path = write_output(args.mode, today, body)

    cfg = MODE_CONFIG[args.mode]
    issue_title = f"{cfg['issue_prefix']} {today.isoformat()} 投稿ネタ候補"
    issue_body = (
        f"Hermes output file: `{out_path.relative_to(ROOT)}`\n\n"
        "フィードバック例:\n\n"
        "```\n"
        "案1 採用。PDF行政資料ネタは強い。\n"
        "案2 ボツ。AI一般論すぎる。\n"
        "案3 保留。JSAI寄りでnote向き。\n"
        "```\n\n"
        "---\n\n"
        + body
    )
    create_issue(issue_title, issue_body)
    print(f"Wrote {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
