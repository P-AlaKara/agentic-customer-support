# Shipping Policy (Status-Aware)

## Baseline Shipping Expectations

Standard shipping usually takes 3–5 business days after dispatch.
Expedited shipping takes 1–2 business days, depending on destination.
Delays may occur due to weather, carrier issues, or peak demand.
Customers receive a tracking link by email once the order is shipped.

## Required Response Rules

- If `order_id` is missing, politely ask for it using format `ORD12345`.
- Do not proceed with tracking updates until an `order_id` is provided.
- If `order_id` exists, use only provided order details and status.
- Do not invent missing fields.

## Status-Specific Instructions

### PROCESSING
- Inform the customer the order is being prepared.
- Mention expected handoff/dispatch timing.
- Reassure them they will receive tracking when it ships.

### SHIPPED
- Confirm the package has been shipped.
- Share available tracking details.
- Provide expected delivery timing window.

### DELIVERED
- Confirm successful delivery.
- Offer help if the customer did not receive it or has post-delivery issues.

### CANCELLED
- Confirm the order has been cancelled.
- Mention possible reasons (payment failure, inventory issue, customer-initiated cancellation).
- Offer next-step help (reorder or support contact).
