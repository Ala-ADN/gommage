import os

from recorder.env_loader import load_local_env


def test_load_local_env_maps_jira_aliases(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JIRA_API_USER", raising=False)
    monkeypatch.delenv("JIRA_CLOUD_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_SITE", raising=False)

    (tmp_path / ".env").write_text("OPENAI_MODEL=test-model\n", encoding="utf-8")
    (tmp_path / ".jira.env").write_text(
        "JIRA_EMAIL=person@example.com\nJIRA_SITE=https://example.atlassian.net\n",
        encoding="utf-8",
    )

    load_local_env(tmp_path)

    assert os.environ["OPENAI_MODEL"] == "test-model"
    assert os.environ["JIRA_API_USER"] == "person@example.com"
    assert os.environ["JIRA_CLOUD_URL"] == "https://example.atlassian.net"


def test_load_local_env_does_not_override_existing_values(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JIRA_API_USER", "existing@example.com")
    (tmp_path / ".jira.env").write_text("JIRA_EMAIL=file@example.com\n", encoding="utf-8")

    load_local_env(tmp_path)

    assert os.environ["JIRA_API_USER"] == "existing@example.com"
