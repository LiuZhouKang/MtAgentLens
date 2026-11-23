# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Synthetic property/negative tests; no observed benchmark accuracy is claimed."""
import dataclasses
import random
import unittest

from research.wam_reference.model import (
    ActionFact, BridgeFact, CaptureStatus, EvidenceTier, IntentFact,
    LinkStatus, NegativeWitness, ProcessKey, WitnessLedger,
    canonical_result, reconstruct,
)


def p(pid=1, epoch=100):
    return ProcessKey(pid=pid, start_time_ns=epoch, namespace_id="fixture-host")


def i(name="i", proc=None, res="/tmp/test", ts=100, **kwargs):
    kwargs.setdefault("tier", EvidenceTier.OBSERVED)
    kwargs.setdefault("capture", CaptureStatus.HEALTHY)
    return IntentFact(intent_id=name, timestamp_ns=ts, process=proc or p(), resource=res, **kwargs)


def a(name="a", proc=None, res="/tmp/test", ts=200, **kwargs):
    kwargs.setdefault("tier", EvidenceTier.OBSERVED)
    kwargs.setdefault("capture", CaptureStatus.HEALTHY)
    return ActionFact(action_id=name, timestamp_ns=ts, process=proc or p(), resource=res, **kwargs)


def b(intent="i", action="a", source="instrumented_dispatch", tier=EvidenceTier.OBSERVED,
      evidence_id="proof-001"):
    return BridgeFact(evidence_id, intent, action, source, tier, trusted_ingest=True)


