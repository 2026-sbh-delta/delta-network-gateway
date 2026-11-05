#!/bin/sh
set -eu
export PGPASSWORD="$(cat /run/secrets/db_password)"
test -n "$PGPASSWORD"
exec "$@"
