#!/bin/sh
set -eu
test -f "$(dirname "$0")/detail.md"
grep -q RESOURCE_SENTINEL_NOTION_20260920 "$(dirname "$0")/detail.md"
printf 'NOTION_PACKAGE_OK\n'
