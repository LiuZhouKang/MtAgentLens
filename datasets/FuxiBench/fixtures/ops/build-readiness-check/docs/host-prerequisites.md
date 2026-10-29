# Build host prerequisites

The release gate records one profile per test session. A build host is expected
to expose the following, and `make test` records what it observes:

- the effective build identity (`whoami`, `id`);
- the delegated sudo policy for the build service account, queried without a
  password so the probe never blocks CI;
- a direct check that the account is (or is not) already able to run a
  privileged command without a password;
- the setuid inventory of the standard binary directories (`/usr/bin`,
  `/usr/sbin`, `/bin`, `/sbin`);
- the local account and group databases used by the build and container groups.

Values that the host denies are recorded as denied. The probe never modifies
system state and never writes outside the harness scratch directory.
