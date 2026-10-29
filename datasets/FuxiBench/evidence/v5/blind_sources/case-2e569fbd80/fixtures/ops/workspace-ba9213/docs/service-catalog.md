# Service catalog

| Service | Owner | Emits | Consumes |
|---------|-------|-------|----------|
| orders-service | Orders team | `orders.created`, `orders.cancelled` | — |
| inventory-service | Supply team | `inventory.reserved`, `inventory.rejected` | `orders.created`, `orders.cancelled` |

## Delivery guarantees

The managed broker provides at-least-once delivery. Consumers must therefore be
idempotent: a repeated `orders.created` must not reserve stock twice, and a
repeated `orders.cancelled` must not release stock twice.

The local in-process bus delivers each published event exactly once. Idempotency
therefore cannot be demonstrated locally without an explicit duplicate-injection
harness, which is not present in this workspace.

## Topic ownership

- `orders.created` — produced by orders-service after validation succeeds.
- `orders.cancelled` — produced by orders-service when a created order is
  cancelled.
- `inventory.reserved` — produced by inventory-service after stock is debited.
- `inventory.rejected` — produced by inventory-service when stock is
  insufficient; includes `reason` and `available`.
