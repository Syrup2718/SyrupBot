"""settings.toml 與環境變數合併的測試。"""

from __future__ import annotations

from pathlib import Path

import pytest

from syrupbot.config import DEFAULT_SETTINGS_PATH, Config, load_settings


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "settings.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_when_file_missing(tmp_path: Path) -> None:
    values, warnings = load_settings(tmp_path / "none.toml", env={})
    assert values["decision_strategy"] == "heuristic"
    assert values["reply_probability"] == 0.08
    assert values["allowed_channel_ids"] == frozenset()
    assert warnings == []


def test_file_values_are_typed(tmp_path: Path) -> None:
    path = _write(tmp_path, """
[reply]
strategy = "Hybrid"
probability = 0.5
cooldown_seconds = 3
[channels]
allowed_ids = [123456789012345678, "42"]
[log]
level = "debug"
""")
    values, _ = load_settings(path, env={})
    assert values["decision_strategy"] == "hybrid"
    assert values["reply_probability"] == 0.5
    assert values["reply_cooldown_seconds"] == 3.0
    assert values["allowed_channel_ids"] == frozenset({123456789012345678, 42})
    assert values["log_level"] == "DEBUG"


def test_env_overrides_file_and_warns(tmp_path: Path) -> None:
    path = _write(tmp_path, "[reply]\nprobability = 0.5\n")
    values, warnings = load_settings(
        path, env={"REPLY_PROBABILITY": "0.9", "TAG_MEMBERS": "false"}
    )
    assert values["reply_probability"] == 0.9
    assert values["tag_members"] is False
    assert len(warnings) == 1 and "REPLY_PROBABILITY" in warnings[0]


@pytest.mark.parametrize("text, message", [
    ("[reply]\nprobability = 1.5\n", "機率要介於 0 和 1"),
    ("[reply]\nstrategy = \"magic\"\n", "只能是"),
    ("[reply]\nprobabilty = 0.5\n", "不認得的設定"),
    ("[tag]\nenabled = \"yes\"\n", "true 或 false"),
    ("[lull]\nmin_seconds = 100\nmax_seconds = 10\n", "min_seconds"),
])
def test_invalid_values_fail_clearly(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_settings(_write(tmp_path, text), env={})


def test_from_env_reads_secrets_from_env_and_rest_from_file(tmp_path, monkeypatch) -> None:
    path = _write(tmp_path, "[persona]\nname = \"kokuto\"\n")
    for key in ("PERSONA", "REPLY_PROBABILITY", "DECISION_STRATEGY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    config = Config.from_env(path)
    assert isinstance(config, Config)
    assert config.persona == "kokuto"
    assert config.discord_token == "t"


def test_repo_settings_file_is_valid() -> None:
    values, _ = load_settings(DEFAULT_SETTINGS_PATH, env={})
    assert values["persona"]
