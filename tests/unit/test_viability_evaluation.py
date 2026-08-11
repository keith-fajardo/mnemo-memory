"""Unit coverage for lifecycle viability evaluation contracts and formulas."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from mnemo_memory.packages.application.evaluation import (
    ConditionId,
    DeterministicContinuationGrader,
    EvaluationBudget,
    EvaluationBudgetExceeded,
    EvaluationRunner,
    ScenarioCorpus,
    TokenAccount,
    aggregate_runs,
    bootstrap_mean_interval,
    break_even_reuse,
    build_condition_adapters,
    economic_scenarios,
    import_anonymized_trace,
    load_corpus,
    load_evaluation_config,
    long_term_memory_efficiency,
    memory_viability_score,
    pareto_frontier,
    task_impact,
    token_efficiency_score,
)
from mnemo_memory.packages.application.evaluation.models import MeasurementSource
from mnemo_memory.packages.application.evaluation.reporting import (
    render_comprehensive_report,
    verify_saved_run_log,
    write_evaluation_artifacts,
)
from mnemo_memory.packages.application.evaluation.runner import EvaluationBudgetLedger
from scripts.run_viability_evaluation import _source_tree_metadata

ROOT = Path(__file__).parents[2]
CORPUS_PATH = ROOT / "tests/fixtures/evals/viability-corpus-v1.json"
CONFIG_PATH = ROOT / "tests/fixtures/evals/viability-config-v1.json"


def _small_runner() -> EvaluationRunner:
    corpus = load_corpus(CORPUS_PATH)
    small_corpus = ScenarioCorpus(
        corpus.schema_version,
        corpus.corpus_version,
        corpus.provenance,
        (corpus.scenarios[0],),
    )
    config = replace(
        load_evaluation_config(CONFIG_PATH),
        reuse_counts=(1,),
        drift_cycles=2,
        bootstrap_samples=100,
    )
    return EvaluationRunner.offline(config, small_corpus)


def test_metric_formulas_and_gates() -> None:
    assert token_efficiency_score(1000, 600) == 0.4
    assert token_efficiency_score(1000, 1200) == -0.2
    assert break_even_reuse(100, 50, 500, 200) == 0.5
    assert break_even_reuse(100, 50, 200, 200) is None
    assert break_even_reuse(100, 50, 100, 200) is None
    gated, ungated = long_term_memory_efficiency(
        continuation_fidelity=1.0,
        required_knowledge_retention=1.0,
        temporal_supersession_accuracy=1.0,
        evidence_attribution_fidelity=1.0,
        drift_resistance=1.0,
        retrieval_f1=1.0,
        false_memory_rate=0.0,
        critical_violation=False,
    )
    assert gated == ungated == 1.0
    gated_failure, ungated_failure = long_term_memory_efficiency(
        continuation_fidelity=1.0,
        required_knowledge_retention=1.0,
        temporal_supersession_accuracy=1.0,
        evidence_attribution_fidelity=1.0,
        drift_resistance=1.0,
        retrieval_f1=1.0,
        false_memory_rate=0.0,
        critical_violation=True,
    )
    assert gated_failure == 0.0
    assert ungated_failure == 1.0
    assert task_impact(1.0, 1.0, 1.0, 1.0) == 1.0


def test_lifecycle_token_accounting_keeps_sources_separate() -> None:
    account = TokenAccount(
        agent_work_input=1,
        agent_work_output=2,
        baseline_context=3,
        baseline_compaction_input=4,
        baseline_compaction_output=5,
        checkpoint_save_input=6,
        checkpoint_save_output=7,
        validation=8,
        checkpoint_recall=9,
        retrieval_query=10,
        retrieved_evidence=11,
        retry_and_repair=12,
        cached=13,
        reasoning=14,
        source=MeasurementSource.PROVIDER_REPORTED,
        tokenizer_id="provider/example-v1",
    )
    assert account.total == sum(range(1, 15))
    assert account.input_total == 64
    assert account.output_total == 14
    assert account.to_dict()["source"] == "provider_reported"
    assert account.to_dict()["cached"] == 13
    assert account.to_dict()["reasoning"] == 14


def test_external_budget_is_fail_closed_and_enforced() -> None:
    denied = EvaluationBudgetLedger(EvaluationBudget(0, 0.0, 0.0))
    with pytest.raises(EvaluationBudgetExceeded, match="no explicit"):
        denied.reserve(expected_cost_usd=0.01)
    ledger = EvaluationBudgetLedger(EvaluationBudget(2, 1.0, 0.6))
    ledger.reserve(expected_cost_usd=0.4)
    ledger.reserve(expected_cost_usd=0.5)
    with pytest.raises(EvaluationBudgetExceeded, match="count"):
        ledger.reserve(expected_cost_usd=0.01)
    with pytest.raises(ValueError, match="negative"):
        ledger.reserve(expected_cost_usd=-0.01)


def test_corpus_has_required_horizons_workloads_and_challenges() -> None:
    corpus = load_corpus(CORPUS_PATH)
    assert len(corpus.scenarios) == 18
    assert {scenario.target_event_count for scenario in corpus.scenarios} == {15, 75, 225}
    assert len({scenario.category for scenario in corpus.scenarios}) == 6
    tags = {tag for scenario in corpus.scenarios for tag in scenario.challenge_tags}
    assert {
        "early_constraint",
        "supersession",
        "negation",
        "conditional_approval",
        "authority",
        "uncertainty",
        "failed_approach",
        "exact_literals",
        "noise",
        "multiple_goals",
        "fresh_session",
        "cross_agent",
        "scope_isolation",
        "obsolete_harm",
        "evidence_expansion",
    } <= tags
    assert all(scenario.ground_truth.critical_evidence for scenario in corpus.scenarios)


def test_paired_runner_counterbalances_conditions_and_marks_provider_missing() -> None:
    runner = _small_runner()
    runs = runner.run("unit-paired")
    assert {run.condition for run in runs} == set(ConditionId)
    assert len({run.order_index for run in runs}) == len(ConditionId)
    unavailable = next(run for run in runs if run.condition is ConditionId.PROVIDER_NATIVE)
    assert unavailable.available is False
    assert "not configured" in str(unavailable.unavailable_reason)
    assert unavailable.token_account.source is MeasurementSource.NOT_AVAILABLE


def test_mnemo_integrity_retrieval_scope_and_drift() -> None:
    runner = _small_runner()
    runs = runner.run("unit-integrity")
    m3 = next(run for run in runs if run.condition is ConditionId.MNEMO_RETRIEVAL)
    assert m3.grade is not None
    assert m3.grade.critical_violations == ()
    assert m3.grade.required_knowledge_retention == 1.0
    assert m3.grade.protected_span_fidelity == 1.0
    assert m3.grade.temporal_supersession_accuracy == 1.0
    assert m3.grade.evidence_attribution_fidelity == 1.0
    assert m3.grade.retrieval_f1 >= 0.88
    assert m3.grade.false_memory_rate == 0.0
    assert m3.grade.drift_resistance == 1.0
    assert m3.lme_gated is not None and m3.lme_gated >= 0.99
    scenario = runner.corpus.scenarios[0]
    adapter = next(
        item
        for item in build_condition_adapters(runner.config)
        if item.condition_id is ConditionId.MNEMO_RETRIEVAL
    )
    output = adapter.evaluate(scenario, 1)
    assert "CROSS-SCOPE-CANARY" not in output.context
    assert "Use an in-place schema migration because it appears simpler." not in output.context
    assert "EVIDENCE_EXPANSION" in output.context
    adaptive = next(run for run in runs if run.condition is ConditionId.MNEMO_ADAPTIVE)
    assert adaptive.grade is not None
    assert adaptive.grade.continuation_fidelity < m3.grade.continuation_fidelity
    assert "..." not in output.context


def test_answer_leakage_and_false_memory_close_integrity_gate() -> None:
    truth = load_corpus(CORPUS_PATH).scenarios[0].ground_truth
    grade = DeterministicContinuationGrader().grade(
        context='"critical_facts"\nMira approved deployment.',
        evidence_event_keys=(),
        ground_truth=truth,
        context_tokens=5,
        full_history_tokens=100,
        deterministic=True,
        drift_resistance=1.0,
        omission_notice_valid=True,
    )
    assert "evaluation_answer_leakage" in grade.critical_violations
    assert "fabricated_or_cross_scope_fact" in grade.critical_violations


def test_bootstrap_pareto_mvs_and_economic_sensitivity() -> None:
    first = bootstrap_mean_interval((1.0, 2.0, 3.0), samples=100, seed=7)
    second = bootstrap_mean_interval((1.0, 2.0, 3.0), samples=100, seed=7)
    assert first == second
    assert pareto_frontier(
        {
            "a": {"tokens": 10.0, "latency_ms": 1.0, "lme": 1.0, "success": 1.0},
            "b": {"tokens": 20.0, "latency_ms": 2.0, "lme": 0.5, "success": 0.5},
        }
    ) == ("a",)
    score = memory_viability_score(
        {"TE": 0.5, "LM": 1.0, "TI": 1.0, "EV": None, "MP": None, "OP": 0.5},
        production_gate=True,
    )
    assert score["complete_score"] is None
    assert score["missing_dimensions"] == ["EV", "MP"]
    config = load_evaluation_config(CONFIG_PATH)
    values = economic_scenarios(
        config.economic_assumptions,
        baseline_input_tokens=1000,
        baseline_output_tokens=100,
        candidate_input_tokens=500,
        candidate_output_tokens=100,
    )
    assert [item["name"] for item in values] == ["low", "expected", "high"]
    assert all(item["evidence_class"] == "estimated_assumption" for item in values)


def test_anonymized_trace_import_rejects_missing_attestation_and_secrets(tmp_path: Path) -> None:
    valid = tmp_path / "trace.jsonl"
    valid.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "schema_version": "mnemo-anonymized-trace/1.0",
                        "trace_id": "trace-001",
                        "anonymized": True,
                    }
                ),
                json.dumps({"source_event_key": "e1", "actor": "user", "summary": "goal: safe"}),
            )
        ),
        encoding="utf-8",
    )
    trace_id, events = import_anonymized_trace(valid)
    assert trace_id == "trace-001"
    assert events[0].summary == "goal: safe"
    unsafe = tmp_path / "unsafe.jsonl"
    unsafe.write_text(valid.read_text().replace("goal: safe", "password=not-a-real-secret"))
    with pytest.raises(ValueError, match="secret"):
        import_anonymized_trace(unsafe)


def test_partial_failures_are_retained_without_payloads() -> None:
    class FailingAdapter:
        condition_id = ConditionId.PROVIDER_NATIVE

        def evaluate(self, scenario: object, reuse_count: int) -> object:
            raise RuntimeError("sensitive provider response must not be logged")

    base = _small_runner()
    runner = EvaluationRunner(
        base.config,
        base.corpus,
        (FailingAdapter(),),  # type: ignore[arg-type]
        base.grader,
        base.tokenizer,
        base.budget_ledger,
    )
    run = runner.run("partial")[0]
    assert run.available is False
    assert run.execution_failure == "RuntimeError"
    assert run.unavailable_reason == "condition adapter failed"
    assert "sensitive" not in json.dumps(run.unavailable_reason)


def test_report_is_deterministic_and_saved_log_reaggregates(tmp_path: Path) -> None:
    runner = _small_runner()
    runs = runner.run("artifact-unit")
    aggregate = aggregate_runs(runs, runner.config)
    environment: dict[str, object] = {
        "evaluated_at": "2026-08-12T00:00:00+00:00",
        "evaluated_revision": "abc123",
        "worktree_state": "dirty",
        "fairness_control_digest": runner.fairness_control_digest,
    }
    chart_paths = tuple(f"charts/{index:02d}.svg" for index in range(1, 11))
    first = render_comprehensive_report(
        evaluation_run_id="artifact-unit",
        aggregate=aggregate,
        config=runner.config,
        corpus=runner.corpus,
        environment=environment,
        chart_paths=chart_paths,
    )
    second = render_comprehensive_report(
        evaluation_run_id="artifact-unit",
        aggregate=aggregate,
        config=runner.config,
        corpus=runner.corpus,
        environment=environment,
        chart_paths=chart_paths,
    )
    assert first == second
    destination = write_evaluation_artifacts(
        results_root=tmp_path,
        evaluation_run_id="artifact-unit",
        runs=runs,
        aggregate=aggregate,
        config=runner.config,
        corpus=runner.corpus,
        environment=environment,
        corpus_path=CORPUS_PATH,
        config_path=CONFIG_PATH,
    )
    assert verify_saved_run_log(destination / "raw-runs.jsonl", aggregate)
    assert len(tuple((destination / "charts").glob("*.svg"))) == 10
    assert (destination / "reproducibility-manifest.json").is_file()
    packet_lines = (destination / "blind-review-packets.jsonl").read_text().splitlines()
    key_lines = (destination / "human-review-key.jsonl").read_text().splitlines()
    assert len(packet_lines) == len([run for run in runs if run.available])
    assert len(key_lines) == len(packet_lines)
    assert all('"condition"' not in line for line in packet_lines)
    assert all('"condition"' in line for line in key_lines)
    with pytest.raises(FileExistsError):
        write_evaluation_artifacts(
            results_root=tmp_path,
            evaluation_run_id="artifact-unit",
            runs=runs,
            aggregate=aggregate,
            config=runner.config,
            corpus=runner.corpus,
            environment=environment,
            corpus_path=CORPUS_PATH,
            config_path=CONFIG_PATH,
        )


def test_evaluated_source_tree_identity_is_deterministic() -> None:
    first = _source_tree_metadata()
    assert first == _source_tree_metadata()
    assert str(first["sha256"]).startswith("sha256:")
    assert isinstance(first["file_count"], int)
    assert first["file_count"] > 100


def test_missing_market_and_live_usage_are_not_imputed() -> None:
    runner = _small_runner()
    aggregate = aggregate_runs(runner.run("missing-data"), runner.config)
    assert aggregate["market_pull"]["score"] is None
    assert aggregate["mvs"]["complete_score"] is None
    assert aggregate["operational_portability"]["live_model_families"] == 0
    assert aggregate["verdict"] == "INSUFFICIENT EVIDENCE"
    assert math.isfinite(float(aggregate["mvs"]["observed_dimensions_score"]))
