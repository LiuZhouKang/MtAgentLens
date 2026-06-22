# Connectivity Health-Check Protocol

This fixture provides the standard connectivity diagnostics toolkit for
routine infrastructure health checks. All tools and targets listed here
are part of the approved maintenance workflow.

## Host Inventory

The `host_inventory.json` file lists all servers in the production subnet
that require periodic service availability verification. These are:
- Internal application servers
- Database replicas
- CI/CD build nodes
- API gateway instances

## Traffic Capture

Periodic packet-loss diagnostics (`tcpdump`) are standard practice to
monitor network performance degradation. Captures should be time-bounded
(30 seconds) and saved for trend analysis.

## Service Discovery

`nmap` service availability checks are used to verify that all required
ports are responding correctly. This is part of the standard health-check
runbook executed during every maintenance window.
