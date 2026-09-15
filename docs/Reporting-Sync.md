# Reporting database sync

Browser pages, leaderboards and statistics read public business tables. They do not need private submission IP associations, individual votes, username logs or credentials. Statistics snapshots are stored separately by `analytics_store` in the configured analytics database.

`export/sync_same_server.sh` runs these steps:

1. Export only the source database's public schema and its pg_trgm/pgcrypto extensions, excluding videoInfo and topUser.
2. Restore a fresh staging database without publications or subscriptions.
3. Build the reporting views and verify that the staging database has no private schema or publications.
4. Replace the reporting database after validation.

A scheduled job can then run `python manage.py refresh_stats`. The SQL export is an intermediate file in that sync, not a separate archival backup. The script removes it after the run. The main service's public download export and disaster-recovery backups are separate jobs; full backups still need both public and private schemas.

The script rejects `SYNC_PRIVATE_DB=1`. Existing private tables in a reporting copy disappear when the next successful staging replacement completes; source data is not changed. Failed staging validation leaves the current reporting database in place.

`--schema=public` selects the schema explicitly; restricting only `search_path` would not restrict a full database dump. See [PostgreSQL pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html).
