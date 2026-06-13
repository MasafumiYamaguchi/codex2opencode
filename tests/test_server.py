import json
from pathlib import Path
from typing import Any

import pytest

import server


def test_resolve_cwd_defaults_to_workspace() -> None:
    assert server._resolve_cwd(None) == server.WORKSPACE_ROOT


def test_resolve_cwd_allows_relative_workspace_path(tmp_path: Path) -> None:
    nested = server.WORKSPACE_ROOT / "tests"
    assert server._resolve_cwd("tests") == nested.resolve()


def test_resolve_cwd_allows_configured_external_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_repo = tmp_path / "external-repo"
    external_repo.mkdir()
    monkeypatch.setattr(server, "_allowed_roots", lambda: [server.WORKSPACE_ROOT, tmp_path])

    assert server._resolve_cwd(str(external_repo)) == external_repo.resolve()


def test_resolve_cwd_rejects_unallowed_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_repo = tmp_path / "external-repo"
    external_repo.mkdir()
    monkeypatch.setattr(server, "_allowed_roots", lambda: [server.WORKSPACE_ROOT])

    with pytest.raises(ValueError, match="cwd must be inside an allowed root"):
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


def _patch_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    state_file = tmp_path / "state" / "work-sessions.json"
    monkeypatch.setattr(server, "STATE_DIR", state_file.parent)
    monkeypatch.setattr(server, "STATE_FILE", state_file)
    return state_file.parent, state_file


def _fake_run_output(
    *, session_id: str | None, text: str = "", ok: bool = True
) -> dict[str, Any]:
    parsed: dict[str, Any] = {"events": []}
    if session_id is not None:
        parsed["session_id"] = session_id
    parsed["text"] = text
    return {
        "ok": ok,
        "cwd": str(server.WORKSPACE_ROOT),
        "args": ["opencode", "run", "--format", "json", "..."],
        "exit_code": 0 if ok else 1,
        "stdout": "",
        "stderr": "",
        "parsed": parsed,
    }


def test_work_start_rejects_duplicate_work_id_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_old",
                    "cwd": str(server.WORKSPACE_ROOT),
                }
            },
        }
    )

    called = {"run": False}
    monkeypatch.setattr(
        server, "_run_opencode", lambda *a, **kw: called.__setitem__("run", True) or {}
    )

    with pytest.raises(ValueError, match="work_id already exists"):
        server.opencode_work_start(work_id="fix-auth", prompt="hello")

    assert called["run"] is False


def test_work_start_resume_uses_existing_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_keep",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "agent": "reviewer",
                    "model": "anthropic/claude-sonnet-4",
                }
            },
        }
    )

    captured: dict[str, Any] = {}

    def fake_run(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured["prompt"] = prompt
        captured.update(kwargs)
        return _fake_run_output(session_id="ses_keep", text="continued")

    monkeypatch.setattr(server, "_run_opencode", fake_run)

    result = server.opencode_work_start(
        work_id="fix-auth",
        prompt="follow up",
        on_exists="resume",
    )

    assert result["ok"] is True
    assert result["resumed"] is True
    assert result["session_id"] == "ses_keep"
    assert result["text"] == "continued"
    assert captured["prompt"] == "follow up"
    assert captured["session_id"] == "ses_keep"
    assert captured["agent"] == "reviewer"
    assert captured["model"] == "anthropic/claude-sonnet-4"
    assert captured["format_json"] is True

    state = server._read_state()
    assert state["active_work_id"] == "fix-auth"
    assert state["works"]["fix-auth"]["session_id"] == "ses_keep"


def test_work_start_replace_starts_fresh_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_old",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "agent": "reviewer",
                }
            },
        }
    )

    def fake_run(prompt: str, **kwargs: Any) -> dict[str, Any]:
        assert kwargs.get("session_id") is None
        return _fake_run_output(session_id="ses_new", text="fresh")

    monkeypatch.setattr(server, "_run_opencode", fake_run)

    result = server.opencode_work_start(
        work_id="fix-auth",
        prompt="start over",
        agent="implementer",
        model="anthropic/claude-sonnet-4",
        on_exists="replace",
    )

    assert result["ok"] is True
    assert result["replaced"] is True
    assert result["session_id"] == "ses_new"

    state = server._read_state()
    assert state["active_work_id"] == "fix-auth"
    assert state["works"]["fix-auth"]["session_id"] == "ses_new"
    assert state["works"]["fix-auth"]["agent"] == "implementer"


