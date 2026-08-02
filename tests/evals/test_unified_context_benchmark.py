from scripts.run_unified_context_benchmark import evaluate


def test_unified_checkpoint_and_lineage_fixture_passes_deterministically() -> None:
    first = evaluate()
    assert first == evaluate()
    assert first["passed"] is True
    tokens = first["token_accounting"]
    assert isinstance(tokens, dict)
    assert tokens["structural_savings_percent"] >= 50
    assert tokens["combined_savings_percent"] >= 50
