# Statistics ownership

Browser serves the legacy public paths:

- /api/getTotalStats
- /api/getTopUsers
- /api/getTopCategoryUsers
- /api/getDaysSavedFormatted

Nginx must send all four paths to browser before deploying the main server
without these handlers. Personal statistics and anonymous audience reporting
remain in the main server.

The same-server sync excludes the primary database's topUser view. It restores
the dump into sponsorblock_sync, runs export/reporting_views.sql there, then
switches the reporting database. Both files must be deployed together.
The SQL preserves the existing leaderboard filters and grouping.

After a successful sync with the new script, the primary topUser materialized
view can be dropped without CASCADE. Save its definition first. The browser
view is reconstructed on every sync, so it does not depend on a primary view.
Do not remove the view from the reporting database.

getDaysSavedFormatted keeps its original shadowHidden filter, including
negative-vote segments, and returns a formatted string. It shares the
source-version cache and hourly prewarming used by other compatibility APIs.

Do not delete archivedSponsorTimes or change downvoteSegmentArchiveJob.
The bsb_analytics database and its historical snapshots are outside sync targets.