def test_work_start_resume_falls_through_when_work_id_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state({"active_work_id": None, "works": {}})

    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: _fake_run_output(session_id="ses_first", text="hi"),
    )

    result = server.opencode_work_start(
        work_id="new",
        prompt="hello",
        on_exists="resume",
    )

    assert result["ok"] is True
    assert "resumed" not in result
    assert result["session_id"] == "ses_first"


def test_work_start_replace_falls_through_when_work_id_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state({"active_work_id": None, "works": {}})

    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: _fake_run_output(session_id="ses_first", text="hi"),
    )

    result = server.opencode_work_start(
        work_id="new",
        prompt="hello",
        on_exists="replace",
    )

    assert result["ok"] is True
    assert "replaced" not in result
    assert result["session_id"] == "ses_first"


def test_work_start_replace_keeps_old_state_when_opencode_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_old",
                    "cwd": str(server.WORKSPACE_ROOT),
                }
            },
        }
    )

    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: _fake_run_output(session_id=None, ok=False),
    )

    result = server.opencode_work_start(
        work_id="fix-auth",
        prompt="start over",
        on_exists="replace",
    )

    assert result["ok"] is False
    assert "replaced" not in result

    state = server._read_state()
    assert state["works"]["fix-auth"]["session_id"] == "ses_old"
    assert state["active_work_id"] == "fix-auth"


def test_work_start_rejects_invalid_on_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state({"active_work_id": None, "works": {}})

    called = {"run": False}
    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda *a, **kw: called.__setitem__("run", True) or {},
    )

    with pytest.raises(ValueError, match="on_exists must be one of"):
        server.opencode_work_start(
            work_id="new",
            prompt="hello",
            on_exists="bogus",
        )

    assert called["run"] is False


# ---------------------------------------------------------------------------
# Highest priority items 2/3/4: summaries, default cwd, invocation logs
# ---------------------------------------------------------------------------


def _patch_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    log_file = tmp_path / "state" / "invocations.jsonl"
    monkeypatch.setattr(server, "STATE_DIR", log_file.parent)
    monkeypatch.setattr(server, "STATE_FILE", log_file.parent / "work-sessions.json")
    monkeypatch.setattr(server, "INVOCATIONS_LOG", log_file)
    return log_file


def test_default_cwd_uses_workspace_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(server.DEFAULT_CWD_ENV, raising=False)
    assert server._default_cwd() == server.WORKSPACE_ROOT


def test_default_cwd_honors_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(server.DEFAULT_CWD_ENV, str(tmp_path))
    assert server._default_cwd() == tmp_path


def test_default_cwd_ignores_blank_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(server.DEFAULT_CWD_ENV, "   ")
    assert server._default_cwd() == server.WORKSPACE_ROOT


def test_resolve_cwd_falls_back_to_default_cwd_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(server.DEFAULT_CWD_ENV, str(tmp_path))
    monkeypatch.setattr(server, "_allowed_roots", lambda: [server.WORKSPACE_ROOT, tmp_path])

    assert server._resolve_cwd(None) == tmp_path.resolve()


def test_log_prompts_enabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(server.LOG_PROMPTS_ENV, raising=False)
    assert server._log_prompts_enabled() is True


@pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no", "off", ""])
def test_log_prompts_disabled_by_env(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(server.LOG_PROMPTS_ENV, value)
    assert server._log_prompts_enabled() is False


def test_append_invocation_log_writes_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = _patch_log(tmp_path, monkeypatch)

    server._append_invocation_log(
        {
            "timestamp": "2026-06-14T10:00:00Z",
            "tool": "opencode_ask",
            "prompt": "hello",
            "text": "world",
        }
    )

    line = log_file.read_text(encoding="utf-8").strip()
    assert json.loads(line) == {
        "timestamp": "2026-06-14T10:00:00Z",
        "tool": "opencode_ask",
        "prompt": "hello",
        "text": "world",
    }


def test_append_invocation_log_redacts_prompt_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = _patch_log(tmp_path, monkeypatch)
    monkeypatch.setenv(server.LOG_PROMPTS_ENV, "false")

    server._append_invocation_log(
        {
            "timestamp": "2026-06-14T10:00:00Z",
            "tool": "opencode_ask",
            "prompt": "secret",
            "text": "secret reply",
            "work_id": None,
        }
    )

    line = log_file.read_text(encoding="utf-8").strip()
    entry = json.loads(line)
    assert "prompt" not in entry
    assert "text" not in entry
    assert entry["tool"] == "opencode_ask"


def test_work_start_records_timestamps_and_turn_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state({"active_work_id": None, "works": {}})

    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: _fake_run_output(session_id="ses_abc", text="hi"),
    )

    server.opencode_work_start(work_id="review-ui", prompt="first")

    work = server._read_state()["works"]["review-ui"]
    assert work["session_id"] == "ses_abc"
    assert work["turn_count"] == 1
    assert work["created_at"] == work["last_used_at"]
    assert work["created_at"].endswith("Z")


def test_work_start_resume_updates_last_used_and_turn_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_keep",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "created_at": "2026-06-13T10:00:00Z",
                    "last_used_at": "2026-06-13T10:00:00Z",
                    "turn_count": 3,
                }
            },
        }
    )

    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: _fake_run_output(session_id="ses_keep", text="ok"),
    )

    server.opencode_work_start(
        work_id="fix-auth",
        prompt="follow up",
        on_exists="resume",
    )

    work = server._read_state()["works"]["fix-auth"]
    assert work["turn_count"] == 4
    assert work["last_used_at"] > "2026-06-13T10:00:00Z"
    assert work["created_at"] == "2026-06-13T10:00:00Z"


def test_work_ask_updates_last_used_and_turn_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_keep",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "created_at": "2026-06-13T10:00:00Z",
                    "last_used_at": "2026-06-13T10:00:00Z",
                    "turn_count": 1,
                }
            },
        }
    )

    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: _fake_run_output(session_id="ses_keep", text="ok"),
    )

    server.opencode_work_ask(prompt="next question")

    work = server._read_state()["works"]["fix-auth"]
    assert work["turn_count"] == 2
    assert work["last_used_at"] > "2026-06-13T10:00:00Z"
    assert work["created_at"] == "2026-06-13T10:00:00Z"


def test_work_list_returns_summaries_sorted_by_recency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "review-ui",
            "works": {
                "fix-auth": {
                    "session_id": "ses_a",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "created_at": "2026-06-12T10:00:00Z",
                    "last_used_at": "2026-06-12T11:00:00Z",
                    "turn_count": 2,
                },
                "review-ui": {
                    "session_id": "ses_b",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "agent": "reviewer",
                    "model": "anthropic/claude-sonnet-4",
                    "created_at": "2026-06-14T10:00:00Z",
                    "last_used_at": "2026-06-14T11:30:00Z",
                    "turn_count": 5,
                },
                "design-flow": {
                    "session_id": "ses_c",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "created_at": "2026-06-13T10:00:00Z",
                    "last_used_at": "2026-06-13T12:00:00Z",
                    "turn_count": 1,
                },
            },
        }
    )

    result = server.opencode_work_list()

    assert result["active_work_id"] == "review-ui"
    assert result["count"] == 3
    assert set(result["works"]) == {"fix-auth", "review-ui", "design-flow"}
    assert [s["work_id"] for s in result["summaries"]] == [
        "review-ui",
        "design-flow",
        "fix-auth",
    ]
    top = result["summaries"][0]
    assert top["turn_count"] == 5
    assert top["agent"] == "reviewer"
    assert top["last_used_at"] == "2026-06-14T11:30:00Z"


