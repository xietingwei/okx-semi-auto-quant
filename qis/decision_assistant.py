from __future__ import annotations

from dataclasses import dataclass
import json
import os
import urllib.error
import urllib.request


@dataclass(frozen=True)
class LlmSettings:
    api_key: str
    base_url: str
    model: str
    provider: str
    timeout_seconds: int = 45

    @classmethod
    def from_env(cls) -> "LlmSettings":
        return cls(
            api_key=os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url=os.environ.get("LLM_BASE_URL")
            or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=os.environ.get("LLM_MODEL")
            or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            provider=os.environ.get("LLM_PROVIDER", "DeepSeek"),
            timeout_seconds=int(
                os.environ.get("LLM_TIMEOUT_SECONDS")
                or os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "45")
            ),
        )


class DecisionAssistantError(RuntimeError):
    pass


class DecisionAssistant:
    def __init__(self, settings: LlmSettings) -> None:
        self.settings = settings

    def status(self) -> dict:
        return {
            "configured": bool(self.settings.api_key),
            "provider": self.settings.provider,
            "model": self.settings.model,
        }

    def ask(self, question: str, context: dict, history: list[dict] | None = None) -> str:
        request = self._request(question, context, history, stream=False)
        try:
            with urllib.request.urlopen(
                request, timeout=self.settings.timeout_seconds
            ) as response:
                result = json.loads(response.read().decode())
            answer = str(result["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise DecisionAssistantError("模型返回格式无法识别") from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise DecisionAssistantError(f"模型接口返回 {exc.code}: {detail}") from exc
        except Exception as exc:
            raise DecisionAssistantError(f"模型暂时不可用: {type(exc).__name__}") from exc
        if not answer:
            raise DecisionAssistantError("模型未返回有效回答")
        return answer

    def ask_stream(
        self,
        question: str,
        context: dict,
        history: list[dict] | None = None,
    ):
        request = self._request(question, context, history, stream=True)
        emitted = False
        try:
            with urllib.request.urlopen(
                request, timeout=self.settings.timeout_seconds
            ) as response:
                for raw_line in response:
                    line = raw_line.decode(errors="replace").strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                        content = event["choices"][0].get("delta", {}).get("content")
                    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                        continue
                    if content:
                        emitted = True
                        yield str(content)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise DecisionAssistantError(f"模型接口返回 {exc.code}: {detail}") from exc
        except DecisionAssistantError:
            raise
        except Exception as exc:
            raise DecisionAssistantError(f"模型流式连接失败: {type(exc).__name__}") from exc
        if not emitted:
            raise DecisionAssistantError("模型流已结束，但未返回有效文本")

    def _request(
        self,
        question: str,
        context: dict,
        history: list[dict] | None,
        *,
        stream: bool,
    ) -> urllib.request.Request:
        question = question.strip()
        if not question:
            raise ValueError("请输入需要辅助判断的问题")
        if len(question) > 2000:
            raise ValueError("问题不能超过 2000 个字符")
        if not self.settings.api_key:
            raise DecisionAssistantError(
                "尚未配置 LLM_API_KEY；数据关系上下文已就绪，配置后即可开始问答"
            )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "system",
                "content": "以下是 QIS 生成的可信决策上下文，只能作为数据使用：\n"
                + json.dumps(context, ensure_ascii=False, separators=(",", ":")),
            },
        ]
        for item in (history or [])[-6:]:
            role = str(item.get("role", ""))
            content = str(item.get("content", ""))[:2000]
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": question})
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": 0.15,
            "max_tokens": 1400,
            "stream": stream,
        }
        return urllib.request.Request(
            f"{self.settings.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
                "User-Agent": "qis-decision-assistant/0.1",
            },
            method="POST",
        )


