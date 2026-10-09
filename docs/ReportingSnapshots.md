# Reporting snapshots

The snapshot deployment uses two PostgreSQL databases, `bsb_reporting_a` and
`bsb_reporting_b`, and a PgBouncer database alias named `browser_data`.
One database is frozen for queries. The other receives changes through logical
replication. PostgreSQL supplies replication; the deployment scripts manage
freezing and rotation.

## Initial setup

`export/prepare_reporting_replication.py configure` creates the reporting roles,
an explicit publication of public tables, database schemas, disabled
subscriptions, and the PgBouncer configuration. Private schemas and `videoInfo`
are excluded. Credentials are generated on the host and stored in protected
files under `/etc/bsb-reporting`; do not commit or copy these files into reports.

Initial replication runs sequentially through
`bsb-reporting-bootstrap.service`. Each database retains primary keys and
constraint indexes during its initial copy. Other indexes are built serially
after copying, avoiding repeated secondary-index maintenance during the bulk
load. Subscriptions on the same PostgreSQL cluster use separately created
replication slots.

The minimum background worker capacity for this sequential initialization is
three: the logical replication launcher, one apply worker, and one table-copy
worker. `max_sync_workers_per_subscription` is limited to one. Worker capacity
is shared with parallel queries. Changing `max_worker_processes` requires a
PostgreSQL restart.

Bootstrap waits until minute 04 through 49 to begin a database. It stops new
copy work at minute 56 to avoid overlapping an existing on-the-hour reporting
job. The databases remain separate from the production reporting database
until application configuration is changed.

## Snapshot rotation

`bsb-reporting-rotate.timer` runs at minute 10 of each hour. Its service:

1. Checks primary readiness and waits for the incoming database to acknowledge a captured source WAL position.
2. Disables incoming replication and waits for its workers to stop.
3. Records the freeze timestamp and rebuilds the local `topUser` materialized view.
4. Pauses the `browser_data` alias until existing transactions finish.
5. Reloads its backend mapping and resumes queued queries.
6. Enables replication on the previous snapshot database.
7. Runs the existing Browser `refresh_stats` command while holding the rotation lock.

The snapshot timestamp is the actual freeze time, not a guaranteed source
database state at an exact wall-clock hour. The proxy switches transactions;
a page with multiple autocommitted queries can straddle a rotation.

The service invokes the rotation script with `--refresh-statistics`. The lock
prevents another script-driven rotation during calculation, so all statistics
queries use the same frozen database. Browser reads use the stable alias too.
The freeze timestamp is written to `public.config` under the `updated` key,
which the existing Browser pages read as their data refresh time.

The statistics command keeps its existing behavior: write the statistics JSON,
update the separate analytics history database, then refresh the statistics
source-version cache and prewarm compatibility results. Browser page caches
expire normally. There is no additional Redis flush or atomic cache handoff.

Statistics have a 60-second execution budget enforced inside the container.
The service records the last run in
`/var/lib/bsb-reporting/statistics-refresh.json`. A failure does not reverse an
already completed proxy switch, rebuild either database, or retry the rotation.
The next hourly rotation attempts statistics again. To retry just the current
snapshot's statistics, run as root on the database host:

```sh
python3 /usr/local/libexec/bsb-reporting/rotate_reporting_snapshot.py --refresh-only
```

This command holds the same lock and checks that the application connects to
the frozen, read-only snapshot before invoking `refresh_stats`. Do not run the
unwrapped management command concurrently with rotation. Publication still
uses the existing command's ordering, so a partial publication failure may
leave the JSON fallback, analytics history, or cache at different versions
until a retry succeeds.

## Resource limits

`bsb-reporting-guard.service` monitors primary readiness, available memory, free
disk space, and retained WAL. Reporting replication and bootstrap database
backends run with low CPU priority, idle I/O priority, and affinity to one CPU.
These are scheduling controls, not a strict disk-bandwidth limit.

Two consecutive unhealthy readiness checks, responses longer than 800 ms, or
available memory below 768 MiB pause the new subscriptions. Retained WAL above
2 GiB or free disk space below 3 GiB stops the new subscriptions and releases
their replication slots. The database files are preserved, but releasing slots
requires reinitialization before replication can resume. Existing official
mirror slots are excluded from this cleanup.

`reset-unpublished` is limited to staged databases whose subscriptions have
already been detached after a guarded abort. It refuses to run with active
PgBouncer or client connections. It replaces the staged public schemas, not
the primary or legacy reporting database.