def test_work_list_handles_legacy_entries_without_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": None,
            "works": {
                "old": {
                    "session_id": "ses_old",
                    "cwd": str(server.WORKSPACE_ROOT),
                }
            },
        }
    )

    result = server.opencode_work_list()

    assert result["count"] == 1
    summary = result["summaries"][0]
    assert summary["work_id"] == "old"
    assert summary["turn_count"] == 0
    assert summary["created_at"] is None
    assert summary["last_used_at"] is None


def test_opencode_ask_writes_invocation_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = _patch_log(tmp_path, monkeypatch)
    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: {
            **_fake_run_output(session_id=None, text="", ok=True),
            "stdout": "plain reply",
        },
    )

    result = server.opencode_ask(prompt="hi there", cwd=str(server.WORKSPACE_ROOT))

    lines = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    entry = lines[0]
    assert entry["tool"] == "opencode_ask"
    assert entry["prompt"] == "hi there"
    assert entry["text"] == "plain reply"
    assert entry["ok"] is True
    assert entry["cwd"] == str(server.WORKSPACE_ROOT)
    assert result["text"] == "plain reply"


def test_work_start_writes_invocation_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = _patch_log(tmp_path, monkeypatch)
    _patch_state(tmp_path, monkeypatch)
    server._write_state({"active_work_id": None, "works": {}})

    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: _fake_run_output(session_id="ses_log", text="hello"),
    )

    server.opencode_work_start(work_id="log-it", prompt="hi")

    lines = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    assert lines[0]["tool"] == "opencode_work_start"
    assert lines[0]["work_id"] == "log-it"
    assert lines[0]["session_id"] == "ses_log"


def test_work_ask_writes_invocation_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = _patch_log(tmp_path, monkeypatch)
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_keep",
                    "cwd": str(server.WORKSPACE_ROOT),
                }
            },
        }
    )

    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: _fake_run_output(session_id="ses_keep", text="ok"),
    )

    server.opencode_work_ask(prompt="next")

    lines = [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    assert lines[0]["tool"] == "opencode_work_ask"
    assert lines[0]["work_id"] == "fix-auth"
    assert lines[0]["session_id"] == "ses_keep"


def test_invocation_log_omits_prompt_for_work_ask_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = _patch_log(tmp_path, monkeypatch)
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_keep",
                    "cwd": str(server.WORKSPACE_ROOT),
                }
            },
        }
    )
    monkeypatch.setenv(server.LOG_PROMPTS_ENV, "false")

    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: _fake_run_output(session_id="ses_keep", text="ok"),
    )

    server.opencode_work_ask(prompt="private question")

    entry = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert "prompt" not in entry
    assert "text" not in entry
    assert entry["tool"] == "opencode_work_ask"
    assert entry["work_id"] == "fix-auth"


# ---------------------------------------------------------------------------
# High value items 5/6/7/8: prompt modes, compact results, cleanup, overrides
# ---------------------------------------------------------------------------


def test_apply_mode_prefix_returns_prompt_unchanged_for_none() -> None:
    assert server._apply_mode_prefix("hello", "none") == "hello"


def test_apply_mode_prefix_prepends_role_text() -> None:
    out = server._apply_mode_prefix("hello", "review")
    assert out.startswith(server.PROMPT_MODE_PREFIXES["review"])
    assert out.endswith("hello")


def test_validate_mode_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode must be one of"):
        server._validate_mode("bogus")


@pytest.mark.parametrize(
    "mode", ["review", "debug", "design", "skeptic", "test-plan"]
)
def test_apply_mode_prefix_known_modes(mode: str) -> None:
    out = server._apply_mode_prefix("hi", mode)
    assert out.startswith(server.PROMPT_MODE_PREFIXES[mode])
    assert out.endswith("hi")


