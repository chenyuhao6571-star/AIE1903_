from __future__ import annotations

from .config import ranking_fixtures, ranking_scoring
from .models import RankingRecord
from .university import resolve_university


def normalize_rank(provider: str, rank: int | None, total_ranked: int | None = None, ranking_year: int | None = None) -> float | None:
    """Convert one provider rank to a standardized 0-10 score.

    Raw ranking numbers are never averaged across providers. Each provider is
    converted to a percentile-based score using configurable thresholds, then
    those standardized scores can be combined.
    """
    if rank is None:
        return None
    config = ranking_scoring()
    provider_config = config["providers"].get(provider.upper(), config["providers"].get("QS", {}))
    total = total_ranked or provider_config.get("default_total_ranked", 1500)
    if total <= 0:
        return None
    percentile = max(rank, 1) / total
    thresholds = config["percentile_thresholds"]
    for upper_bound, score in thresholds:
        if percentile <= upper_bound:
            return float(score)
    return float(thresholds[-1][1])


class RankingRepository:
    def __init__(self, fixtures: list[dict] | None = None) -> None:
        self._records: list[RankingRecord] = []
        for item in fixtures if fixtures is not None else ranking_fixtures():
            normalized = resolve_university(item.get("institution", ""))
            record = RankingRecord(
                institution=normalized.canonical,
                provider=item["provider"],
                ranking_type=item["ranking_type"],
                subject=item.get("subject"),
                ranking_year=item["ranking_year"],
                rank=item.get("rank"),
                total_ranked=item.get("total_ranked"),
                raw_score=item.get("raw_score"),
                standardized_score=None,
                source_url=item.get("source_url"),
                retrieved_at=item.get("retrieved_at"),
                status="found",
            )
            record.standardized_score = normalize_rank(
                record.provider, record.rank, record.total_ranked, record.ranking_year
            )
            self._records.append(record)

    def lookup(self, institution: str, ranking_type: str = "overall", subject: str | None = None) -> list[RankingRecord]:
        canonical = resolve_university(institution).canonical
        result = [
            r
            for r in self._records
            if r.institution == canonical
            and r.ranking_type == ranking_type
            and (subject is None or r.subject == subject)
        ]
        return sorted(result, key=lambda r: (r.ranking_year, r.provider))

    def combined_standardized_score(self, institution: str, ranking_type: str = "overall", subject: str | None = None) -> tuple[float | None, list[RankingRecord]]:
        records = self.lookup(institution, ranking_type, subject)
        scores = [r.standardized_score for r in records if r.standardized_score is not None]
        if not scores:
            return None, records
        return sum(scores) / len(scores), records
