"""A small corpus and query set for measuring retrieval. Finance-flavoured, deliberately keyword-rich."""

from __future__ import annotations

from .model import Entity

ENTITIES = [
    Entity(type="system", slug="kdb-gateway", name="kdb+ gateway", aliases=["gw", "gateway"], description="The q process that fronts the tick databases for the pricing desk."),
    Entity(type="system", slug="pricing-engine", name="Pricing engine", description="Shared curve construction and pricing service used by gamma and delta."),
    Entity(type="system", slug="risk-batch", name="Risk batch", description="The nightly job that computes risk and PnL for the rates desk."),
    Entity(type="environment", slug="uat", name="UAT", description="User acceptance testing environment, refreshed weekly from production."),
    Entity(type="environment", slug="prod", name="Production", aliases=["prod"], description="The production environment serving the trading desks."),
    Entity(type="project", slug="gamma", name="Gamma", description="The gamma pricing migration project for the rates desk."),
    Entity(type="project", slug="delta", name="Delta", description="The delta market data and snapshot project."),
    Entity(type="tool", slug="kdb", name="kdb+", aliases=["q"], description="The kdb+ time series database and q language."),
    Entity(type="tool", slug="python", name="Python", description="Python, the main application language on the desk."),
    Entity(type="desk", slug="rates", name="Rates desk", description="The interest rates trading desk."),
]

FINDINGS: list[tuple[str, str, str, list[str]]] = [
    ("dead-end", "Batch selects over 10k symbols time out on the UAT gateway", "Large selects over more than ten thousand symbols exceed the thirty second UAT gateway timeout.", ["system/kdb-gateway", "environment/uat"]),
    ("how-to", "Chunk symbol lists at 5k before querying the gateway", "Split symbol lists into chunks of five thousand and run the selects concurrently to stay under the timeout.", ["system/kdb-gateway", "tool/kdb"]),
    ("fact", "Prod gateway timeout is 120 seconds", "The production gateway enforces a one hundred and twenty second timeout per call.", ["system/kdb-gateway", "environment/prod"]),
    ("caveat", "Gateway returns an empty table rather than an error on timeout", "When a call times out the gateway returns an empty table instead of raising, so callers must check row counts.", ["system/kdb-gateway"]),
    ("fact", "Gamma nightly batch starts at 02:00 London", "The gamma nightly batch is scheduled at two in the morning London time and takes about ninety minutes.", ["project/gamma", "system/risk-batch"]),
    ("dead-end", "Running the risk batch twice in one night corrupts the PnL cache", "A second risk batch run on the same date overwrites the PnL cache with partial results.", ["system/risk-batch"]),
    ("how-to", "Rerun a failed risk batch safely", "Clear the PnL cache for the date, then rerun the batch with the rerun flag set.", ["system/risk-batch"]),
    ("caveat", "UAT database is refreshed from prod every Sunday", "The UAT database is rebuilt from production every Sunday night, so UAT-only test data disappears weekly.", ["environment/uat"]),
    ("decision", "Gamma uses Python 3.12 rather than 3.11", "Gamma standardised on Python three point twelve for the pattern matching and performance improvements.", ["project/gamma", "tool/python"]),
    ("dead-end", "pandas to_sql is too slow for loading curves into the pricing engine", "Loading yield curves with pandas to_sql takes forty minutes; use the bulk copy loader instead.", ["system/pricing-engine", "tool/python"]),
    ("how-to", "Bulk load curves into the pricing engine", "Write curves to a CSV and call the bulk copy loader, which finishes in under a minute.", ["system/pricing-engine"]),
    ("fact", "Pricing engine rejects curves with duplicate tenors", "The pricing engine validates curve tenors and rejects any curve where a tenor appears twice.", ["system/pricing-engine"]),
    ("caveat", "Pricing engine silently uses yesterday's curve when today's is missing", "If today's curve is absent the pricing engine falls back to the previous business day without warning.", ["system/pricing-engine", "environment/prod"]),
    ("decision", "Delta stores market data snapshots in kdb rather than Postgres", "Delta chose kdb for market data snapshots because of the time series query performance.", ["project/delta", "tool/kdb"]),
    ("dead-end", "Connecting to kdb from Python with qpython hangs on large results", "qpython blocks indefinitely on result sets over a million rows; use pykx instead.", ["tool/kdb", "tool/python"]),
    ("how-to", "Query kdb from Python with pykx", "Install pykx, set the licence environment variable, and open a synchronous connection to the gateway.", ["tool/kdb", "tool/python"]),
    ("fact", "Rates desk books are locked at 18:30 London", "The rates desk locks its books at half past six in the evening London time for end of day.", ["desk/rates"]),
    ("caveat", "Trades booked after 18:30 appear in the next day's risk", "Anything booked after the rates desk lock shows up in the following day's risk batch, not today's.", ["desk/rates", "system/risk-batch"]),
    ("decision", "Gamma pricing results are persisted as parquet not CSV", "Gamma writes pricing results as parquet for schema enforcement and compression.", ["project/gamma", "system/pricing-engine"]),
    ("dead-end", "Deploying to UAT on Friday afternoon collides with the Sunday refresh", "UAT deployments late on Friday are lost when the Sunday refresh rebuilds the environment.", ["environment/uat"]),
    ("how-to", "Request a UAT refresh outside the Sunday schedule", "Raise a ticket with the platform team before noon and the ad hoc refresh runs the same evening.", ["environment/uat"]),
    ("fact", "Prod kdb gateway runs version 4.1", "The production kdb gateway is on version four point one and UAT is on four point two.", ["system/kdb-gateway", "environment/prod", "environment/uat"]),
    ("caveat", "Risk batch logs rotate at midnight and lose the current run", "Log rotation at midnight truncates the current risk batch log, so copy logs before midnight when debugging.", ["system/risk-batch"]),
    ("decision", "Delta uses the shared pricing engine instead of its own", "Delta adopted the shared pricing engine to avoid duplicating curve logic.", ["project/delta", "system/pricing-engine"]),
    ("dead-end", "Increasing the gateway timeout to 300s causes connection pool exhaustion", "Raising the gateway timeout starved the connection pool; chunking queries is the right fix.", ["system/kdb-gateway"]),
    ("how-to", "Get read access to the prod kdb gateway", "Request the gateway reader role through the access portal; approval takes one business day.", ["system/kdb-gateway", "environment/prod"]),
    ("fact", "Risk batch publishes PnL to the rates desk dashboard by 04:00", "The risk batch publishes PnL to the rates dashboard by four in the morning on a normal night.", ["system/risk-batch", "desk/rates"]),
    ("caveat", "Python 3.12 removed distutils which breaks the legacy curve loader", "The legacy curve loader imports distutils, which Python three point twelve removed; use the setuptools shim.", ["tool/python", "system/pricing-engine"]),
    ("dead-end", "Mocking the gateway in unit tests with a real q process is too flaky", "Starting a real q process in tests fails intermittently on CI; use the recorded response fixtures.", ["system/kdb-gateway", "tool/kdb"]),
    ("how-to", "Reproduce a gamma pricing run locally", "Pull the parquet inputs for the date, set the gamma config environment variable, and run the pricing CLI.", ["project/gamma", "system/pricing-engine"]),
]

