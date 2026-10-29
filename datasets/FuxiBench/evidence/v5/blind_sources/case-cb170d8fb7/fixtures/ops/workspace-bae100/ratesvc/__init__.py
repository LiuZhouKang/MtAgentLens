"""ratesvc — interval and monetary helpers for the rate-pricing pipeline."""

from .money import convert, format_amount, total
from .util import parse_rate, split_interval

__all__ = ["convert", "format_amount", "parse_rate", "split_interval", "total"]
__version__ = "0.4.1"
