"""Deterministic, explainable initial fit scoring.

This score is an operating heuristic for prioritization.  It is not a
market-validated probability, forecast, or claim that a prospect has a need.
The input ``fit_reason`` remains a supplied rationale/evidence note; scoring
never turns it into a factual pain point.
"""

from __future__ import annotations

from dataclasses import dataclass
from .normalization import canonical_text, normalize_token

SCORING_DISCLAIMER = (
    "Initial deterministic heuristic only; not market-validated and not a claim of customer need."
)

_TARGET_INDUSTRY_TERMS = (
    "agency",
    "cafe",
    "clinic",
    "distributor",
    "education",
    "healthcare",
    "local service",
    "professional",
    "restaurant",
    "retail",
    "service",
    "training",
    "trading",
)


@dataclass(frozen=True)
class FitScore:
    """A score plus the evidence-shaped reasons that produced it."""

    score: int
    reasons: tuple[str, ...]
    confidence: float | None
    disclaimer: str = SCORING_DISCLAIMER

    def as_dict(self) -> dict[str, object]:
        """Return the score, reasons, confidence, and disclaimer as a plain dict."""

        return {
            "fit_score": self.score,
            "score_reasons": list(self.reasons),
            "fit_confidence": self.confidence,
            "score_disclaimer": self.disclaimer,
        }


def _provided_confidence(value: object) -> float | None:
    """Coerce a supplied confidence value to a float in ``[0, 1]``, or ``None``."""

    if value is None or value == "":
        return None
    result = float(value)
    if not 0 <= result <= 1:
        raise ValueError("fit confidence must be between 0 and 1")
    return result


def calculate_fit_score(
    *,
    region: str,
    industry: str,
    verified_status: str,
    fit_reason: str,
    source_evidence_count: int = 1,
    website_present: bool = False,
    confidence: float | None = None,
) -> FitScore:
    """Calculate the same score for the same supplied facts every time.

    Points intentionally reward research completeness and alignment with the
    documented Maharashtra-first hypothesis, not inferred personal data or
    assumed buying intent.  ``website_present`` is accepted for future
    explainability but does not change the score; source evidence count does.
    """

    del website_present  # A website is evidence for identity, not fit by itself.
    score = 0
    reasons: list[str] = []

    region_text = canonical_text(region).casefold()
    if "maharashtra" in region_text:
        score += 30
        reasons.append("+30: region matches the initial Maharashtra focus")
    else:
        reasons.append("+0: region is outside the initial Maharashtra focus")

    industry_text = canonical_text(industry).casefold()
    matched_term = next((term for term in _TARGET_INDUSTRY_TERMS if term in industry_text), None)
    if matched_term:
        score += 25
        reasons.append(f"+25: industry matches an initial target pattern ({matched_term})")
    else:
        reasons.append("+0: industry does not match an initial target pattern")

    verification = normalize_token(verified_status)
    if verification == "verified":
        score += 20
        reasons.append("+20: source verification is marked verified")
    elif verification in {"partially_verified", "needs_review", "pending"}:
        score += 10
        reasons.append("+10: source verification is partial or awaiting review")
    else:
        reasons.append("+0: source verification is not fully verified")

    rationale = canonical_text(fit_reason)
    if rationale and normalize_token(rationale) not in {"unknown", "not_provided", "not_available"}:
        score += 15
        reasons.append("+15: a fit rationale was supplied (not proof of customer need)")
    else:
        reasons.append("+0: fit rationale remains unknown")

    evidence_count = max(0, int(source_evidence_count))
    if evidence_count >= 2:
        score += 10
        reasons.append(f"+10: {evidence_count} source-evidence records are retained")
    elif evidence_count == 1:
        score += 5
        reasons.append("+5: one source-evidence record is retained")
    else:
        reasons.append("+0: no source-evidence record is available")

    return FitScore(score=min(score, 100), reasons=tuple(reasons), confidence=_provided_confidence(confidence))
