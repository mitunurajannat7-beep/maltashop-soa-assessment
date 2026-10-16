# Architecture working decisions

## Case constraints

MaltaShop has twelve developers, two operations staff, a limited tooling budget and no production Kubernetes experience. The replacement must isolate checkout from notification and recommendation failures, support weekly releases without planned downtime, allow thirty partners with different rights, and target ten-times normal sale traffic and 99.9% checkout availability.

## Recommendation

Migrate incrementally using a strangler approach. Keep a limited number of capability-oriented services rather than creating many small services at once. The prototype demonstrates distributed order consistency. It does not prove production availability or ten-times peak capacity.

## Prototype boundaries

| Capability | Owns | Prototype behaviour |
| --- | --- | --- |
| Order | Orders, client idempotency keys, saga state, outbound events | Accept a place-order request and orchestrate reservation and simulated authorization |
| Inventory | Stock, reservations and operation keys | Reserve stock atomically; release it idempotently |
| Payment | Simulated authorizations and operation keys | Authorize or reject a mock payment; cancel it idempotently |
| Notification | Processed event identifiers and notification records | Consume broker messages; record a mock notification, without sending email |

Use one SQLite database file per service for the small, single-replica prototype. Production replicas require suitable independent managed databases and a migration plan; sharing a local SQLite file between replicated application containers is not the production design.

## Consistency

Order orchestrates a saga: record a pending order; reserve inventory; request payment authorization; confirm the order; write an OrderPlaced event to a transactional outbox. On rejection, compensate completed steps. Persist states before remote operations so interrupted work can be recovered. An ambiguous payment outcome must remain pending reconciliation; it must not be misreported as a confirmed rejection.

Client idempotency keys identify repeat requests and bind to a canonical request payload. The same key with different input is a conflict. Downstream operation keys are deterministic per order and action. Notification stores the event ID in the same transaction as its business effect. Broker acknowledgement follows that transaction. A crash between publish and marking an outbox row sent can produce duplicates, which consumers must handle.

## AI and access control

The production Recommendation service consumes browsing and order streams through asynchronous subscriptions. Its processing, queue backlog or failure cannot be a synchronous dependency of checkout. A gateway validates customer credentials and partner scopes; each of the thirty partners receives an individual identity and access policy. These access-control and AI components must be labelled as proposed unless actually implemented.

## Delivery and operations

Use CI for automated tests and container validation, followed by an approval-controlled production release in the proposed delivery pipeline. A local Compose smoke test is real prototype validation, not evidence of an actual production deployment. Describe Kubernetes reliability and mesh trade-offs in the report without claiming that the prototype runs a production cluster.
