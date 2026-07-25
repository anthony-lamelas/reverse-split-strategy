"""Backtest package for the reverse-split shorting strategy.

Modules:
- engine: the core `backtest_mega` simulator (faithful reproduction of the grid-search
  logic from analysis/strategy.ipynb) plus metrics helpers.
- events: builders that turn MongoDB collections into a normalized event frame
  (columns: ticker, t_ann, t_split, ratio).
- shortability: a proxy classifier estimating whether each event would have been
  shortable at a retail broker (e.g. Schwab).
"""
from .engine import backtest_mega, summarize_trades, CHOSEN_STRATEGY

__all__ = ["backtest_mega", "summarize_trades", "CHOSEN_STRATEGY"]