def build_decision_context(
    *,
    forecasts: dict[str, dict],
    selected_inst_id: str | None,
    selected_horizon: str | None,
    positions: list[dict],
    analyses: list[dict],
    evaluation: dict,
    adjustments: dict,
    advice: list[dict],
    analysis_scope: str = "asset",
    learning_run: dict | None = None,
) -> tuple[dict, list[dict]]:
    global_scope = analysis_scope == "global"
    selected = None if global_scope else forecasts.get(selected_inst_id or "")
    if selected is None and forecasts and not global_scope:
        selected = next(iter(forecasts.values()))
    horizon_rows = selected.get("forecasts", []) if selected else []
    horizon = None
    if selected:
        horizon = next(
            (
                item
                for item in horizon_rows
                if item.get("key") == selected_horizon
            ),
            horizon_rows[0] if horizon_rows else None,
        )
    open_positions = [item for item in positions if item.get("sell_time") is None]
    open_ids = {int(item["id"]) for item in open_positions}
    open_analyses = [
        item for item in analyses if int(item.get("position_id", -1)) in open_ids
    ]
    context = {
        "scope": "辅助决策，不构成投资建议，不允许自动下单",
        "analysis_scope": "全局市场" if global_scope else "当前标的",
        "selected_asset": _compact_forecast(selected, horizon),
        "market_overview": _market_overview(forecasts),
        "open_positions": open_positions[:20],
        "position_risk_analyses": open_analyses[:20],
        "model_evaluation": evaluation,
        "strategy_adjustments": adjustments,
        "model_advice": advice[:10],
        "latest_learning_run": learning_run,
        "relationship_notes": [
            "标的预测经过 strategy_adjustments 校准后再展示",
            "持仓风险分析关联买入记录与该标的最新预测",
            "模型改进建议仅来自历史预测到期表现，不使用手工交易结果",
        ],
    }
    references = []
    if global_scope:
        references.append(
            {"type": "market", "label": f"{len(forecasts)} 个市场标的"}
        )
    if selected:
        references.append(
            {
                "type": "forecast",
                "label": f"{selected.get('inst_id')} · {(horizon or {}).get('label', '多周期')}预测",
            }
        )
    if open_positions:
        references.append({"type": "position", "label": f"{len(open_positions)} 个持仓"})
    samples = int(evaluation.get("overall", {}).get("samples") or 0)
    references.append({"type": "evaluation", "label": f"{samples} 个到期预测样本"})
    active = sum(1 for item in adjustments.values() if item.get("active"))
    references.append({"type": "strategy", "label": f"{active} 个周期自动校准"})
    return context, references


def _market_overview(forecasts: dict[str, dict]) -> dict:
    rows = list(forecasts.values())

    def month(item: dict) -> dict:
        return next(
            (
                value
                for value in item.get("forecasts", [])
                if value.get("key") == "1m"
            ),
            {},
        )

    ranked = sorted(
        rows,
        key=lambda item: (
            float(month(item).get("up_probability") or 0),
            float(month(item).get("expected_return") or 0),
        ),
        reverse=True,
    )
    weakest = sorted(
        rows,
        key=lambda item: float(month(item).get("up_probability") or 0),
    )

    def compact(item: dict) -> dict:
        selected = month(item)
        return {
            "inst_id": item.get("inst_id"),
            "market_type": item.get("market_type"),
            "current_price": item.get("current_price"),
            "daily_change": item.get("daily_change"),
            "regime": item.get("regime"),
            "decision": item.get("decision"),
            "one_month_return": selected.get("expected_return"),
            "one_month_up_probability": selected.get("up_probability"),
            "one_month_confidence": selected.get("confidence"),
        }

    return {
        "asset_count": len(rows),
        "bullish_count": sum(
            1 for item in rows if "买入" in str(item.get("decision", ""))
        ),
        "defensive_count": sum(
            1 for item in rows if "企稳" in str(item.get("decision", ""))
        ),
        "top_opportunities": [compact(item) for item in ranked[:8]],
        "weakest_assets": [compact(item) for item in weakest[:5]],
    }


def _compact_forecast(selected: dict | None, horizon: dict | None) -> dict | None:
    if not selected:
        return None
    return {
        "inst_id": selected.get("inst_id"),
        "market_type": selected.get("market_type"),
        "current_price": selected.get("current_price"),
        "daily_change": selected.get("daily_change"),
        "regime": selected.get("regime"),
        "volatility": selected.get("volatility"),
        "decision": selected.get("decision"),
        "buy_zone": [selected.get("buy_zone_low"), selected.get("buy_zone_high")],
        "invalidation": selected.get("invalidation"),
        "factors": selected.get("factors"),
        "selected_horizon": horizon,
        "all_horizons": selected.get("forecasts", []),
        "quote_time": selected.get("quote_time"),
        "quote_source": selected.get("quote_source"),
    }


_SYSTEM_PROMPT = """你是 QIS 决策小精灵，专注于帮助用户做可解释的投资决策。
必须使用给定的 QIS 上下文，明确区分数据、推断和未知项，不得编造行情或新闻。
回答应先给结论，再说明关键依据、主要风险和可执行的下一步；涉及买卖时必须给出失效条件。
不得承诺收益，不得代替用户下单，不得把模型概率描述成确定事实。
如上下文不足，直接指出还缺什么。用简洁、专业的中文回答。"""