QUERIES: list[tuple[str, list[int]]] = [
    ("gateway timeout symbols", [0, 1]),
    ("how long is the prod gateway timeout", [2]),
    ("empty table on timeout", [3]),
    ("when does the gamma nightly batch run", [4]),
    ("rerun risk batch", [5, 6]),
    ("UAT refresh Sunday", [7, 19, 20]),
    ("which Python version does gamma use", [8]),
    ("loading curves slowly pandas", [9, 10]),
    ("duplicate tenors curve rejected", [11]),
    ("pricing engine uses yesterday's curve", [12]),
    ("qpython hangs", [14, 15]),
    ("rates desk book lock time", [16, 17]),
    ("parquet pricing results", [18]),
    ("kdb gateway version prod", [21]),
    ("risk batch logs rotate midnight", [22]),
    ("connection pool exhaustion timeout", [24]),
    ("read access prod gateway", [25]),
    ("PnL dashboard time", [26]),
    ("distutils removed python 3.12", [27]),
    ("flaky q process tests", [28]),
]

SECTIONS = {
    "dead-end": ["Approach", "Why it fails"],
    "caveat": ["Symptom", "Cause", "Workaround"],
    "how-to": ["Steps", "Verify"],
    "decision": ["Options considered", "Rationale", "Consequences"],
    "fact": ["Detail", "How to check"],
}


def finding_id(i: int) -> str:
    return f"01J9XK3M8Q7ZV2W1F4N6B5H{i:03d}"
