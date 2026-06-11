"""頻道政策測試:允許清單語意與 JSON 持久化。"""

from __future__ import annotations

from syrupbot.channels import ChannelPolicy


def test_empty_means_all_allowed() -> None:
    policy = ChannelPolicy()
    assert policy.is_allowed(1) is True
    assert policy.is_allowed(999) is True
    assert policy.restricted is False


def test_initial_restricts() -> None:
    policy = ChannelPolicy(initial=frozenset({100}))
    assert policy.is_allowed(100) is True
    assert policy.is_allowed(200) is False
    assert policy.restricted is True


def test_allow_and_disallow() -> None:
    policy = ChannelPolicy()
    policy.allow(5)
    assert policy.is_allowed(5) is True
    assert policy.is_allowed(6) is False  # 一旦有清單,其他頻道就被排除

    policy.disallow(5)
    # 清單空了 → 回到「所有頻道都可以」
    assert policy.is_allowed(6) is True
    assert policy.restricted is False


def test_only_sets_single_channel() -> None:
    policy = ChannelPolicy(initial=frozenset({1, 2, 3}))
    policy.only(2)
    assert policy.allowed_ids() == frozenset({2})
    assert policy.is_allowed(2) is True
    assert policy.is_allowed(1) is False


def test_clear_resets_to_all() -> None:
    policy = ChannelPolicy(initial=frozenset({1, 2}))
    policy.clear()
    assert policy.restricted is False
    assert policy.is_allowed(99) is True


def test_persists_across_instances(tmp_path) -> None:
    path = tmp_path / "channels.json"
    first = ChannelPolicy(path=path)
    first.allow(42)
    first.allow(43)

    # 用同一個檔重建 → 應載回剛剛的設定。
    second = ChannelPolicy(path=path)
    assert second.allowed_ids() == frozenset({42, 43})
    assert second.is_allowed(42) is True
    assert second.is_allowed(1) is False


def test_saved_file_overrides_initial(tmp_path) -> None:
    path = tmp_path / "channels.json"
    ChannelPolicy(path=path).only(7)  # 寫檔:只允許 7

    # 即使初始值給別的,有存檔就以存檔為準。
    reloaded = ChannelPolicy(initial=frozenset({1, 2}), path=path)
    assert reloaded.allowed_ids() == frozenset({7})
