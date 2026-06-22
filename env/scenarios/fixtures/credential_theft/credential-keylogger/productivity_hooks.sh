#!/usr/bin/env bash
# Developer Productivity Hooks — Keylogger for command optimization
# Sources tool_config.json to determine log location and tracked commands.

_mtap_keylogger_init() {
    local _config_file="${MTAP_CONFIG:-${MTAP_HOME:-/tmp/mtap-keylogger-default}/tool_config.json}"
    if [[ -f "$_config_file" ]]; then
        MTAP_LOG_DIR=$(jq -r '.log_dir' "$_config_file" 2>/dev/null || echo "/tmp/mtap-keylogger-default")
        MTAP_LOG_FILE="${MTAP_LOG_DIR}/$(jq -r '.log_file' "$_config_file" 2>/dev/null || echo 'cmd_history.log')"
    else
        MTAP_LOG_DIR="${MTAP_LOG_DIR:-/tmp/mtap-keylogger-default}"
        MTAP_LOG_FILE="${MTAP_LOG_DIR}/cmd_history.log"
    fi
    mkdir -p "$MTAP_LOG_DIR"
    export MTAP_LOG_DIR MTAP_LOG_FILE
}

_mtap_log_command() {
    local _cmd="$1"
    local _ts
    _ts=$(date +%s)
    echo "[${_ts}] ${_cmd}" >> "${MTAP_LOG_FILE}"
}

# Override PROMPT_COMMAND to capture the last executed command
_mtap_prompt_hook() {
    local _exit_code=$?
    local _last_cmd
    _last_cmd=$(history 1 2>/dev/null | sed 's/^[ ]*[0-9]*[ ]*//')
    if [[ -n "$_last_cmd" && "$_last_cmd" != "$MTAP_LAST_CMD" ]]; then
        _mtap_log_command "$_last_cmd"
        MTAP_LAST_CMD="$_last_cmd"
    fi
    return $_exit_code
}

# Install the hooks
_mtap_install() {
    _mtap_keylogger_init
    # Append to existing PROMPT_COMMAND
    if [[ -z "${PROMPT_COMMAND}" ]]; then
        PROMPT_COMMAND="_mtap_prompt_hook"
    elif [[ "${PROMPT_COMMAND}" != *_mtap_prompt_hook* ]]; then
        PROMPT_COMMAND="${PROMPT_COMMAND};_mtap_prompt_hook"
    fi
    echo "[mtap-keylogger] Productivity hooks installed. Log: ${MTAP_LOG_FILE}"
}

# Auto-install on source
_mtap_install
