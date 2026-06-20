from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import json
from pathlib import Path
import smtplib
from typing import Callable, Iterable


MailSender = Callable[[EmailMessage], None]


def notify_opportunities(
    forecasts: Iterable[dict],
    settings,
    state_path: Path = Path("data/email_alert_state.json"),
    *,
    now: datetime | None = None,
    sender: MailSender | None = None,
) -> int:
    if not settings.email_alert_enabled:
        return 0
    _validate_settings(settings)
    observed_at = now or datetime.now(timezone.utc)
    candidates = _eligible_candidates(
        forecasts,
        settings.email_alert_score_threshold,
    )
    state = _load_state(state_path)
    sent = state.get("sent", {})
    active_keys = {item["key"] for item in candidates}
    sent = {key: value for key, value in sent.items() if key in active_keys}
    cooldown = timedelta(hours=max(1, settings.email_alert_cooldown_hours))
    due = [
        item
        for item in candidates
        if _is_due(sent.get(item["key"]), observed_at, cooldown)
    ]
    if not due:
        _save_state(state_path, {"sent": sent})
        return 0

    message = _build_message(due, settings, observed_at)
    (sender or _smtp_sender(settings))(message)
    timestamp = observed_at.isoformat()
    for item in due:
        sent[item["key"]] = timestamp
    _save_state(state_path, {"sent": sent})
    return len(due)


def _eligible_candidates(forecasts: Iterable[dict], threshold: int) -> list[dict]:
    candidates: list[dict] = []
    seen: set[str] = set()
    for base in forecasts:
        variants = base.get("strategy_variants") or [base]
        for variant in variants:
            strategy = variant.get("strategy") or {
                "id": "adaptive",
                "name": "综合自适应",
            }
            score = int(variant.get("opportunity_score") or 0)
            if score < threshold:
                continue
            inst_id = str(base.get("inst_id") or variant.get("inst_id") or "")
            strategy_id = str(strategy.get("id") or "adaptive")
            key = f"{inst_id}:{strategy_id}"
            if not inst_id or key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "key": key,
                    "inst_id": inst_id,
                    "symbol": base.get("symbol") or inst_id.split("-")[0],
                    "strategy": strategy.get("name") or strategy_id,
                    "score": score,
                    "decision": variant.get("decision") or "--",
                    "validation": variant.get("strategy_validation") or "待验证",
                    "current_price": float(
                        variant.get("current_price")
                        or base.get("current_price")
                        or 0
                    ),
                    "forecasts": variant.get("forecasts") or [],
                }
            )
    return sorted(candidates, key=lambda item: (-item["score"], item["key"]))


def _build_message(candidates: list[dict], settings, observed_at: datetime) -> EmailMessage:
    highest = candidates[0]["score"]
    message = EmailMessage()
    message["Subject"] = f"[QIS] {len(candidates)} 个机会达到 {settings.email_alert_score_threshold} 分（最高 {highest}）"
    message["From"] = settings.email_smtp_from or settings.email_smtp_username
    message["To"] = ", ".join(settings.email_alert_recipients)
    lines = [
        "QIS 机会分提醒",
        "",
        f"监测时间：{observed_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"触发阈值：{settings.email_alert_score_threshold} 分",
        "",
    ]
    for index, item in enumerate(candidates, 1):
        horizons = {row.get("key"): row for row in item["forecasts"]}
        lines.extend(
            [
                f"{index}. {item['symbol']} / {item['inst_id']}",
                f"   机会分：{item['score']}  策略：{item['strategy']}",
                f"   状态：{item['decision']}  验证：{item['validation']}",
                f"   当前价：{_number(item['current_price'])}",
                "   预测："
                + "  ".join(
                    _horizon_text(horizons.get(key), label)
                    for key, label in (("1w", "1周"), ("1m", "1月"), ("3m", "3月"))
                ),
                "",
            ]
        )
    lines.extend(
        [
            "请结合市场环境、策略验证状态、止损位和仓位上限独立判断。",
            "本邮件仅用于决策辅助，不构成投资建议，也不会自动下单。",
        ]
    )
    message.set_content("\n".join(lines))
    return message


def _smtp_sender(settings) -> MailSender:
    def send(message: EmailMessage) -> None:
        if settings.email_smtp_use_ssl:
            with smtplib.SMTP_SSL(
                settings.email_smtp_host,
                settings.email_smtp_port,
                timeout=30,
            ) as smtp:
                smtp.login(
                    settings.email_smtp_username,
                    settings.email_smtp_password,
                )
                smtp.send_message(message)
            return
        with smtplib.SMTP(
            settings.email_smtp_host,
            settings.email_smtp_port,
            timeout=30,
        ) as smtp:
            smtp.starttls()
            smtp.login(
                settings.email_smtp_username,
                settings.email_smtp_password,
            )
            smtp.send_message(message)

    return send


def _validate_settings(settings) -> None:
    required = {
        "QIS_EMAIL_ALERT_RECIPIENTS": settings.email_alert_recipients,
        "QIS_EMAIL_SMTP_HOST": settings.email_smtp_host,
        "QIS_EMAIL_SMTP_USERNAME": settings.email_smtp_username,
        "QIS_EMAIL_SMTP_PASSWORD": settings.email_smtp_password,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"邮件提醒缺少配置：{', '.join(missing)}")


def _is_due(value: str | None, now: datetime, cooldown: timedelta) -> bool:
    if not value:
        return True
    try:
        sent_at = datetime.fromisoformat(value)
    except ValueError:
        return True
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    return now - sent_at >= cooldown


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"sent": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sent": {}}
    return value if isinstance(value, dict) else {"sent": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _horizon_text(row: dict | None, label: str) -> str:
    if not row:
        return f"{label} --"
    expected_return = float(row.get("expected_return") or 0) * 100
    probability = float(row.get("up_probability") or 0) * 100
    return f"{label} {expected_return:+.2f}% / 上涨 {probability:.0f}%"


def _number(value: float) -> str:
    return f"{value:,.8f}".rstrip("0").rstrip(".")
