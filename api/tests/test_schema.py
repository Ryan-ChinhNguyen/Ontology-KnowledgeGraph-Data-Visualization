"""Guards on the database schema itself.

These assert the constraints the upload rules depend on are actually declared,
so removing one shows up as a failing test rather than as a race condition in
production.
"""

from ontology_shared.models import Base, File, Job, Session


def constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def index_names(table) -> set[str]:
    return {index.name for index in table.indexes}


class TestSessionsTable:
    def test_rejects_empty_or_negative_totals(self) -> None:
        assert {
            "ck_sessions_total_files_positive",
            "ck_sessions_total_size_positive",
        } <= constraint_names(Session.__table__)

    def test_sizes_are_wide_enough_for_future_limits(self) -> None:
        assert Session.__table__.c.total_size_bytes.type.__class__.__name__ == "BigInteger"


class TestFilesTable:
    def test_content_hash_is_unique(self) -> None:
        """The API's pre-insert check cannot stop two concurrent uploads of the
        same bytes; this constraint is what actually does."""
        assert "uq_files_sha256_hash" in constraint_names(File.__table__)

    def test_filenames_are_unique_within_a_session(self) -> None:
        assert "uq_files_session_filename" in constraint_names(File.__table__)

    def test_hash_length_is_checked(self) -> None:
        assert "ck_files_sha256_length" in constraint_names(File.__table__)

    def test_session_lookup_is_indexed(self) -> None:
        assert "ix_files_session_id" in index_names(File.__table__)

    def test_deleting_a_session_removes_its_files(self) -> None:
        foreign_key = next(iter(File.__table__.c.session_id.foreign_keys))
        assert foreign_key.ondelete == "CASCADE"


class TestJobsTable:
    def test_latest_job_lookup_is_indexed(self) -> None:
        assert "ix_jobs_session_id_queued_at" in index_names(Job.__table__)

    def test_session_id_has_no_redundant_index(self) -> None:
        """The composite index above already covers session_id as its leading
        term, so a second index on it alone would only cost write time."""
        assert "ix_jobs_session_id" not in index_names(Job.__table__)

    def test_attempt_count_cannot_go_negative(self) -> None:
        assert "ck_jobs_attempt_count_non_negative" in constraint_names(Job.__table__)


class TestSchemaScope:
    def test_only_the_upload_tables_exist(self) -> None:
        assert set(Base.metadata.tables) == {"sessions", "files", "jobs"}
