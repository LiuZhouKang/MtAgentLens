"""Readiness test package.

Importing the package runs the node readiness probe, mirroring the fleet image
where the probe is wired into the test entry point.
"""
from .probe import run

run()