@pytest.mark.parametrize(
    "mode", ["review", "debug", "design", "skeptic", "test-plan"]
)
def test_opencode_ask_applies_mode_prefix(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured["prompt"] = prompt
        return _fake_run_output(session_id=None, text="ok")

    monkeypatch.setattr(server, "_run_opencode", fake_run)

    result = server.opencode_ask(prompt="actual question", mode=mode)

    assert result["ok"] is True
    assert captured["prompt"] == server.PROMPT_MODE_PREFIXES[mode] + "actual question"


def test_opencode_ask_rejects_unknown_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_log(tmp_path, monkeypatch)
    called = {"run": False}
    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda *a, **kw: called.__setitem__("run", True) or {},
    )

    with pytest.raises(ValueError, match="mode must be one of"):
        server.opencode_ask(prompt="hi", mode="bogus")

    assert called["run"] is False


def test_opencode_ask_records_mode_in_invocation_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = _patch_log(tmp_path, monkeypatch)
    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: _fake_run_output(session_id=None, text="ok"),
    )

    server.opencode_ask(prompt="hi", mode="review")

    entry = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert entry["mode"] == "review"


def test_opencode_ask_compact_returns_slim_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: {
            **_fake_run_output(session_id="ses_x", text="hello"),
            "stdout": "noisy stdout",
            "stderr": "noisy stderr",
            "args": ["opencode", "run", "--format", "json", prompt],
        },
    )

    result = server.opencode_ask(prompt="hi", compact=True)

    assert set(result.keys()) <= {
        "ok",
        "work_id",
        "session_id",
        "cwd",
        "text",
        "resumed",
        "replaced",
        "error",
    }
    assert result["text"] == "hello"
    assert "stdout" not in result
    assert "stderr" not in result
    assert "args" not in result
    assert "parsed" not in result


def test_opencode_ask_full_response_keeps_verbose_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: {
            **_fake_run_output(session_id=None, text="hi"),
            "stdout": "raw",
        },
    )

    result = server.opencode_ask(prompt="hi")

    assert result["stdout"] == "raw"
    assert result["stderr"] == ""


def test_work_start_compact_strips_verbose_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state({"active_work_id": None, "works": {}})

    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: {
            **_fake_run_output(session_id="ses_c", text="hello"),
            "stdout": "noisy",
            "args": ["opencode", "run"],
        },
    )

    result = server.opencode_work_start(
        work_id="compact-1", prompt="hi", compact=True
    )

    assert result["ok"] is True
    assert result["text"] == "hello"
    assert result["work_id"] == "compact-1"
    assert result["session_id"] == "ses_c"
    assert "stdout" not in result
    assert "args" not in result
    assert "parsed" not in result


def test_work_start_records_mode_in_state_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = _patch_log(tmp_path, monkeypatch)
    _patch_state(tmp_path, monkeypatch)
    server._write_state({"active_work_id": None, "works": {}})

    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: _fake_run_output(session_id="ses_m", text="ok"),
    )

    server.opencode_work_start(work_id="mode-it", prompt="hi", mode="design")

    entry = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert entry["mode"] == "design"


def test_work_ask_applies_mode_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_keep",
                    "cwd": str(server.WORKSPACE_ROOT),
                }
            },
        }
    )

    captured: dict[str, Any] = {}

    def fake_run(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured["prompt"] = prompt
        return _fake_run_output(session_id="ses_keep", text="ok")

    monkeypatch.setattr(server, "_run_opencode", fake_run)

    result = server.opencode_work_ask(prompt="focus", mode="debug")

    assert result["ok"] is True
    assert captured["prompt"] == server.PROMPT_MODE_PREFIXES["debug"] + "focus"


def test_work_ask_compact_strips_verbose_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_keep",
                    "cwd": str(server.WORKSPACE_ROOT),
                }
            },
        }
    )

    monkeypatch.setattr(
        server,
        "_run_opencode",
        lambda prompt, **kw: {
            **_fake_run_output(session_id="ses_keep", text="ok"),
            "stdout": "raw",
            "args": ["opencode", "run"],
        },
    )

    result = server.opencode_work_ask(prompt="next", compact=True)

    assert result["text"] == "ok"
    assert "stdout" not in result
    assert "args" not in result


