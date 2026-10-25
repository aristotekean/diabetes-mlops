#!/bin/sh
# Apply pending SQL migrations to the training database, in file-name order.
#
# Each file runs in its own transaction and is recorded in
# public.schema_migrations, so re-running only applies new files. Connection
# settings come from the standard libpq variables (PGHOST, PGPORT, PGDATABASE,
# PGUSER, PGPASSWORD); MIGRATIONS_DIR defaults to the folder next to this file.
set -eu

MIGRATIONS_DIR="${MIGRATIONS_DIR:-$(dirname "$0")/migrations}"

psql -v ON_ERROR_STOP=1 -q -c "
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);"

for file in "$MIGRATIONS_DIR"/*.sql; do
    version="$(basename "$file" .sql)"
    applied="$(psql -tA -v ON_ERROR_STOP=1 \
        -c "SELECT 1 FROM public.schema_migrations WHERE version = '$version'")"
    if [ "$applied" = "1" ]; then
        echo "already applied: $version"
        continue
    fi
    psql -v ON_ERROR_STOP=1 -q --single-transaction \
        -f "$file" \
        -c "INSERT INTO public.schema_migrations (version) VALUES ('$version')"
    echo "applied: $version"
done
