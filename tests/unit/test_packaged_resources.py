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
    assert package_files.joinpath("resources/schemas/context-packet-v1.json").is_file()
