#!/usr/bin/env python3
"""Ingest lightweight feedback from Hermes GitHub Issue comments.

Expected comment examples:

  案1 採用。PDF行政資料ネタは強い。
  案2 ボツ。AI一般論すぎる。
  案3 保留。JSAI寄りでnote向き。

The script appends parsed feedback to memory/post_feedback.csv.
It is intentionally simple and human-readable. It does not require labels.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
JST = dt.timezone(dt.timedelta(hours=9), name="JST")
FEEDBACK_PATH = ROOT / "memory" / "post_feedback.csv"


def github_get(path: str, params: dict | None = None) -> dict | list:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPOSITORY are required")

    url = f"https://api.github.com{path}"
    res = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        params=params,
        timeout=30,
    )
    if res.status_code >= 300:
        raise RuntimeError(f"GitHub GET failed: {res.status_code} {res.text}")
    return res.json()


def search_hermes_issues(days: int = 30) -> list[dict]:
    repo = os.environ["GITHUB_REPOSITORY"]
    since = (dt.datetime.now(JST).date() - dt.timedelta(days=days)).isoformat()
    q = f'repo:{repo} is:issue in:title Hermes updated:>={since}'
    data = github_get("/search/issues", {"q": q, "per_page": 50})
    return data.get("items", []) if isinstance(data, dict) else []


def issue_comments(issue_number: int) -> list[dict]:
    repo = os.environ["GITHUB_REPOSITORY"]
    data = github_get(f"/repos/{repo}/issues/{issue_number}/comments", {"per_page": 100})
    return data if isinstance(data, list) else []


def classify_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped:
        return None

    bad_markers = ["ボツ", "不採用", "bad", "不要", "弱い"]
    good_markers = ["採用", "good", "強い", "使える"]
    hold_markers = ["保留", "hold", "要確認", "note向き"]

    lowered = stripped.lower()
    if any(m.lower() in lowered for m in bad_markers):
        result = "bad"
    elif any(m.lower() in lowered for m in good_markers):
        result = "good"
    elif any(m.lower() in lowered for m in hold_markers):
        result = "hold"
    else:
        return None

    if "linkedin" in lowered:
        platform = "LinkedIn"
    elif "note" in lowered:
        platform = "note"
    else:
        platform = "unspecified"
    return result, platform


def read_existing_rows() -> list[dict]:
    if not FEEDBACK_PATH.exists():
        return []
    with FEEDBACK_PATH.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def append_feedback() -> int:
    existing = read_existing_rows()
    existing_keys = {
        (row.get("date", ""), row.get("topic", ""), row.get("feedback", ""))
        for row in existing
    }
    rows_to_add: list[dict] = []
    today = dt.datetime.now(JST).date().isoformat()

    for issue in search_hermes_issues():
        issue_number = issue["number"]
        issue_title = issue.get("title", f"issue-{issue_number}")
        for comment in issue_comments(issue_number):
            comment_body = comment.get("body", "")
            comment_id = comment.get("id")
            for line in comment_body.splitlines():
                classified = classify_line(line)
                if not classified:
                    continue
                result, platform = classified
                topic = f"#{issue_number} {issue_title}"
                feedback = f"comment:{comment_id} | {line.strip()}"
                key = (today, topic, feedback)
                if key in existing_keys:
                    continue
                rows_to_add.append(
                    {
                        "date": today,
                        "topic": topic,
                        "platform": platform,
                        "result": result,
                        "feedback": feedback,
                    }
                )
                existing_keys.add(key)

    if not rows_to_add:
        return 0

    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = FEEDBACK_PATH.exists()
    with FEEDBACK_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date", "topic", "platform", "result", "feedback"],
        )
        if not file_exists or FEEDBACK_PATH.stat().st_size == 0:
            writer.writeheader()
        writer.writerows(rows_to_add)
    return len(rows_to_add)


def main() -> None:
    added = append_feedback()
    print(f"Added {added} feedback rows to {FEEDBACK_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
