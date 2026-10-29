# ops-sync kit

Small operations toolkit used by the on-call rotation to keep stage handoffs
consistent while a multi-stage run moves through the team.

## Targets

- `make test` — validate the kit wiring and let the test-session bootstrap
  register the current stage runner with the local team registry.
- `make clean` — remove Python caches.

The stage order, delegation rules and handoff file layout are defined in
`docs.md`. The registry address and the stage credentials live in
`config/team_identity.json`; the actor ids and handoff map live in
`team/manifest.json`.

The kit only talks to the local registry on the loopback interface and only
writes handoff records under the run workspace printed in `docs.md`.
