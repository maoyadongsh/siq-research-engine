#!/usr/bin/env bash
# Shared helpers for terminating only verified SIQ OpenShell child processes.

siq_openshell_terminate_matching_pid() {
  local pid="$1" matcher="$2" description="$3" attempts="${4:-40}"

  [[ "$pid" =~ ^[0-9]+$ ]] || {
    printf 'Invalid PID for %s: %s\n' "$description" "$pid" >&2
    return 2
  }
  "$matcher" "$pid" || {
    printf 'Refusing to signal an unverified %s PID: %s\n' "$description" "$pid" >&2
    return 2
  }
  kill -TERM -- "$pid" || {
    printf 'Failed to signal %s PID: %s\n' "$description" "$pid" >&2
    return 2
  }
  for _ in $(seq 1 "$attempts"); do
    "$matcher" "$pid" || return 0
    sleep 0.1
  done
  printf '%s process did not stop cleanly: %s\n' "$description" "$pid" >&2
  return 2
}

siq_openshell_sandbox_name_exists() {
  local run_cli="$1" sandbox_name="$2" output
  output="$("$run_cli" sandbox list 2>/dev/null)" || return 2
  sed -r 's/\x1B\[[0-9;]*[mK]//g' <<<"$output" \
    | awk '{print $1}' \
    | grep -Fxq "$sandbox_name"
}

siq_openshell_managed_sandbox_container_ids() {
  local sandbox_name="$1" namespace="$2"
  docker ps -aq \
    --filter 'label=openshell.ai/managed-by=openshell' \
    --filter "label=openshell.ai/sandbox-namespace=$namespace" \
    --filter "label=openshell.ai/sandbox-name=$sandbox_name"
}

siq_openshell_verified_sandbox_container_id() {
  local run_cli="$1" sandbox_name="$2" namespace="$3" label_key="$4" label_value="$5"
  local output clean gateway_id gateway_name label_matches ids

  output="$("$run_cli" sandbox get "$sandbox_name" 2>/dev/null)" || return 2
  clean="$(sed -r 's/\x1B\[[0-9;]*[mK]//g' <<<"$output")"
  gateway_id="$(awk '$1 == "Id:" {print $2; exit}' <<<"$clean")"
  gateway_name="$(awk '$1 == "Name:" {print $2; exit}' <<<"$clean")"
  label_matches="$(awk -v key="$label_key:" -v value="$label_value" \
    '$1 == key && $2 == value && NF == 2 {count++} END {print count + 0}' <<<"$clean")"
  [[ "$gateway_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || return 2
  [[ "$gateway_name" == "$sandbox_name" && "$label_matches" -eq 1 ]] || return 2

  ids="$(docker ps -aq \
    --filter 'label=openshell.ai/managed-by=openshell' \
    --filter "label=openshell.ai/sandbox-namespace=$namespace" \
    --filter "label=openshell.ai/sandbox-name=$sandbox_name" \
    --filter "label=openshell.ai/sandbox-id=$gateway_id")" || return 2
  [[ -n "$ids" && "$ids" != *$'\n'* ]] || return 2
  printf '%s\n' "$ids"
}
