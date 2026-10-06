# SQLite-only amendment — 06/10/2026

The owner directly confirmed in the Codex conversation: **SQLite-only, with an empty profile for a new installation**. This supersedes the 02/10 requirement to support advanced MySQL configurations. Claude's `OWNER_DECISIONS.md` is supporting context; the direct owner reply is the authorization.

Product storage will use local SQLite with the existing WAL/busy-timeout, maintenance admission, atomic compare-and-set claims and immutable migration history. Product startup must neither infer nor connect to MySQL from legacy database settings, environment variables, setup commands or restored manifests. Existing MySQL databases and credentials are not migrated, reset or deleted. Unsupported legacy configuration must produce a sanitized actionable result.

A clean installation creates an empty per-user SQLite profile and begins onboarding; it must not ship or import the developer's `.env`, database, Telegram session, vectors or credentials. Installing/reinstalling is not authorization to erase an existing user's data. A future explicit reset flow belongs to reviewed management/installer work and requires preview, backup and confirmation.

The implementation must update product configuration/setup/storage entry points, dependencies, documentation and CI coherently. Historical migrations and useful dialect regression evidence remain immutable. Removal of obsolete MySQL tests must preserve their SQLite safety coverage and introduce negative cases proving unsupported connection targets cannot be opened. New schema/contracts changes remain coordinator-owned and generated frontend mirrors must be regenerated.

The amendment does not waive source permissions, owner/SID guards, native-only secrets, cloud consent, budget/reconciliation, backup/restore fencing, art or release gates. Physical installation, upgrade, human UAT, soak and distribution approval remain Pending until performed. The five active task acceptance records must identify which executable snapshot they cover; pre-amendment MySQL results remain historical evidence.
