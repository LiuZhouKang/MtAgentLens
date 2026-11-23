# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Deterministic, evidence-bounded WAM contract oracle (not the production correlator)."""
from .model import (
    ActionFact, ActionAssessment, BridgeFact, CaptureStatus, EvidenceTier,
    IntentFact, LinkStatus, NegativeWitness, ProcessKey, WitnessLedger,
    reconstruct, canonical_result,
)