# Item 8: per-call agent/model override on follow-up


def test_work_ask_uses_stored_agent_and_model_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_keep",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "agent": "reviewer",
                    "model": "anthropic/claude-sonnet-4",
                }
            },
        }
    )

    captured: dict[str, Any] = {}

    def fake_run(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _fake_run_output(session_id="ses_keep", text="ok")

    monkeypatch.setattr(server, "_run_opencode", fake_run)

    server.opencode_work_ask(prompt="next")

    assert captured["agent"] == "reviewer"
    assert captured["model"] == "anthropic/claude-sonnet-4"


def test_work_ask_agent_override_does_not_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_keep",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "agent": "reviewer",
                }
            },
        }
    )

    captured: dict[str, Any] = {}

    def fake_run(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _fake_run_output(session_id="ses_keep", text="ok")

    monkeypatch.setattr(server, "_run_opencode", fake_run)

    result = server.opencode_work_ask(prompt="next", agent="implementer")

    assert captured["agent"] == "implementer"
    assert "model_override" not in result
    assert result["agent_override"] == "implementer"

    state = server._read_state()
    assert state["works"]["fix-auth"]["agent"] == "reviewer"


def test_work_ask_model_override_does_not_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_keep",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "model": "anthropic/claude-sonnet-4",
                }
            },
        }
    )

    captured: dict[str, Any] = {}

    def fake_run(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _fake_run_output(session_id="ses_keep", text="ok")

    monkeypatch.setattr(server, "_run_opencode", fake_run)

    result = server.opencode_work_ask(
        prompt="next", model="openai/gpt-5"
    )

    assert captured["model"] == "openai/gpt-5"
    assert result["model_override"] == "openai/gpt-5"
    assert "agent_override" not in result

    state = server._read_state()
    assert state["works"]["fix-auth"]["model"] == "anthropic/claude-sonnet-4"


def test_work_ask_blank_override_falls_back_to_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fix-auth",
            "works": {
                "fix-auth": {
                    "session_id": "ses_keep",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "agent": "reviewer",
                }
            },
        }
    )

    captured: dict[str, Any] = {}

    def fake_run(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _fake_run_output(session_id="ses_keep", text="ok")

    monkeypatch.setattr(server, "_run_opencode", fake_run)

    server.opencode_work_ask(prompt="next", agent="   ")

    assert captured["agent"] == "reviewer"


# Item 7: cleanup


def test_parse_iso_timestamp_handles_z_suffix() -> None:
    parsed = server._parse_iso_timestamp("2026-01-02T03:04:05Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.year == 2026 and parsed.second == 5


def test_parse_iso_timestamp_returns_none_for_invalid() -> None:
    assert server._parse_iso_timestamp("not a date") is None
    assert server._parse_iso_timestamp(None) is None
    assert server._parse_iso_timestamp("") is None
    assert server._parse_iso_timestamp("   ") is None


def test_work_cleanup_rejects_negative_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state({"active_work_id": None, "works": {}})

    with pytest.raises(ValueError, match="older_than_seconds must be >= 0"):
        server.opencode_work_cleanup(older_than_seconds=-1)


def test_work_cleanup_removes_stale_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "fresh",
            "works": {
                "stale": {
                    "session_id": "ses_s",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "created_at": "2020-01-01T00:00:00Z",
                    "last_used_at": "2020-01-01T00:00:00Z",
                    "turn_count": 1,
                },
                "fresh": {
                    "session_id": "ses_f",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "created_at": "2026-06-14T10:00:00Z",
                    "last_used_at": "2026-06-14T10:00:00Z",
                    "turn_count": 1,
                },
            },
        }
    )

    result = server.opencode_work_cleanup(older_than_seconds=60)

    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["mark_only"] is False
    assert result["count"] == 1
    assert result["removed"] == ["stale"]
    assert result["stale"] == ["stale"]
    assert "fresh" not in result["removed"]

    state = server._read_state()
    assert "stale" not in state["works"]
    assert "fresh" in state["works"]


