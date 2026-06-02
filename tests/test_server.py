from pathlib import Path

import pytest

import server


def test_resolve_cwd_defaults_to_workspace() -> None:
    assert server._resolve_cwd(None) == server.WORKSPACE_ROOT


def test_resolve_cwd_allows_relative_workspace_path(tmp_path: Path) -> None:
    nested = server.WORKSPACE_ROOT / "tests"
    assert server._resolve_cwd("tests") == nested.resolve()


def test_resolve_cwd_rejects_outside_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cwd must be inside workspace root"):
        server._resolve_cwd(str(tmp_path))


def test_build_opencode_args_minimal() -> None:
    assert server._build_opencode_args("review this") == ["opencode", "run", "review this"]


def test_build_opencode_args_with_options() -> None:
    assert server._build_opencode_args(
        "review this",
        executable="opencode.cmd",
        agent="reviewer",
        model="anthropic/claude-sonnet-4",
        attach_url="http://localhost:4096",
        session_id="ses_123",
        format_json=True,
    ) == [
        "opencode.cmd",
        "run",
        "--attach",
        "http://localhost:4096",
        "--session",
        "ses_123",
        "--model",
        "anthropic/claude-sonnet-4",
        "--agent",
        "reviewer",
        "--format",
        "json",
        "review this",
    ]


def test_parse_json_events_extracts_session_and_text() -> None:
    stdout = "\n".join(
        [
            '{"type":"step_start","sessionID":"ses_abc","part":{"type":"step-start"}}',
            '{"type":"text","sessionID":"ses_abc","part":{"type":"text","text":"Hello"}}',
            '{"type":"text","sessionID":"ses_abc","part":{"type":"text","text":" world"}}',
        ]
    )

    parsed = server._parse_json_events(stdout)

    assert parsed["session_id"] == "ses_abc"
    assert parsed["text"] == "Hello world"
    assert len(parsed["events"]) == 3


def test_state_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "state" / "work-sessions.json"
    monkeypatch.setattr(server, "STATE_DIR", state_file.parent)
    monkeypatch.setattr(server, "STATE_FILE", state_file)

    state = {
        "active_work_id": "review-ui",
        "works": {"review-ui": {"session_id": "ses_abc"}},
    }

    server._write_state(state)

    assert server._read_state() == state
