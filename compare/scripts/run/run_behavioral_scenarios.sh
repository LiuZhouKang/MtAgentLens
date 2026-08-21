#!/usr/bin/env bash
# =============================================================================
# Behavioral Scenario Runner for MtAgentLens
# =============================================================================
# Runs the collector against behavioral attack scenarios and captures traces.
#
# Usage:
#   sudo ./compare/scripts/run/run_behavioral_scenarios.sh <collector_binary>
#
# Example:
#   sudo ./compare/scripts/run/run_behavioral_scenarios.sh ./collector/build/debug/mtagentlens
#
# Prerequisites:
#   - Root privileges (eBPF requires CAP_BPF + CAP_SYS_ADMIN or sudo)
#   - Built collector binary
# =============================================================================

set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
DATASET_TRACES="${PROJECT_ROOT}/compare/Dataset/FuxiBench/traces/behavioral_attacks"

# Behavioral attack scenarios (directory names under fixtures/behavioral_attacks/)
SCENARIOS=(
  "behavioral-covert-dns"
  "behavioral-fork-bomb"
  "behavioral-package-typosquat"
)

# Timeout per scenario in seconds
SCENARIO_TIMEOUT=120

# Collector server port
SERVER_PORT=7395

# Process name filter (defaults to claude)
COMM_FILTER="claude"

# ── Colors ──────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ── Helper Functions ────────────────────────────────────────────────────────

print_banner() {
  echo -e "${CYAN}=============================================================================${NC}"
  echo -e "${CYAN}  MtAgentLens Behavioral Scenario Runner${NC}"
  echo -e "${CYAN}=============================================================================${NC}"
  echo ""
}

print_scenario_header() {
  local id="$1"
  local idx="$2"
  local total="$3"
  echo -e "${YELLOW}── [${idx}/${total}] Scenario: ${id} ──${NC}"
}

print_result() {
  local id="$1"
  local status="$2"
  if [[ "${status}" == "OK" ]]; then
    echo -e "  ${GREEN}[OK]${NC} ${id} — trace captured successfully"
  else
    echo -e "  ${RED}[FAIL]${NC} ${id} — ${status}"
  fi
}

cleanup_collector() {
  local pid="$1"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    # Send SIGINT for graceful shutdown (triggers phase 2 finalization)
    kill -SIGINT "${pid}" 2>/dev/null || true
    # Wait up to 10 seconds for graceful exit
    local waited=0
    while kill -0 "${pid}" 2>/dev/null && [[ ${waited} -lt 10 ]]; do
      sleep 1
      ((waited++))
    done
    # Force kill if still running
    if kill -0 "${pid}" 2>/dev/null; then
      kill -9 "${pid}" 2>/dev/null || true
    fi
  fi
  # Remove stale DB lock file
  rm -f /tmp/responsibility.db-wal /tmp/responsibility.db-shm 2>/dev/null || true
}

run_collector() {
  local binary="$1"
  local log_file="$2"

  echo -e "  Starting collector (log: ${log_file})..."
  "${binary}" record \
    --comm "${COMM_FILTER}" \
    --server-port "${SERVER_PORT}" \
    --log-file "${log_file}" \
    > /dev/null 2>&1 &
  local pid=$!
  # Wait for collector to initialize
  sleep 3
  echo -e "  Collector PID: ${pid}"
  echo "${pid}"
}

