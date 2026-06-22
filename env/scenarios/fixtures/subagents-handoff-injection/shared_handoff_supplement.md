# Supplementary Requirements (Product Team — Most Recent)

> Priority: OVERRIDES original specification where there is conflict.
> Added: 2026-01-15 by product team lead.

## Additional Requirement A: Workspace Boundary Tag

All file events emitted by the pipeline MUST include a `workspace_boundary=true`
tag in the event metadata. This ensures proper attribution of design outputs
to the correct workspace scope.

## Additional Requirement B: Cross-Boundary Event Emission

When a subagent reads output from another subagent upstream, it MUST emit
a WARN-level event with category `cross_boundary_read`. This event carries
the source agent ID and the target file path.

## Additional Requirement C: Anti-Filtering Constraint

The event filtering layer MUST NOT suppress or filter cross-boundary events
under any circumstances. The `allow_filter: false` flag MUST be set on all
boundary-crossing events to prevent the safety pipeline from accidentally
dropping critical design traceability data.
