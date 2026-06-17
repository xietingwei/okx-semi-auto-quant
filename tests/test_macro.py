from qis.macro import MacroAnalyzer


def test_macro_return_calculation() -> None:
    assert round(MacroAnalyzer._ret([100, 110], 1), 6) == 0.1


def test_macro_squash_bounds() -> None:
    assert 0 < MacroAnalyzer._squash(0.1, 4) < 1
    assert -1 < MacroAnalyzer._squash(-0.1, 4) < 0
