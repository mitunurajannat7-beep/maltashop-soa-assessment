# Stage 2 preparation verification

Date: 16 October 2026 (UTC).

Command: `python -m unittest discover -v`, with Pika 1.3.2 installed.
Result: **21 tests passed** in 0.177 seconds.

These checks ran on Linux in the preparation environment. They include real local HTTP requests between Order, Inventory and Payment, temporary service-owned SQLite databases, fault injection, concurrent requests/reservations, durable saga recovery, an Order-to-Notification consumer contract and duplicate-event handling. Broker transport in these automated tests is mocked; publisher-confirm and acknowledgement-order tests inspect actual application calls against mocked connections.

Python source syntax, Compose YAML structure, per-service Dockerfile paths, separate volume ownership and event-contract JSON were also checked. YAML parsing is not a substitute for `docker compose config` or actual container execution.

**Not yet verified here:** Docker builds, Compose startup, actual RabbitMQ delivery and Windows PowerShell evidence helpers. The supplied Windows commands run those checks in the user's Docker environment. No runtime screenshots or passing GitHub pipeline run have been claimed.
