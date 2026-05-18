#!/usr/bin/env sh
# Copyright 2026 Firefly Software Solutions Inc
#
# Reject staged content that looks like a live Anthropic API key. Run
# from pre-commit; the hook stages the filenames as positional args.
set -eu

# Anthropic live key prefix. Catches anyone copy-pasting a real key into
# an example, a script, or a test fixture.
PATTERN='sk-ant-api[0-9]{2}-'

bad=0
for path in "$@"; do
  if grep -E -l "$PATTERN" "$path" >/dev/null 2>&1; then
    echo "error: $path contains an Anthropic API key prefix." >&2
    bad=1
  fi
done

exit "$bad"
