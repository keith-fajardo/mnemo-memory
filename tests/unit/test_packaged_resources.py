from importlib import resources


def test_runtime_resources_are_available_from_the_package() -> None:
    package_files = resources.files("mnemo_memory")

    assert package_files.joinpath("resources/migrations/0001_initial.sql").is_file()
    assert package_files.joinpath(
        "resources/migrations/0002_checkpoint_aggregate_revisions.sql"
    ).is_file()
    assert package_files.joinpath("resources/migrations/0003_dbt_manifest_snapshots.sql").is_file()
    assert package_files.joinpath(
        "resources/migrations/0004_source_structure_snapshots.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/migrations/0005_source_snapshot_activations.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/migrations/0006_checkpoint_lifecycle_events.sql",
    ).is_file()
    assert package_files.joinpath(
        "resources/migrations/0007_approved_episodic_events.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/migrations/0008_source_file_fingerprints.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/migrations/0009_checkpoint_source_observations.sql"
    ).is_file()
    assert package_files.joinpath("resources/migrations/0010_knowledge_documents.sql").is_file()
    assert package_files.joinpath("resources/migrations/0011_knowledge_section_fts.sql").is_file()
    assert package_files.joinpath(
        "resources/migrations/0012_knowledge_section_embeddings.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/migrations/0013_approved_episodic_event_governance.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/migrations/0014_dbt_supplemental_artifacts.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/migrations/0015_dbt_macro_dependency_edges.sql"
    ).is_file()
    assert package_files.joinpath("resources/migrations/0016_dbt_source_freshness.sql").is_file()
    assert package_files.joinpath(
        "resources/migrations/0017_dbt_manifest_activations.sql"
    ).is_file()
    assert package_files.joinpath("resources/migrations/0018_event_outbox.sql").is_file()
    assert package_files.joinpath("resources/migrations/0019_task_activity_events.sql").is_file()
    assert package_files.joinpath(
        "resources/migrations/0020_episodic_memory_candidates.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/migrations/0021_episodic_candidate_reviews.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/migrations/0022_episodic_memory_governance.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/migrations/0023_episodic_memory_expirations.sql"
    ).is_file()
    assert package_files.joinpath("resources/migrations/0024_episodic_memory_purges.sql").is_file()
    assert package_files.joinpath("resources/migrations/0025_task_activity_retention.sql").is_file()
    assert package_files.joinpath("resources/migrations/0026_episodic_deletions.sql").is_file()
    assert package_files.joinpath(
        "resources/migrations/0027_approved_episodic_event_pins.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/migrations/0028_project_index_sync_status.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/postgres_migrations/0001_team_control_plane.sql"
    ).is_file()
    assert package_files.joinpath("resources/postgres_migrations/0002_team_knowledge.sql").is_file()
    assert package_files.joinpath(
        "resources/postgres_migrations/0003_team_checkpoints.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/postgres_migrations/0004_team_task_events_outbox.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/postgres_migrations/0005_team_approved_episodic_events.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/postgres_migrations/0006_team_episodic_candidates.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/postgres_migrations/0007_team_episodic_governance.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/postgres_migrations/0008_team_episodic_retention.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/postgres_migrations/0009_team_task_activity_retention.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/postgres_migrations/0010_team_episodic_deletions.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/postgres_migrations/0011_team_source_structure.sql"
    ).is_file()
    assert package_files.joinpath(
        "resources/postgres_migrations/0012_team_checkpoint_source_observations.sql"
    ).is_file()
    assert package_files.joinpath("resources/schemas/context-packet-v1.json").is_file()
    assert package_files.joinpath("resources/web/index.html").is_file()
    assert package_files.joinpath("resources/web/app.js").is_file()
    assert package_files.joinpath("resources/web/app.css").is_file()
