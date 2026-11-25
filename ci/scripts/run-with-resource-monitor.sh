#!/usr/bin/env bash
# Keep resource evidence and a heartbeat while a foreground build runs.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
source "$root/ci/scripts/build-jobs.sh"
stage=${1:?Usage: run-with-resource-monitor.sh STAGE COMMAND [ARG ...]}
shift
[[ $# -gt 0 ]] || { echo 'A build command is required' >&2; exit 2; }
dist=${FLORAL_DIST:-"$root/.ci-dist/$stage"}
mkdir -p "$dist"
resource_log="$dist/resource-monitor.log"
: > "$resource_log"

resource_snapshot() {
  printf '\nRESOURCE_SNAPSHOT %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'Configured make jobs: %s; online CPUs: ' "${FLORAL_JOBS:-unset}"
  nproc
  free -m
  swapon --show --bytes
  df -h "$root" /tmp
  printf '\nLargest resident processes (RSS in KiB):\n'
  ps -eo pid,ppid,comm,rss,%cpu,etimes --sort=-rss | sed -n '1,16p'
  printf '\nLLD processes (RSS in KiB):\n'
  ps -C ld.lld -o pid,ppid,comm,rss,%cpu,etimes || true
  printf '\nCurrent cgroup membership:\n'
  cat /proc/self/cgroup
  local group_path group_dir metric
  group_path=$(awk -F: '$1 == "0" {print $3}' /proc/self/cgroup)
  group_dir="/sys/fs/cgroup${group_path:-/}"
  for metric in memory.current memory.peak memory.max memory.events memory.events.local \
    memory.swap.current memory.swap.peak memory.swap.max memory.pressure cpu.pressure io.pressure; do
    if [[ -r "$group_dir/$metric" ]]; then
      printf '\n%s:\n' "$group_dir/$metric"
      cat "$group_dir/$metric"
    fi
  done
  for metric in memory cpu io; do
    if [[ -r "/proc/pressure/$metric" ]]; then
      printf '\n/proc/pressure/%s:\n' "$metric"
      cat "/proc/pressure/$metric"
    fi
  done
  printf '\nRecent kernel resource errors, if any:\n'
  sudo -n dmesg --ctime 2>/dev/null | grep -Ei 'out of memory|oom-kill|killed process|I/O error' | tail -n 12 || true
}

(
  set +e
  sleep_pid=
  trap '[[ -z "$sleep_pid" ]] || kill "$sleep_pid" 2>/dev/null; exit 0' TERM INT
  while :; do
    resource_snapshot 2>&1 | tee -a "$resource_log"
    sleep 30 &
    sleep_pid=$!
    wait "$sleep_pid"
    sleep_pid=
  done
) &
monitor_pid=$!

cleanup() {
  local status=$?
  trap - EXIT
  kill "$monitor_pid" 2>/dev/null || true
  wait "$monitor_pid" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT
"$@"
