"""Configured firmware release providers."""

from firmware_list.providers.nerivec import NERIVEC

PROVIDERS = (NERIVEC,)

__all__ = ["NERIVEC", "PROVIDERS"]
