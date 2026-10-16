# MaltaShop SOA Assessment

An educational prototype and architecture study for MaltaShop's place-order flow.

## Current development stage

Stage 1 establishes requirements, service boundaries and the implementation plan.
The working services, Docker configuration and CI workflow will be added in subsequent development stages. This scaffold does not yet run a prototype.

## Intended prototype

- Order: accepts orders and orchestrates the order saga.
- Inventory: reserves stock and releases reservations on compensation.
- Payment: authorizes simulated payments and cancels authorizations on compensation. No real card processing or card data.
- Notification: consumes confirmed-order events independently of checkout.
- A message broker transports asynchronous events.
- Each service owns its own persistent data store.

The larger production design also includes Catalogue, Customer identity, Partner access and AI Recommendation capabilities. These are design scope rather than claims about implemented prototype features.

## Validation planned

Happy-path ordering, rejected payment compensation, insufficient inventory, request idempotency, duplicate-event handling, contract compatibility and cross-service correlation IDs.

## Assessment evidence

Capture real execution output only. See `docs/evidence-plan.md`. Setup screenshots are not proof that the MaltaShop implementation works.

## Documentation

- `docs/architecture.md`: boundaries and consistency decisions.
- `docs/evidence-plan.md`: required evidence and capture guidance.
- `docs/progress.md`: development status and outstanding tasks.

## Authorship and limitations

This project is prepared with AI assistance. The student must review it and follow the institution's disclosure requirements. Documentation will distinguish tested behaviour, simulated components and proposed production improvements.
