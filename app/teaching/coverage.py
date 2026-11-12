"""Claim-level exact-span coverage labels for audit and handoff decisions."""

from enum import StrEnum


class ClaimCoverage(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


def assess_claim_coverage(
    claims: tuple[str, ...], evidence: dict[str, tuple[str, ...]]
) -> ClaimCoverage:
    if not claims:
        return ClaimCoverage.UNSUPPORTED
    allowed = {span for spans in evidence.values() for span in spans}
    supported_count = sum(claim in allowed for claim in claims)
    if supported_count == len(claims):
        return ClaimCoverage.SUPPORTED
    if supported_count:
        return ClaimCoverage.PARTIALLY_SUPPORTED
    return ClaimCoverage.UNSUPPORTED
