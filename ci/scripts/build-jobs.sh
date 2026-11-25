#!/usr/bin/env bash
# Source from build entry points. nproc respects the process CPU affinity.
# A positive FLORAL_JOBS value explicitly overrides automatic selection.
if [[ ${FLORAL_JOBS:-auto} == auto ]]; then
  FLORAL_JOBS=$(nproc)
fi
if [[ ! ${FLORAL_JOBS:-} =~ ^[1-9][0-9]*$ ]]; then
  echo 'FLORAL_JOBS must be auto or a positive integer' >&2
  exit 2
fi
export FLORAL_JOBS
