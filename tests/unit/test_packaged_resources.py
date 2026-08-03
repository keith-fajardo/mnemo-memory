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
    assert package_files.joinpath("resources/schemas/context-packet-v1.json").is_file()
