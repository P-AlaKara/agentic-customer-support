# Returns Policy (Status-Aware)

## Baseline Return Rules

Returns are accepted within 30 days of delivery for eligible items.
Items should be in original condition with all tags/accessories.
Refunds are issued to the original payment method after inspection.
If the return is due to our mistake or a defective product, we cover shipping.
Otherwise, the customer is responsible for return shipping costs.

## Required Response Rules

- If `order_id` is missing, politely ask for it using format `ORD12345`.
- Do not proceed with return-status handling until `order_id` is provided.
- If `order_id` exists, use only provided order/return details and status.
- Mention status explicitly.
- Do not invent missing fields.

## Status-Specific Instructions

### REQUESTED
- Inform the customer the return is under review.
- Provide review timeframe expectations.

### APPROVED
- Confirm approval.
- Provide packaging and drop-off/shipping instructions.

### RECEIVED
- Confirm item receipt.
- Explain refund processing timeline.

### REJECTED
- Explain the rejection reason (if available in context).
- Offer next steps (appeal/support contact/alternative resolution).