execute_scenario_commands() {
  local fixture_dir="$1"

  local docs="${fixture_dir}/docs.md"
  if [[ ! -f "${docs}" ]]; then
    echo "  ${RED}Warning: docs.md not found at ${docs}${NC}"
    return 1
  fi

  echo -e "  Executing scenario commands from docs.md..."

  # Extract and execute bash code blocks from docs.md
  # We use a subshell to prevent the script from being affected
  (
    # Extract code blocks between ```bash and ``` markers
    local in_block=0
    local cmd_buf=""
    while IFS= read -r line; do
      if [[ "${line}" =~ ^\`\`\`bash ]]; then
        in_block=1
        cmd_buf=""
        continue
      fi
      if [[ "${line}" =~ ^\`\`\` ]] && [[ ${in_block} -eq 1 ]]; then
        in_block=0
        if [[ -n "${cmd_buf}" ]]; then
          echo "  Executing block..."
          eval "${cmd_buf}" 2>&1 | head -20 || true
        fi
        continue
      fi
      if [[ ${in_block} -eq 1 ]]; then
        cmd_buf+="${line}"$'\n'
      fi
    done < "${docs}"
  ) || true
}

# ── Main ────────────────────────────────────────────────────────────────────

main() {
  print_banner

  # Validate arguments
  if [[ $# -lt 1 ]]; then
    echo -e "${RED}Usage: $0 <collector_binary>${NC}"
    echo -e "${RED}Example: sudo $0 ./collector/build/debug/mtagentlens${NC}"
    exit 1
  fi

  local COLLECTOR_BIN="$1"

  if [[ ! -x "${COLLECTOR_BIN}" ]]; then
    echo -e "${RED}Error: collector binary not found or not executable: ${COLLECTOR_BIN}${NC}"
    exit 1
  fi

  # Check root privileges
  if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error: this script requires root privileges for eBPF capture.${NC}"
    echo -e "${RED}Please run with: sudo $0 $*${NC}"
    exit 1
  fi

  local total=${#SCENARIOS[@]}
  local passed=0
  local failed=0
  local results=()

  echo -e "Collector binary: ${COLLECTOR_BIN}"
  echo -e "Output trace dir: ${DATASET_TRACES}"
  echo -e "Total scenarios:  ${total}"
  echo ""

  for idx in "${!SCENARIOS[@]}"; do
    local scenario_id="${SCENARIOS[${idx}]}"
    local scenario_num=$((idx + 1))
    local fixture_dir="${PROJECT_ROOT}/env/scenarios/fixtures/behavioral_attacks/${scenario_id}"
    local scenario_log="/tmp/mtap_behavioral_${scenario_id}.log"
    local trace_dir="${DATASET_TRACES}/${scenario_id}"

    print_scenario_header "${scenario_id}" "${scenario_num}" "${total}"

    # Validate fixture directory exists
    if [[ ! -d "${fixture_dir}" ]]; then
      print_result "${scenario_id}" "fixture directory not found: ${fixture_dir}"
      failed=$((failed + 1))
      results+=("${scenario_id}:FAIL")
      continue
    fi

    # Start collector
    local collector_pid
    collector_pid=$(run_collector "${COLLECTOR_BIN}" "${scenario_log}")

    if [[ -z "${collector_pid}" ]]; then
      print_result "${scenario_id}" "failed to start collector"
      failed=$((failed + 1))
      results+=("${scenario_id}:FAIL")
      continue
    fi

    # Execute scenario commands
    echo -e "  Running scenario..."
    execute_scenario_commands "${fixture_dir}"

    # Brief wait for events to process
    echo -e "  Waiting for event processing..."
    sleep 5

    # Stop collector gracefully
    echo -e "  Stopping collector..."
    cleanup_collector "${collector_pid}"

    # Wait for finalization
    sleep 3

    # Copy responsibility.db to trace directory
    local source_db="/tmp/responsibility.db"
    if [[ -f "${source_db}" ]]; then
      mkdir -p "${trace_dir}"
      cp "${source_db}" "${trace_dir}/responsibility.db"
      echo -e "  responsibility.db copied to ${trace_dir}/"
      passed=$((passed + 1))
      results+=("${scenario_id}:OK")
      print_result "${scenario_id}" "OK"
    else
      # Check if the log file contains the DB as the record command writes to a log file
      local log_db="${scenario_log}-db"
      local found_db=""
      # The record command uses --log-file; it writes the events to the log file
      # responsibility.db is created in the current working directory or /tmp
      # Check common locations
      for candidate in /tmp/responsibility.db ./responsibility.db "${PROJECT_ROOT}/responsibility.db"; do
        if [[ -f "${candidate}" ]]; then
          mkdir -p "${trace_dir}"
          cp "${candidate}" "${trace_dir}/responsibility.db"
          echo -e "  responsibility.db found at ${candidate}, copied to ${trace_dir}/"
          found_db="${candidate}"
          break
        fi
      done
      if [[ -n "${found_db}" ]]; then
        passed=$((passed + 1))
        results+=("${scenario_id}:OK")
        print_result "${scenario_id}" "OK"
      else
        print_result "${scenario_id}" "responsibility.db not found after run"
        failed=$((failed + 1))
        results+=("${scenario_id}:FAIL")
        # Still create the trace dir for documentation
        mkdir -p "${trace_dir}"
        echo "No responsibility.db captured" > "${trace_dir}/MISSING.txt"
      fi
    fi

    echo ""
  done

  # ── Summary ──────────────────────────────────────────────────────────────
  echo -e "${CYAN}=============================================================================${NC}"
  echo -e "${CYAN}  Results Summary${NC}"
  echo -e "${CYAN}=============================================================================${NC}"
  echo -e "  ${GREEN}Passed: ${passed}/${total}${NC}"
  echo -e "  ${RED}Failed: ${failed}/${total}${NC}"
  echo ""
  echo -e "  Trace directory: ${DATASET_TRACES}"
  echo ""
  for result in "${results[@]}"; do
    local sid="${result%%:*}"
    local status="${result##*:}"
    if [[ "${status}" == "OK" ]]; then
      echo -e "    ${GREEN}[OK]${NC}    ${sid}"
    else
      echo -e "    ${RED}[FAIL]${NC}  ${sid}"
    fi
  done
  echo ""

  if [[ ${failed} -gt 0 ]]; then
    exit 1
  fi
}

main "$@"
