"""Schwab Trader API integration (auth + order construction).

The strategy shorts distressed micro-caps; many are not shortable/marginable at a
retail broker, so the order layer defaults to DRY_RUN and records fill-vs-reject
outcomes rather than assuming fills. See docs/SCHWAB_SETUP.md.
"""
