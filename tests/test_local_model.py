"""不啟動外部程序，驗證模型啟動參數及程序所有權。"""

from unittest.mock import Mock

import pytest

from syrupbot import local_model
from syrupbot.config import Config


@pytest.fixture
def model_config(monkeypatch, tmp_path):
    exe = tmp_path / "llama-server.exe"
    exe.touch()
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-no-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://127.0.0.1:9932/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "qwen-chat")
    monkeypatch.setenv("LLAMA_SERVER_EXE", str(exe))
    monkeypatch.setenv("LLAMA_SERVER_ARGS", '["-c", "16384", "--jinja"]')
    return Config.from_env()


def test_waits_and_cleans_up_owned_server(monkeypatch, model_config):
    process = Mock()
    process.poll.return_value = None
    spawn = Mock(return_value=process)
    probe = Mock(side_effect=[None, False, True])
    monkeypatch.setattr(local_model.subprocess, "Popen", spawn)
    monkeypatch.setattr(local_model, "_server_ready", probe)
    monkeypatch.setattr(local_model.time, "sleep", Mock())

    with (
        pytest.raises(ValueError, match="bot stopped"),
        local_model.local_model(model_config),
    ):
        assert probe.call_count == 3
        process.terminate.assert_not_called()
        raise ValueError("bot stopped")

    args = spawn.call_args.args[0]
    assert args[1:] == [
        "-c", "16384", "--jinja", "--alias", "qwen-chat",
        "--host", "127.0.0.1", "--port", "9932",
    ]
    assert "DISCORD_TOKEN" not in spawn.call_args.kwargs["env"]
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)


def test_reuses_existing_server(monkeypatch, model_config):
    spawn = Mock()
    monkeypatch.setattr(local_model.subprocess, "Popen", spawn)
    monkeypatch.setattr(local_model, "_server_ready", Mock(return_value=True))

    with local_model.local_model(model_config):
        spawn.assert_not_called()

    spawn.assert_not_called()


def test_rejects_invalid_argument_list(monkeypatch, model_config):
    monkeypatch.setenv("LLAMA_SERVER_ARGS", '["-c", 16384]')
    with pytest.raises(RuntimeError, match="LLAMA_SERVER_ARGS"):
        Config.from_env()
