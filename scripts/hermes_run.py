#!/usr/bin/env python3
"""Hermes Content Radar.

Scheduled GitHub Actions runner for collecting LinkedIn/note topic ideas.

Modes:
  - daily: daily tech / real-estate AI radar
  - jsai: JSAI and academic deep dive
  - weekly: weekly editorial planning from accumulated outputs

The script writes Markdown outputs under outputs/<mode>/YYYY-MM-DD.md and, when
GITHUB_TOKEN is available, creates a GitHub Issue for review.

Provider behavior:
  - Use OpenAI first when OPENAI_API_KEY is available.
  - Fall back to Gemini when OpenAI fails or OPENAI_API_KEY is absent and
    GEMINI_API_KEY is available.
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


def extract_openai_text(response: object) -> str:
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


def call_openai(prompt: str) -> tuple[str, str]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    client = OpenAI(api_key=api_key)

    first_error: Exception | None = None
    try:
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
        )
        return extract_openai_text(response), f"openai:{model}:web_search"
    except Exception as exc:
        first_error = exc
        print(f"OpenAI web_search attempt failed: {type(exc).__name__}: {exc}")

    try:
        response = client.responses.create(
            model=model,
            input=(
                prompt
                + "\n\n注意: OpenAI web_search_preview の呼び出しに失敗したため、利用可能な知識と与えられたソースヒントだけで出力してください。"
                + f"\nエラー概要: {type(first_error).__name__}: {first_error}"
            ),
        )
        return extract_openai_text(response), f"openai:{model}:no_web_search"
    except Exception as exc:
        raise RuntimeError(
            "OpenAI failed after web_search and no-web attempts: "
            f"first={type(first_error).__name__}: {first_error}; "
            f"second={type(exc).__name__}: {exc}"
        ) from exc


def extract_gemini_text(data: dict) -> str:
    candidates = data.get("candidates") or []
    chunks: list[str] = []
    for candidate in candidates:
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            text = part.get("text")
            if text:
                chunks.append(text)
    if chunks:
        return "\n".join(chunks)
    return str(data)


def call_gemini(prompt: str) -> tuple[str, str]:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    base_payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.4,
        },
    }

    # Try Gemini with Google Search grounding first. If unavailable for the
    # selected model/account, retry without the tool so the workflow still runs.
    for attempt_name, extra_payload in [
        ("google_search", {"tools": [{"google_search": {}}]}),
        (
            "no_google_search",
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": prompt
                                + "\n\n注意: Gemini Google Search grounding が利用できない可能性があるため、与えられたソースヒントと一般知識の範囲で出力してください。URL不明の情報は保留・要確認に回してください。"
                            }
                        ],
                    }
                ]
            },
        ),
    ]:
        payload = dict(base_payload)
        payload.update(extra_payload)
        response = requests.post(
            url,
            params={"key": api_key},
            json=payload,
            timeout=120,
        )
        if response.status_code < 300:
            return extract_gemini_text(response.json()), f"gemini:{model}:{attempt_name}"
        print(f"Gemini {attempt_name} attempt failed: {response.status_code} {response.text[:1000]}")

    raise RuntimeError("Gemini failed after google_search and no-search attempts")


def call_model(prompt: str) -> tuple[str, str]:
    errors: list[str] = []

    if os.environ.get("OPENAI_API_KEY"):
        try:
            return call_openai(prompt)
        except Exception as exc:
            errors.append(f"OpenAI: {type(exc).__name__}: {exc}")
            print(f"Falling back after OpenAI failure: {type(exc).__name__}: {exc}")

    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        try:
            return call_gemini(prompt)
        except Exception as exc:
            errors.append(f"Gemini: {type(exc).__name__}: {exc}")

    raise RuntimeError(
        "No usable model provider. Set OPENAI_API_KEY or GEMINI_API_KEY. "
        + " | ".join(errors)
    )


def write_output(mode: str, today: dt.date, body: str, model_label: str) -> Path:
    cfg = MODE_CONFIG[mode]
    out_dir = ROOT / cfg["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{today.isoformat()}.md"
    header = textwrap.dedent(
        f"""
        # {cfg['issue_prefix']} {today.isoformat()}

        - Generated at: {dt.datetime.now(JST).isoformat()}
        - Mode: `{mode}`
        - Model: `{model_label}`
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
    body, model_label = call_model(prompt)
    out_path = write_output(args.mode, today, body, model_label)

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
    print(f"Wrote {out_path.relative_to(ROOT)} with {model_label}")


if __name__ == "__main__":
    main()
