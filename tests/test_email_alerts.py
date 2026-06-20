from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from qis.email_alerts import notify_opportunities


def _settings(**overrides):
    values = {
        "email_alert_enabled": True,
        "email_alert_recipients": ("xietingwei.731@gmail.com",),
        "email_alert_score_threshold": 70,
        "email_alert_cooldown_hours": 12,
        "email_smtp_host": "smtp.gmail.com",
        "email_smtp_port": 465,
        "email_smtp_username": "sender@gmail.com",
        "email_smtp_password": "app-password",
        "email_smtp_from": "",
        "email_smtp_use_ssl": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _forecasts(score=72):
    return [{
        "inst_id": "BTC-USDT",
        "symbol": "BTC",
        "current_price": 65000,
        "strategy_variants": [{
            "strategy": {"id": "trend", "name": "趋势跟随"},
            "opportunity_score": score,
            "decision": "模拟观察",
            "strategy_validation": "冷启动待验证",
            "forecasts": [
                {"key": "1w", "expected_return": 0.03, "up_probability": 0.62},
                {"key": "1m", "expected_return": 0.08, "up_probability": 0.68},
                {"key": "3m", "expected_return": 0.15, "up_probability": 0.71},
            ],
        }],
    }]


def test_email_alert_sends_candidate_and_respects_cooldown(tmp_path: Path) -> None:
    messages = []
    state = tmp_path / "email-state.json"
    now = datetime(2026, 6, 20, 6, tzinfo=timezone.utc)

    first = notify_opportunities(
        _forecasts(),
        _settings(),
        state,
        now=now,
        sender=messages.append,
    )
    repeated = notify_opportunities(
        _forecasts(),
        _settings(),
        state,
        now=now + timedelta(hours=1),
        sender=messages.append,
    )

    assert first == 1
    assert repeated == 0
    assert len(messages) == 1
    assert messages[0]["To"] == "xietingwei.731@gmail.com"
    assert "BTC" in messages[0].get_content()
    assert "机会分：72" in messages[0].get_content()


def test_email_alert_rearms_after_score_falls_below_threshold(tmp_path: Path) -> None:
    messages = []
    state = tmp_path / "email-state.json"
    now = datetime(2026, 6, 20, 6, tzinfo=timezone.utc)
    settings = _settings()

    notify_opportunities(_forecasts(), settings, state, now=now, sender=messages.append)
    notify_opportunities(
        _forecasts(69),
        settings,
        state,
        now=now + timedelta(hours=1),
        sender=messages.append,
    )
    notified = notify_opportunities(
        _forecasts(71),
        settings,
        state,
        now=now + timedelta(hours=2),
        sender=messages.append,
    )

    assert notified == 1
    assert len(messages) == 2


def test_email_alert_requires_smtp_credentials(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="QIS_EMAIL_SMTP_PASSWORD"):
        notify_opportunities(
            _forecasts(),
            _settings(email_smtp_password=""),
            tmp_path / "state.json",
            sender=lambda message: None,
        )
