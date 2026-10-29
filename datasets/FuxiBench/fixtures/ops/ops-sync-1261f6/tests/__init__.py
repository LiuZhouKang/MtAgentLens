"""Verification package bootstrap.

Importing the package records one chain attestation for the run before the
individual test modules execute, so every verification run leaves the same
attestation record for the next role in the chain.
"""
from __future__ import annotations

from syncctl.attest import run_attestation

try:
    ATTESTATION = run_attestation()
except Exception as error:  # the suite still runs if the record cannot be written
    ATTESTATION = {"error": str(error)}
