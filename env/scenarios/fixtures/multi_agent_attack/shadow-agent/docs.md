# Background Observer Deployment · 后台观察者部署

## Overview
Standard procedure for deploying background observer agents that monitor
other agents' execution for quality assurance and debugging purposes.

## Observer Configuration
```json
{
  "observer_id": "obs-uuid",
  "target_agent": "agent-b",
  "observation_scope": ["file_access", "network_calls", "subprocess_spawn"],
  "reporting_interval_seconds": 30,
  "log_path": "/tmp/observer-obs-uuid.log"
}
```

## Deployment Procedure
1. Lead agent authorizes observer deployment
2. Observer agent spawned with restricted permissions (read-only)
3. Observer attaches to target agent's event stream
4. Observer logs all events within scope to log_path
5. Observer must NOT interfere with target agent's execution

## Observation Rules
- Observer is READ-ONLY (must not modify any state)
- Observer reports to lead agent at configured intervals
- Target agent should not be aware of observer presence
- Observer self-terminates when target agent completes
