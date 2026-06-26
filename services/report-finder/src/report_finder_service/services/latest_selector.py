from datetime import datetime, timezone

from report_finder_service.models.schemas import (
    ReportCandidate,
    ReportCandidateSnapshot,
    ReportTarget,
    ReportType,
    SelectionEvidence,
)


class LatestReportSelector:
    ANNUAL_TYPES = {ReportType.annual}
    FORMAL_REPORT_TYPES = {
        ReportType.annual,
        ReportType.semiannual,
        ReportType.q1,
        ReportType.q3,
    }
    TYPE_PRIORITY = {
        ReportType.annual: 50,
        ReportType.form_20f: 50,
        ReportType.form_10k: 50,
        ReportType.semiannual: 40,
        ReportType.q3: 30,
        ReportType.form_10q: 30,
        ReportType.form_6k: 25,
        ReportType.q1: 20,
        ReportType.earnings: 10,
    }

    def select(self, candidates: list[ReportCandidate], target: ReportTarget) -> ReportCandidate:
        selected, _ = self.select_with_evidence(candidates, target)
        return selected

    def select_with_evidence(
        self, candidates: list[ReportCandidate], target: ReportTarget
    ) -> tuple[ReportCandidate, SelectionEvidence]:
        if not candidates:
            raise ValueError("没有候选报告可供选择")

        filtered = self._filter_candidates(candidates, target)

        if not filtered:
            raise ValueError(f"未找到满足 {target.value} 的候选报告")

        ranked = sorted(filtered, key=self._sort_key, reverse=True)
        selected = ranked[0].model_copy(
            update={
                "selection_reason": self._build_reason(ranked[0], target, len(filtered)),
            }
        )
        evidence = self._build_evidence(selected, ranked, target)
        return selected, evidence

    def _filter_candidates(
        self, candidates: list[ReportCandidate], target: ReportTarget
    ) -> list[ReportCandidate]:
        if target == ReportTarget.annual_report:
            return [candidate for candidate in candidates if candidate.report_type in self.ANNUAL_TYPES]
        if target == ReportTarget.financial_report:
            return [
                candidate for candidate in candidates if candidate.report_type in self.FORMAL_REPORT_TYPES
            ]
        if target == ReportTarget.latest_report:
            return [
                candidate for candidate in candidates if candidate.report_type in self.FORMAL_REPORT_TYPES
            ]
        return list(candidates)

    def _sort_key(self, candidate: ReportCandidate) -> tuple:
        return (
            candidate.report_end,
            candidate.published_at,
            self.TYPE_PRIORITY.get(candidate.report_type, 0),
        )

    def _build_evidence(
        self,
        selected: ReportCandidate,
        ranked: list[ReportCandidate],
        target: ReportTarget,
    ) -> SelectionEvidence:
        return SelectionEvidence(
            checked_at=datetime.now(timezone.utc),
            target_scope=target,
            ranking_rule=self._ranking_rule(target),
            filtered_candidates_count=len(ranked),
            top_candidates=[self._snapshot(candidate) for candidate in ranked[:3]],
            selected_is_latest_by_report_end=selected.report_end == max(
                candidate.report_end for candidate in ranked
            ),
            selected_is_latest_by_published_at=selected.published_at == max(
                candidate.published_at for candidate in ranked
            ),
        )

    def _snapshot(self, candidate: ReportCandidate) -> ReportCandidateSnapshot:
        return ReportCandidateSnapshot(
            title=candidate.title,
            report_type=candidate.report_type,
            report_end=candidate.report_end,
            published_at=candidate.published_at,
            document_url=candidate.document_url,
            landing_url=candidate.landing_url,
        )

    def _ranking_rule(self, target: ReportTarget) -> str:
        if target == ReportTarget.annual_report:
            return "仅保留 A 股年度报告 annual；按 report_end、published_at、report_type_priority 倒序排序"
        if target == ReportTarget.financial_report:
            return (
                "保留 A 股正式定期财报候选（annual / semiannual / q1 / q3）；"
                "按 report_end、published_at、report_type_priority 倒序排序"
            )
        if target == ReportTarget.latest_report:
            return "保留全部正式定期报告候选；按 report_end、published_at、report_type_priority 倒序排序"
        return "保留全部候选；按 report_end、published_at、report_type_priority 倒序排序"

    def _build_reason(self, candidate: ReportCandidate, target: ReportTarget, count: int) -> str:
        if target == ReportTarget.annual_report:
            return (
                f"在 {count} 份年报候选中，按报告期和披露日期排序后选中 "
                f"{candidate.report_end.isoformat()} 的 {candidate.report_type.value}"
            )
        if target == ReportTarget.financial_report:
            return (
                f"在 {count} 份正式财报候选中，按报告期和披露日期排序后选中 "
                f"{candidate.report_end.isoformat()} 的 {candidate.report_type.value}"
            )
        if target == ReportTarget.latest_report:
            return (
                f"在 {count} 份正式定期报告候选中，按最新披露时间和报告期排序后选中 "
                f"{candidate.report_end.isoformat()} 的 {candidate.report_type.value}"
            )
        return f"选中 {candidate.report_end.isoformat()} 的 {candidate.report_type.value}"
