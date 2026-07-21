"""Application metadata contract following vn.py's pluggable app model."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qis.trader.engine import BaseEngine


class BaseApp:
    """Metadata used by :class:`MainEngine` to install a function engine."""

    app_name: str = ""
    display_name: str = ""
    engine_class: type[BaseEngine]
