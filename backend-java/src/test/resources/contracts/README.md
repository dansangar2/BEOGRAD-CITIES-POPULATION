# Frozen synthetic contracts

Captured from Django before Python HTTP archival on 2026-10-28. These fixtures
contain no real database rows, credentials or provider responses. Two schemas
(empty/populated) and 660 cases each preserve the previously verified HTTP
contracts, including nine languages, seven methods and Accept variants.

`*-database.json` contains complete SQL statements for a new temporary SQLite
database. `*-responses.json` deduplicates expected responses and references
them from individual request cases. Java loads these directly; neither Python,
Django nor backend-python is executed during Maven verification.
