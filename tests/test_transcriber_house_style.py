def test_opencc_zhi_overconversion_fixed():
    # OpenCC s2twp 把「就是只要」轉成「就是隻要」——修正層要救回（修修 2026-07-26）
    from shared.transcriber import _to_traditional

    got = _to_traditional("就学会一件事 就是只要你一激烈运动")
    assert got == "就學會一件事 就是只要你一激烈運動"
    assert _to_traditional("它不是只是重新重塑") == "它不是只是重新重塑"
    # 量詞語境的 隻 不可誤傷
    assert _to_traditional("我养了一只猫") == "我養了一隻貓"
