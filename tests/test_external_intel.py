from qis.external_intel import ExternalIntelAnalyzer, Headline


def test_external_intel_scores_negative_headline() -> None:
    analyzer = ExternalIntelAnalyzer()
    score = analyzer._score([Headline("test", "Exchange hack triggers liquidation fears", "")])

    assert score < 0


def test_external_intel_scores_positive_headline() -> None:
    analyzer = ExternalIntelAnalyzer()
    score = analyzer._score([Headline("test", "ETF inflow rises on institutional adoption", "")])

    assert score > 0
