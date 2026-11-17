"""Non-configurable policy for the A-share paper-only downstream build.

There is deliberately no environment flag to enable brokerage access. Enabling
real gateways requires a separately reviewed code change and authorization.
"""
from typing import NoReturn


class PaperOnlyViolation(PermissionError):
    """This build is not authorized to connect to execution venues."""


def deny_broker_access() -> NoReturn:
    """Fail before credentials, SDK initialization or brokerage network access."""
    raise PaperOnlyViolation("ASHARE_PAPER_ONLY: broker access is disabled in this build")


def agent_method_allowed(method: str) -> bool:
    """Research agents can read; they cannot submit or activate changes."""
    return method.upper() in {"GET", "HEAD", "OPTIONS"}
