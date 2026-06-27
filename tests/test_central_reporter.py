import os
import re
import sys
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import central_reporter as cr


def test_summarize_report():
    repos = [
        {"status": None},
        {"status": {"conclusion": "success", "status": "completed"}},
        {"status": {"conclusion": "failure", "status": "completed"}},
        {"status": {"conclusion": None, "status": "in_progress"}},
    ]

    summary = cr.summarize_report(repos)

    assert summary["total_repos"] == 4
    assert summary["with_workflow"] == 3
    assert summary["successful"] == 1
    assert summary["failed"] == 1
    assert summary["in_progress"] == 1
    assert summary["no_workflow"] == 1
    assert summary["needs_attention"] == 1
    assert summary["health_score"] == 33


def test_repo_health_classifies_attention_and_no_workflow():
    failed_repo = {"status": {"status": "completed", "conclusion": "failure"}}
    no_workflow_repo = {"status": None}

    failed_health = cr.repo_health(failed_repo)
    no_workflow_health = cr.repo_health(no_workflow_repo)

    assert failed_health["label"] == "Needs attention"
    assert failed_health["needs_attention"] is True
    assert no_workflow_health["label"] == "No workflow"
    assert no_workflow_health["needs_attention"] is False


def test_gather_report_with_workflow_status(requests_mock):
    repo_data = [
        {
            "name": "repo1",
            "html_url": "https://github.com/testuser/repo1",
            "description": "Example repo",
            "stargazers_count": 7,
            "forks_count": 3,
            "private": False,
            "updated_at": "2026-06-21T00:00:00Z",
        }
    ]
    requests_mock.get("https://api.github.com/users/testuser/repos", json=repo_data)
    requests_mock.get(
        "https://api.github.com/repos/testuser/repo1/actions/runs",
        json={
            "workflow_runs": [
                {
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://github.com/testuser/repo1/actions/runs/1",
                    "name": "CI",
                    "run_started_at": "2026-06-21T00:00:00Z",
                    "updated_at": "2026-06-21T00:05:00Z",
                }
            ]
        },
    )

    report = cr.gather_report("testuser")

    assert len(report) == 1
    assert report[0]["name"] == "repo1"
    assert report[0]["status"]["conclusion"] == "success"
    assert report[0]["status"]["duration_seconds"] == 300


def test_create_issue_without_token_returns_none(monkeypatch):
    monkeypatch.setattr(cr, "TOKEN", None)
    result = cr.create_issue("owner", "repo", "title", "body", labels=["agent-report"])
    assert result is None


def test_issue_exists_detects_duplicate(requests_mock, monkeypatch):
    monkeypatch.setattr(cr, "TOKEN", "fake-token")
    requests_mock.get(
        re.compile(r"https://api\.github\.com/repos/owner/repo/issues.*"),
        json=[{"title": "[Agent Report] Workflow failure in repo"}],
    )
    assert cr.issue_exists("owner", "repo", "[Agent Report] Workflow failure in repo")


def test_render_dashboard_contains_repo_and_summary():
    repos = [
        {
            "name": "repo1",
            "description": "A sample repository",
            "stars": 1,
            "forks": 0,
            "status": {
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/testuser/repo1/actions/runs/1",
                "updated_at": "2026-06-21T00:05:00Z",
                "name": "CI",
            },
        }
    ]
    summary = {
        "total_repos": 1,
        "with_workflow": 1,
        "successful": 1,
        "failed": 0,
        "in_progress": 0,
        "no_workflow": 0,
        "needs_attention": 0,
        "health_score": 100,
    }

    html = cr.render_dashboard(repos, summary)

    assert "GitHub Agent Dashboard" in html
    assert "repo1" in html
    assert "Total repos" in html
    assert "Health score" in html


def test_render_report_includes_attention_section():
    repos = [
        {
            "name": "repo1",
            "description": "A sample repository",
            "stars": 1,
            "forks": 0,
            "status": {
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://github.com/testuser/repo1/actions/runs/1",
                "updated_at": "2026-06-21T00:05:00Z",
                "name": "CI",
            },
        }
    ]
    summary = cr.summarize_report(repos)

    html = cr.render_report(repos, summary)

    assert "Repositories that need attention" in html
    assert "repo1" in html
    assert "Needs attention" in html


def test_build_issue_body_includes_status():
    body = cr.build_issue_body("repo1", {"name": "CI", "status": "completed", "conclusion": "failure", "run_started_at": "2026-06-21T00:00:00Z", "updated_at": "2026-06-21T00:05:00Z", "duration_seconds": 300, "html_url": "https://example.com"})
    assert "Workflow name: CI" in body
    assert "Conclusion: failure" in body
    assert "View workflow run" in body
