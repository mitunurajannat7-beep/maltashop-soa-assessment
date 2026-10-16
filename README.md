# MaltaShop SOA Assessment

A Python prototype of a place-order saga: four separately running services, four service-owned SQLite stores, RabbitMQ events, a transactional outbox and an idempotent consumer. Payments and notifications are simulated. No actual card processing, card data or email delivery is used.

## Development status

Stage 2 implements services, Docker configuration, tests and demonstration helpers. Twenty-one automated tests passed in the preparation environment, including real local HTTP integration. Docker Compose and real RabbitMQ execution still require verification on a Docker-capable computer. CI will be added in a subsequent development commit. Planned checks are not completed evidence.

## Prerequisites

Docker Desktop running Linux containers, Docker Compose v2 and Git. Windows PowerShell 5.1+ supports the optional evidence helpers. Python is supplied inside the containers; no separate Windows Python installation is necessary. Internet access is needed initially to download base images and the pinned Pika package.

## Start the system

From the repository root, create the environment file once, then start:

```powershell
Copy-Item .env.example .env
docker compose up --build -d --wait --wait-timeout 180
docker compose ps
```

Preserve any later custom `.env` values instead of copying over them. All five containers should become healthy. The required single command `docker compose up` also builds missing images and starts the system. First-time builds may take several minutes.

| Service | Local URL | Owned persistent volume |
| --- | --- | --- |
| Order | http://localhost:8001 | order_data: orders, saga state and outbox |
| Inventory | http://localhost:8002 | inventory_data: stock and reservations |
| Payment | http://localhost:8003 | payment_data: simulated authorizations |
| Notification | http://localhost:8004 | notification_data: inbox IDs and mock notification records |
| RabbitMQ management | http://localhost:15672 | broker_data: queues and messages |

Every Python service has `/health`, checking process/database availability, not end-to-end reliability. Notification may be healthy while its worker reconnects; smoke testing separately proves consumption. Broker defaults are `maltashop` / `local-demo-broker` for local education only. AMQP is internal to the Compose network. Published ports bind only to host loopback.

## Order request and evidence

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Demo.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Show-Evidence.ps1
```

Execution-policy overrides apply only to these child processes, not the system. Demo prints the actual HTTP request and response and writes `evidence/demonstration.json`. Show-Evidence prints real Order and Notification JSON logs with matching correlation, order and event IDs. It waits up to thirty seconds for consumption and preserves original combined logs. Generated evidence is ignored by Git.

Example body for POST `/orders`:

```json
{"customer_id":"demo-customer","sku":"HEADPHONES","quantity":1,"payment_token":"mock-success"}
```

Supply `Idempotency-Key` and `X-Correlation-ID` headers. HEADPHONES costs 4,999 euro cents; LAMP costs 2,999. Prices come from Inventory, not the customer. Both start at 100 units in a fresh database. Quantity is an integer from 1 to 100. Only `mock-success` and `mock-decline` payment tokens are accepted.

HTTP 201 means a new order confirmed; 200 is a successful replay; 202 is pending reconciliation; 409 is a business rejection or conflicting key reuse. GET `/orders/{order_id}` returns durable progress. A replay refers to the original order's correlation ID. Internal requests require `X-Service-Key`, except `/health`. The public Order endpoint has no actual customer authentication: keep it local. Gateway authentication and individual partner authorization are production-design scope.

## Automated tests

Build and run the dedicated test stage:

```powershell
docker build --target test -f services/order/Dockerfile -t maltashop-tests:stage2 .
docker run --rm maltashop-tests:stage2
```

Tests use temporary databases, not live demonstration data. Coverage includes business rules, concurrent stock reservation, repeat keys, lost payment responses, durable recovery, interrupted compensation, duplicate events, Order-to-Notification contract compatibility and real local HTTP integration. Broker I/O in this suite is mocked and must be labelled accordingly.

With Compose running, test real HTTP and RabbitMQ integration:

```powershell
docker compose exec -T order python -m scripts.smoke_test
```

The smoke test checks ordering, duplicate requests, conflicting input, actual broker publication/consumption, matching correlation IDs, rejected-payment compensation and missing stock. It consumes one stock unit per successful run. It ends with `SMOKE TEST PASSED` only if all checks succeed.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| SERVICE_API_KEY | local-demo-service-key | Internal demo credential |
| BROKER_USER | maltashop | Local broker user |
| BROKER_PASSWORD | local-demo-broker | Local broker password; use URL-safe demo values |
| SERVICE_NAME | Per-service Dockerfile | Process role |
| DB_PATH | /data/service.db | That service's private database |
| PORT | 8000 | Internal HTTP port |
| INVENTORY_URL | http://inventory:8000 | Order's Inventory endpoint |
| PAYMENT_URL | http://payment:8000 | Order's Payment endpoint |
| BROKER_URL | Constructed by Compose | AMQP connection endpoint; never log it |

Configuration is read at startup. `.env` is excluded from Git. Change demonstration credentials before any use outside an isolated local environment. Changing environment variables does not update a user already stored in an initialized RabbitMQ volume; use appropriate broker account management.

## Stop and restart

```powershell
docker compose down
docker compose up -d --wait
```

Named volumes retain data. `docker compose down -v` deletes orders, stock history and messages. It is a destructive reset, not the normal shutdown command.

## Consistency and reliability

Order persists saga progress before remote operations. Inventory and Payment bind operation keys to their inputs. An unknown payment outcome stays pending; retrying the same key reconciles it without a second authorization. Rejection triggers idempotent payment cancellation and stock release. Cancellation tombstones block delayed operations. Confirmation and an OrderPlaced event are recorded in one local transaction.

An independent outbox worker uses durable routing, persistent messages, mandatory publication and publisher confirms. Notification commits its inbox ID and simulated effect before acknowledging delivery. Delivery is at least once with deduplication, not end-to-end exactly once. Broker/Notification outages are outside synchronous checkout: events remain in the outbox or queue. Correlation IDs cross HTTP headers, event envelopes and structured stdout logs. No full metrics/traces stack or live email provider is implemented.

## Production limitations and accepted debt

- The standard-library HTTP server is deliberately small, not a hardened production application server. Replace it with a production framework/server and add TLS, customer identity, authorization and rate controls.
- SQLite stores and saga locks assume one process per service. Replicas need service-owned managed databases, cross-process saga leases, migration controls and safe recovery ownership.
- RabbitMQ is a single node. Production requires appropriate replication/queue choices, backups, retention and capacity planning. The classic dead-letter path is not lossless disaster recovery.
- Recovery retries lack an operational reconciliation console and production backoff/retry budgets. Unresolved orders require monitoring and human escalation.
- Shared infrastructure code in a monorepo requires regression/contract testing when changed. Dockerfiles build services separately. Production releases should pin immutable image digests and promote the same image; mutable demonstration base-image tags are not a production supply-chain policy.
- The prototype does not implement a storefront, real payments/email, AI model, thirty partner policies, Kubernetes, mesh, demonstrated ten-times capacity or measured 99.9% availability. These are proposed architecture and validation-roadmap items.

## Assessment and authorship

Prepared with AI assistance. Review the work and follow institutional disclosure requirements. Preserve genuine development commits and real execution evidence. See `docs/architecture.md`, `docs/evidence-plan.md` and `docs/progress.md`. The final report and CI workflow are not included in Stage 2.