Guard samples and snapshot metadata are stored under `/var/lib/bsb-reporting`.
Service logs are available through `journalctl`. PgBouncer binds to loopback,
uses SCRAM authentication, limits backend connections, and rotates its own
log with four retained files and an 8 MiB rotation threshold.

## Verification and application configuration

Run `export/verify_reporting_pool.py` as root on the database host after
bootstrap and PgBouncer startup. It uses the existing Browser container as a
client, keeps one connection open across an actual snapshot rotation, verifies
the selected database changes, and checks read-only access and the absence of
the private schema. It rotates the staged alias and therefore is not a purely
read-only diagnostic.

Applications can use a fixed host and port with database name `browser_data`
and the generated reporting reader account. Transaction pooling also requires
checking server-side cursor and session-state compatibility. PgBouncer does not
invalidate application caches; the existing statistics command updates its
source-version cache, and page caches use their configured expiry.

Keep the analytics database's host, port, and writer credentials configured
explicitly. They must not inherit the reporting reader or the proxy port.
Schema changes are not replicated automatically; migrations must also update
both reporting schemas before new columns are published.

Before changing production application configuration, verify both snapshots
and retain the old database connection settings for rollback. Pause the legacy
dump-and-statistics cron entry before enabling the replacement hourly workflow.
Keep unrelated cron entries unchanged.

## Application migration

`export/browser-snapshot.compose.yml` is an additional Compose override. It
uses `SBtools.settings.snapshot`, which disables server-side cursors on the
transaction-pooled reporting connection. It also replaces the default startup
command: a read-only reporting database must not run the unqualified Django
`migrate` command. Analytics migrations continue to use `--database=analytics`.

The host-prepared override and credential file are under
`/var/lib/bsb-reporting/migration`. The credential file retains the running
container's environment, overrides the reporting connection, and explicitly
preserves the analytics connection. The prepared override preserves the
running image, host network, persistent statistics data mount, and Gunicorn
worker and thread settings. Preparing or rendering these files does not
recreate the running application.

1. Pause the legacy dump-and-statistics cron entry and the snapshot rotation timer.
2. Recreate the Browser web container using the prepared Compose override.
3. Verify that Browser uses `browser_data` on port 6432 with the read-only
   reporting account, while analytics still uses its original writer connection.
4. Install the rotation script and service with `--refresh-statistics`, reload
   systemd, and run `--refresh-only` for the current snapshot.
5. Verify that the JSON statistics, analytics history, and statistics cache
   source version match the snapshot's `updated` value. Start the rotation timer.
6. Check a complete rotation with statistics and inspect service logs and
   primary readiness. Keep the legacy database and saved container configuration
   available for rollback.

Existing API servers, official mirror servers, Nginx routes, and frontend URLs
keep their current configuration. The migration adds deployment settings and
an operations-script hook; it does not change Browser queries or the statistics
calculation code.

## Completing the deployment switch

After verification, persist the snapshot environment in the deployment's
`.env.docker`, owned by the deployment user with mode 0600. Merge the snapshot
settings and startup command into its normal
`docker-compose.override.yml`, preserving the image and persistent data mount.
The normal override should use `.env.docker` from the base Compose file rather
than the root-only staging credential file.

Compare the resolved service configuration with the staged configuration
without printing secrets, then run the normal `docker compose up -d --no-deps
--no-build web` command from the project directory. This prevents a later
deployment from restoring the old database connection. Keep the saved original
environment and override available for rollback.

Remove the retired hourly dump-and-statistics entry from the active crontab.
Keep the snapshot timer enabled and retain unrelated backup jobs. Confirm that
Browser, the public statistics endpoints, and the stored statistics version
agree, and that the old reporting database has no application connections.
The old database can remain available for rollback until a separate cleanup.

## Publishing a release

Commit and push the application settings, operations scripts, units, and docs
before deploying. Build the Browser image from a clean checkout of that commit:

```sh
docker build --build-arg VCS_REF="$RELEASE_COMMIT" -t "bsb-browser:$RELEASE_COMMIT" .
```

The image includes `SBtools.settings.snapshot`; remove the temporary bind mount
for that module when updating the deployment override to the release image.
Install operations scripts and systemd units from the same checkout. Keep
credentials and runtime state outside the checkout. Record the image ID, commit,
and installed-file checksums in the host's protected deployment state.

Pause the rotation timer during deployment and wait for any active rotation to
finish. After deployment, verify the image revision, application database
connections, and statistics versions, then resume the timer. Retain the previous
image and deployment configuration for rollback.
