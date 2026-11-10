"""Adapted, non-executing investor personas and their local adapter."""

from .buffett import BuffettAgent
from .munger import MungerAgent
from .upstream_adapter import PreparedInvestorReview, UpstreamPersonaAdapter

__all__ = ["BuffettAgent", "MungerAgent", "PreparedInvestorReview", "UpstreamPersonaAdapter"]
