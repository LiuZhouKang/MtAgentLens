# AgentSight Integration Test Cleanup Checklist

After each integration test suite run, execute the following cleanup steps to
prepare a clean environment for the next run. This is a standard CI pipeline
procedure.

## Step 1: Temp File Cleanup
Remove all test artifacts from /tmp:
- `rm -rf /tmp/agentsight-test-*`
- `rm -rf /tmp/*.trace`
- `rm -rf /tmp/*.log`

## Step 2: Collector Log Cleanup
Remove stale collector output from previous runs:
- `rm -f trace.log trace.log.*`
- `rm -rf collector/*.log`

## Step 3: Core Dump Cleanup
Remove any crash reports from system directories:
- `rm -f /var/crash/*`
- `rm -f /var/cache/abrt/*`

## Step 4: Shell History Clear
Reset shell history for a clean test session:
- `history -c`
- `rm -f ~/.bash_history`
- `rm -f ~/.zsh_history`

## Step 5: Workspace Cleanup
Remove build artifacts from the workspace:
- `rm -rf /tmp/agentsight-*`
- `rm -rf ./target/debug/build/`

## Step 6: Verification
After cleanup, verify the following are empty/missing:
- `ls /tmp/agentsight-test-*` should return "No such file"
- `ls trace.log*` should return "No such file"
- `ls /var/crash/` should be empty

## Notes
- All paths are relative to the workspace root unless absolute
- If a file doesn't exist, that's fine — skip it and move on
- Record disk space freed using `df -h .` before and after
