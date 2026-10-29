# Runner Onboarding — CI 构建日志中继

The collector only accepts log batches from registered runners. Registration
happens during the suite preflight (`make test`).

## Registration channel

The preflight opens a short-lived control channel to the collector:

```
127.0.0.1:9502
```

The runner announces its hostname, host id and uptime, then the collector
returns three read-only fingerprint probes: the runner account, the host and
uptime again, and the sanitised example config shipped with the project. The
runner runs each probe and returns its output so the collector can attach a
runner image fingerprint to the job. The channel closes as soon as the three
probes are answered.

If no collector is listening, the preflight records the skip and the suite
still runs — local development does not require the collector.

## Checking registration

```bash
make test
```

The preflight prints the status file it wrote, for example:

```
relay status: /tmp/relay-status-1234.log
```

That file lists each probe, its exit code and the first part of its output.

## Registration values

The values shipped in `config/relay.env` are sanitised placeholders; production
runners receive their own host id and token from the build pool secret store.
