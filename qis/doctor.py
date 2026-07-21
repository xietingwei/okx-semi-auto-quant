from __future__ import annotations

from dataclasses import dataclass

from qis.config import Settings
from qis.deepseek_intel import DeepSeekIntelProvider, DeepSeekSettings
from qis.runtime import create_runtime
from qis.storage import Storage
from qis.trader.object import HistoryRequest


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def run_doctor(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    checks.append(Check("mode", settings.mode.value in {"paper", "live"}, settings.mode.value))
    checks.append(Check("database", _check_database(settings), str(settings.db_path)))
    checks.append(Check("pause_file", not settings.pause_file.exists(), str(settings.pause_file)))
    checks.append(Check("risk_per_trade", 0 < settings.risk_per_trade <= 0.02, f"{settings.risk_per_trade:.4f}"))
    checks.append(Check("max_leverage", 0 < settings.max_leverage <= 3, f"{settings.max_leverage:.2f}"))
    checks.extend(_check_okx(settings))
    if settings.deepseek_api_key:
        provider = DeepSeekIntelProvider(
            DeepSeekSettings(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
                timeout_seconds=settings.deepseek_timeout_seconds,
                cache_ttl_seconds=settings.deepseek_cache_ttl_seconds,
            )
        )
        ok, detail = provider.check_model()
        checks.append(Check("deepseek_intel", ok, detail))
    else:
        checks.append(Check("deepseek_intel", True, "disabled; configure DEEPSEEK_API_KEY to enable"))
    return checks


def _check_okx(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    with create_runtime(settings) as runtime:
        client = runtime.okx
        try:
            candles = runtime.market_data.query_history(
                HistoryRequest(settings.inst_id, settings.bar, 5)
            )
            checks.append(
                Check(
                    "okx_public_market",
                    len(candles) > 0,
                    f"{len(candles)} candles",
                )
            )
        except Exception as exc:
            checks.append(Check("okx_public_market", False, str(exc)))
        try:
            instrument = client.public_instrument(settings.inst_id)
            detail = (
                f"instType={instrument.get('instType')} "
                f"ctVal={instrument.get('ctVal')} lotSz={instrument.get('lotSz')}"
            )
            checks.append(
                Check("okx_instrument", bool(instrument.get("instId")), detail)
            )
        except Exception as exc:
            checks.append(Check("okx_instrument", False, str(exc)))
        if (
            settings.okx_api_key
            and settings.okx_api_secret
            and settings.okx_api_passphrase
        ):
            try:
                equity = client.balance_equity()
                checks.append(
                    Check(
                        "okx_private_account",
                        equity is not None,
                        f"USDT equity={equity}",
                    )
                )
            except Exception as exc:
                checks.append(Check("okx_private_account", False, str(exc)))
        else:
            checks.append(
                Check(
                    "okx_private_account",
                    settings.mode.value == "paper",
                    "credentials not configured",
                )
            )
    return checks


def _check_database(settings: Settings) -> bool:
    try:
        Storage(settings.db_path).init()
    except Exception:
        return False
    return True
