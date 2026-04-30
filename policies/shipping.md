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

## Edge Cases

### Delayed Shipment
- A shipment is considered delayed if it has not progressed for more than 48 hours past the carrier's last update or has missed its expected-delivery window by more than 1 business day.
- Acknowledge the delay and apologise for the inconvenience before sharing tracking details.
- Common causes to mention when relevant: carrier sorting backlog, customs clearance, weather disruption, address verification.
- Set a clear next-step expectation: "I will continue to monitor this. If the package does not move within the next 24 hours, please reply and I will escalate to the carrier."

### Lost in Transit
- A package is treated as lost if there has been no carrier scan for more than 7 business days (domestic) or 14 business days (international).
- Confirm the shipping address on file with the customer before opening a lost-package case.
- Open a carrier trace and offer the customer a choice of replacement (subject to stock) or full refund. Make it clear that replacement timing depends on stock availability.
- Set expectations: carrier traces typically resolve within 5-7 business days.

### Missed Delivery / Redelivery
- If the carrier attempted delivery but the customer was unavailable, explain the redelivery options offered by the carrier (next-day re-attempt, pickup-point hold, or scheduled redelivery slot).
- Provide the carrier's tracking link so the customer can self-serve the redelivery option.
- If the package is being held at a pickup point, share the location, hours, and any required ID or confirmation number from the tracking record.
- After two failed delivery attempts, packages are typically returned to the warehouse. Inform the customer that, in that case, they will be offered a refund or a re-ship to a corrected address.