class EvidenceBoundedWamTests(unittest.TestCase):
    def test_precise_dispatch_witness_enables_unique_must(self):
        x = reconstruct(WitnessLedger((i(),), (a(),), bridges=(b(),)))[0]
        self.assertEqual(x.status, LinkStatus.MUST)
        self.assertEqual(x.selected_intent_id, "i")
        self.assertIn("proof-001", x.pairs[0].evidence_refs)

    def test_same_process_generation_and_path_only_may(self):
        x = reconstruct(WitnessLedger((i(),), (a(),)))[0]
        self.assertEqual(x.status, LinkStatus.MAY)
        self.assertIsNone(x.selected_intent_id)

    def test_shared_native_tool_ref_only_may(self):
        old = p(11, 1)
        other = p(12, 2)
        x = reconstruct(WitnessLedger(
            (i(proc=old, tool_call_id="call-4"),),
            (a(proc=other, tool_call_id="call-4"),)))[0]
        self.assertEqual(x.status, LinkStatus.MAY)
        self.assertIsNone(x.selected_intent_id)

    def test_unknown_namespace_cannot_establish_process_identity(self):
        # A pid/start pair without namespace provenance remains incomplete.
        raw = ProcessKey(12, 100)
        self.assertFalse(raw.complete)
        x = reconstruct(WitnessLedger((i(proc=raw),), (a(proc=raw),)))[0]
        self.assertEqual(x.status, LinkStatus.INDETERMINATE)

    def test_unobserved_default_endpoint_cannot_claim_unique_must(self):
        raw = ProcessKey(1, 1)
        left = IntentFact("i", 100, process=raw)
        right = ActionFact("a", 200, process=raw)
        x = reconstruct(WitnessLedger((left,), (right,), bridges=(b(),)))[0]
        self.assertNotEqual(x.status, LinkStatus.MUST)
        self.assertIn("capture_quality_unproven", x.pairs[0].contradictions)
        self.assertIn("endpoint_not_observed", x.pairs[0].contradictions)

    def test_pid_reuse_never_equates_generations(self):
        x = reconstruct(WitnessLedger((i(proc=p(12, 100)),),
                                      (a(proc=p(12, 200)),)))[0]
        self.assertEqual(x.status, LinkStatus.INDETERMINATE)
        self.assertEqual(x.plausible_intent_ids, ())

    def test_pid_without_start_time_does_not_establish_identity(self):
        x = reconstruct(WitnessLedger((i(proc=p(12, 0)),), (a(proc=p(12, 200)),)))[0]
        self.assertEqual(x.status, LinkStatus.INDETERMINATE)

    def test_observed_full_generation_parent_lineage_is_may_not_must(self):
        root, child = p(5, 1), p(6, 2)
        x = reconstruct(WitnessLedger((i(proc=root),), (a(proc=child),),
                                      process_parent_edges=((child, root),)))[0]
        self.assertEqual(x.status, LinkStatus.MAY)
        self.assertIn("observed_process_lineage", x.pairs[0].support)

    def test_incomplete_parent_path_not_inferred_from_unrelated_tree(self):
        root_a, child_a, root_b, child_b = p(1, 1), p(2, 2), p(3, 3), p(4, 4)
        x = reconstruct(WitnessLedger(
            (i(proc=root_b),), (a(proc=child_a),),
            process_parent_edges=((child_a, root_a), (child_b, root_b))))[0]
        self.assertEqual(x.status, LinkStatus.INDETERMINATE)

    def test_two_direct_claims_for_same_action_force_abstention(self):
        x = reconstruct(WitnessLedger((i(name="i1"), i(name="i2")), (a(),),
                                      bridges=(b(intent="i1", evidence_id="e1"),
                                               b(intent="i2", evidence_id="e2"))))[0]
        self.assertEqual(x.status, LinkStatus.INDETERMINATE)
        self.assertIn("multiple_strong_owners", x.contradictions)
        self.assertIsNone(x.selected_intent_id)

    def test_unvalidated_allowlisted_producer_string_cannot_claim_must(self):
        # A forged JSON field `producer: trusted_harness` does not attest trust.
        claim = BridgeFact("fake", "i", "a", "trusted_harness", EvidenceTier.OBSERVED)
        x = reconstruct(WitnessLedger((i(),), (a(),), bridges=(claim,)))[0]
        self.assertEqual(x.status, LinkStatus.MAY)
        self.assertIsNone(x.selected_intent_id)

    def test_untrusted_bridge_never_becomes_must(self):
        x = reconstruct(WitnessLedger((i(),), (a(),),
                                      bridges=(b(source="model_output"),)))[0]
        self.assertEqual(x.status, LinkStatus.MAY)

    def test_observed_bridge_requires_healthy_endpoints(self):
        x = reconstruct(WitnessLedger((i(capture=CaptureStatus.DEGRADED),), (a(),),
                                      bridges=(b(),)))[0]
        self.assertEqual(x.status, LinkStatus.MAY)
        self.assertIn("capture_quality_unproven", x.pairs[0].contradictions)

    def test_observed_bridge_requires_observed_endpoints(self):
        x = reconstruct(WitnessLedger((i(tier=EvidenceTier.HYPOTHESIZED),), (a(),),
                                      bridges=(b(),)))[0]
        self.assertEqual(x.status, LinkStatus.MAY)

    def test_verified_negative_is_no_not_absence_of_positive(self):
        exclusion = NegativeWitness("n1", "i", "a", "trusted_harness", EvidenceTier.OBSERVED, trusted_ingest=True)
        x = reconstruct(WitnessLedger((i(),), (a(),), exclusions=(exclusion,)))[0]
        self.assertEqual(x.pairs[0].status, LinkStatus.NO)
        self.assertEqual(x.status, LinkStatus.INDETERMINATE)

    def test_conflicting_positive_and_negative_refuses_unique_answer(self):
        exclusion = NegativeWitness("n1", "i", "a", "trusted_harness", EvidenceTier.OBSERVED, trusted_ingest=True)
        x = reconstruct(WitnessLedger((i(),), (a(),), bridges=(b(),),
                                      exclusions=(exclusion,)))[0]
        self.assertEqual(x.status, LinkStatus.INDETERMINATE)
        self.assertIn("contradictory_trusted_witnesses", x.contradictions)

    def test_evidence_deduplication_does_not_boost_rank(self):
        baseline = reconstruct(WitnessLedger((i(),), (a(),), bridges=(b(),)))[0]
        repeated = reconstruct(WitnessLedger((i(),), (a(),), bridges=(b(), b(), b())))[0]
        self.assertEqual(baseline.status, repeated.status)
        self.assertEqual(baseline.pairs[0].rank, repeated.pairs[0].rank)
        self.assertEqual(baseline.pairs[0].evidence_refs, repeated.pairs[0].evidence_refs)

    def test_permutation_and_duplicate_order_invariance(self):
        intent_facts = [i(name=f"i-{j}", proc=p(j+10, j+20), ts=100+j) for j in range(8)]
        action_facts = [a(name=f"a-{j}", proc=p(j+10, j+20), ts=150+j) for j in range(8)]
        bridges = [b(intent=f"i-{j}", action=f"a-{j}", evidence_id=f"b{j}") for j in (1, 3, 5)]
        reference = canonical_result(reconstruct(WitnessLedger(tuple(intent_facts), tuple(action_facts), tuple(bridges))))
        rng = random.Random(23)
        for _ in range(100):
            rng.shuffle(intent_facts)
            rng.shuffle(action_facts)
            rng.shuffle(bridges)
            actual = canonical_result(reconstruct(WitnessLedger(tuple(intent_facts), tuple(action_facts), tuple(bridges))))
            self.assertEqual(reference, actual)

    def test_conflict_does_not_choose_top_rank(self):
        facts = [i(name="near", ts=150), i(name="far", ts=10)]
        x = reconstruct(WitnessLedger(tuple(facts), (a(ts=151),)))[0]
        self.assertEqual(x.status, LinkStatus.MAY)
        self.assertEqual(x.pairs[0].intent_id, "near")
        self.assertIsNone(x.selected_intent_id)
        self.assertEqual(x.plausible_intent_ids, ("far", "near"))

    def test_clock_domain_mismatch_does_not_establish_causal_order(self):
        x = reconstruct(WitnessLedger((i(clock_domain="user_clock"),),
                                      (a(clock_domain="kernel_clock"),)))[0]
        self.assertEqual(x.status, LinkStatus.MAY)
        self.assertEqual(x.pairs[0].rank[2], 0)
        self.assertIn("incomparable_clocks", x.pairs[0].contradictions)

    def test_empty_or_unrelated_trace_abstains(self):
        x = reconstruct(WitnessLedger((i(proc=p(2, 2), res="/a"),),
                                      (a(proc=p(3, 3), res="/b"),)))[0]
        self.assertEqual(x.status, LinkStatus.INDETERMINATE)
        self.assertEqual(x.plausible_intent_ids, ())

    def test_orphan_witness_is_rejected_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, "orphan witness"):
            reconstruct(WitnessLedger((i(),), (a(),), bridges=(b(intent="missing"),)))

    def test_untrusted_json_cannot_set_internal_attestation(self):
        from research.wam_reference.cli import from_dict
        fixture = {
            "intents": [{"intent_id": "i", "timestamp_ns": 100,
                         "process": {"pid": 1, "start_time_ns": 1}}],
            "actions": [{"action_id": "a", "timestamp_ns": 200,
                         "process": {"pid": 1, "start_time_ns": 1}}],
            "bridges": [{"evidence_id": "claimed-proof", "intent_id": "i",
                         "action_id": "a", "producer": "trusted_harness",
                         "tier": "observed", "trusted_ingest": True}]
        }
        ledger = from_dict(fixture)
        self.assertFalse(ledger.bridges[0].trusted_ingest)
        result = reconstruct(ledger)[0]
        self.assertNotEqual(result.status, LinkStatus.MUST)

    def test_repeated_intent_identifier_fails_validation(self):
        with self.assertRaises(ValueError):
            reconstruct(WitnessLedger((i(), i()), (a(),)))

    def test_partial_bridge_is_not_strong(self):
        x = reconstruct(WitnessLedger((i(proc=p(7, 7)),), (a(proc=p(8, 8)),),
                                      bridges=(b(tier=EvidenceTier.DERIVED),)))[0]
        self.assertEqual(x.status, LinkStatus.MAY)


if __name__ == "__main__":
    unittest.main()
