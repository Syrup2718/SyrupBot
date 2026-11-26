from syrupbot.persona import Persona


def _make_persona() -> Persona:
    return Persona.load("syrup")


def test_loads_syrup_persona() -> None:
    persona = _make_persona()
    assert persona.name == "漿糖"
    assert persona.particles  # 有定義語助詞
    assert persona.examples   # 有 few-shot 範例


def test_system_prompt_includes_key_pieces() -> None:
    prompt = _make_persona().system_prompt
    assert "漿糖" in prompt
    assert "156 公分" in prompt
    assert "橘色四角星髮夾" in prompt
    assert "\n。" not in prompt
    # 語助詞的「適量」指示要在 prompt 裡,避免每句都用。
    assert "不要每句都用" in prompt
    # few-shot 範例要被帶進去。
    assert "範例" in prompt
    assert "對方:漿糖你在嗎\n  你:不在 怎麼了?\n" in prompt
    assert "對方:你是不是討厭我\n  你:沒有呀 寶寶泥補藥亂想\n" in prompt


def test_mentioned_by_name_matches_aliases() -> None:
    persona = _make_persona()
    assert persona.mentioned_by_name("syrup 在嗎") is True
    assert persona.mentioned_by_name("糖糖你好") is True
    assert persona.mentioned_by_name("今天天氣真好") is False


def test_matches_interest() -> None:
    persona = _make_persona()
    assert persona.matches_interest("想吃蛋糕") is True
    assert persona.matches_interest("這張梗圖超好笑") is True
    assert persona.matches_interest("來討論季度財報") is False