def test_work_cleanup_skips_active_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "stale-active",
            "works": {
                "stale-active": {
                    "session_id": "ses_a",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "last_used_at": "2020-01-01T00:00:00Z",
                    "turn_count": 1,
                },
            },
        }
    )

    result = server.opencode_work_cleanup(older_than_seconds=60)

    assert result["count"] == 0
    assert result["removed"] == []
    state = server._read_state()
    assert "stale-active" in state["works"]
    assert state["active_work_id"] == "stale-active"


def test_work_cleanup_can_include_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": "stale-active",
            "works": {
                "stale-active": {
                    "session_id": "ses_a",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "last_used_at": "2020-01-01T00:00:00Z",
                    "turn_count": 1,
                },
            },
        }
    )

    result = server.opencode_work_cleanup(older_than_seconds=60, include_active=True)

    assert result["removed"] == ["stale-active"]
    state = server._read_state()
    assert "stale-active" not in state["works"]
    assert state["active_work_id"] is None


def test_work_cleanup_dry_run_does_not_modify_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": None,
            "works": {
                "stale": {
                    "session_id": "ses_s",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "last_used_at": "2020-01-01T00:00:00Z",
                },
            },
        }
    )

    result = server.opencode_work_cleanup(older_than_seconds=60, dry_run=True)

    assert result["dry_run"] is True
    assert result["removed"] == ["stale"]
    assert result["threshold"].endswith("Z")
    state = server._read_state()
    assert "stale" in state["works"]


def test_work_cleanup_mark_only_flags_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": None,
            "works": {
                "stale": {
                    "session_id": "ses_s",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "last_used_at": "2020-01-01T00:00:00Z",
                },
            },
        }
    )

    result = server.opencode_work_cleanup(
        older_than_seconds=60, mark_only=True
    )

    assert result["mark_only"] is True
    assert result["marked"] == ["stale"]
    assert result["removed"] == []

    state = server._read_state()
    work = state["works"]["stale"]
    assert work["stale"] is True
    assert "stale_marked_at" in work


def test_work_cleanup_treats_legacy_entries_as_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": None,
            "works": {
                "old": {
                    "session_id": "ses_old",
                    "cwd": str(server.WORKSPACE_ROOT),
                }
            },
        }
    )

    result = server.opencode_work_cleanup(older_than_seconds=0)

    assert result["removed"] == ["old"]
    state = server._read_state()
    assert "old" not in state["works"]


def test_work_list_summaries_include_stale_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_state(tmp_path, monkeypatch)
    server._write_state(
        {
            "active_work_id": None,
            "works": {
                "fresh": {
                    "session_id": "ses_f",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "last_used_at": "2026-06-14T10:00:00Z",
                },
                "stale": {
                    "session_id": "ses_s",
                    "cwd": str(server.WORKSPACE_ROOT),
                    "last_used_at": "2026-06-10T10:00:00Z",
                    "stale": True,
                    "stale_marked_at": "2026-06-14T09:00:00Z",
                },
            },
        }
    )

    result = server.opencode_work_list()

    by_id = {s["work_id"]: s for s in result["summaries"]}
    assert by_id["fresh"]["stale"] is False
    assert by_id["stale"]["stale"] is True


def test_compact_result_keeps_mode_flags() -> None:
    full = {
        "ok": True,
        "work_id": "w",
        "session_id": "ses",
        "cwd": "/r",
        "text": "t",
        "resumed": True,
        "stdout": "raw",
        "stderr": "raw",
        "args": ["x"],
    }
    out = server._compact_result(full)
    assert out == {
        "ok": True,
        "work_id": "w",
        "session_id": "ses",
        "cwd": "/r",
        "text": "t",
        "resumed": True,
    }


def test_compact_result_keeps_overrides() -> None:
    full = {
        "ok": True,
        "text": "t",
        "agent_override": "implementer",
        "model_override": "openai/gpt-5",
    }
    out = server._compact_result(full)
    assert out == full
