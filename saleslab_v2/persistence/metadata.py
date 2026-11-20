"""V2 models share the application's Alembic metadata without legacy coupling."""

from database import Base

v2_metadata = Base.metadata

# Explicit ownership boundary. Never infer V2 ownership from a naming convention:
# future V2 tables are required to be registered here even if their names have no
# ``_v2`` suffix.
V2_TABLE_NAMES = frozenset(
    {
        "analysis_runs_v2",
        "source_documents_v2",
        "stage_results_v2",
        "calculation_traces_v2",
        "metric_observations_v2",
        "reconciliation_results_v2",
        "data_quality_issues_v2",
        "coverage_assessments_v2",
        "capability_decisions_v2",
    }
)


def legacy_tables(metadata=v2_metadata):
    """Select the registered legacy tables without relying on name suffixes."""
    return [
        table for table in metadata.sorted_tables if table.name not in V2_TABLE_NAMES
    ]
