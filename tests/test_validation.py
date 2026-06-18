from qis.validation import similarity_probability


def test_walk_forward_validation_returns_calibrated_metrics() -> None:
    events = [
        ((float(index % 3), float(index % 2)), index % 3 != 0)
        for index in range(30)
    ]

    result = similarity_probability((1.0, 1.0), events, (1.0, 1.0))

    assert 0 <= result.raw_probability <= 1
    assert 0 <= result.calibrated_probability <= 1
    assert result.walk_forward_samples == 22
    assert result.brier_score is not None
    assert result.calibration_error is not None


def test_walk_forward_validation_marks_small_sample() -> None:
    result = similarity_probability((1.0,), [((1.0,), True)] * 5, (1.0,))

    assert result.walk_forward_samples == 0
    assert result.drift_status == "insufficient"
