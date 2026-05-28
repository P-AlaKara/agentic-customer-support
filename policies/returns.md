# Returns Policy

## Baseline Return Rules

Returns are accepted within 20 days of delivery for eligible items.
Items should be in original condition with all tags/accessories.
Refunds are issued to the original payment method after inspection.
If the return is due to our mistake or a defective product, we cover shipping.
Otherwise, the customer is responsible for return shipping costs.

## Required Response Rules

- If `order_id` is missing, politely ask the customer for it and direct them to their order confirmation email, where the ID is included alongside other order details.
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
- Tell the customer to pack the item in its original packaging if available, or any sturdy box; include all original accessories, manuals, and tags.
- Tell the customer that a prepaid return label has been sent to their email — they should print it, attach it to the package, and drop the package at any nearby branch of the listed carrier (no appointment needed).
- Tell the customer to keep the carrier drop-off receipt until the refund is processed.
- If the customer cannot print the label, instruct them to reply to the confirmation email to request a QR-code drop-off option.

### RECEIVED
- Confirm item receipt.
- Explain refund processing timeline.

### REJECTED
- Explain the rejection reason (if available in context).
- Offer next steps (appeal/support contact/alternative resolution).

## Edge Cases

### Partial Refunds (Missing Accessories or Components)
- If an item is returned without its original accessories, manuals, packaging, tags, or components, the refund may be issued as a partial refund.
- The deduction is proportional to the missing items and the cost to make the product resellable.
- Inform the customer that the partial-refund amount will be confirmed by email after inspection, and offer to escalate if the deduction is disputed.

### Items Damaged During Return Transit
- If a returned item arrives damaged, we file a claim with the return carrier.
- The customer is not held responsible if the damage occurred in our prepaid return label channel.
- If the customer used their own carrier, we ask them to file the claim with that carrier and offer to assist with documentation.

### Return Window Extensions During Promotions
- During seasonal promotions or holiday windows, the return window may be extended (for example, items purchased in November or December typically qualify for an extended return window through mid-January).
- Apply any active extended-window policy automatically when the order falls within the promotional purchase dates. Confirm the applicable date by checking the order's promotional flag, if available.

### Digital Goods and Non-Returnable Items
- Digital goods, downloadable content, gift cards, redeemed vouchers, opened personal-care items, perishables, and final-sale items are non-returnable.
- Politely explain the policy and offer alternative help (for example, troubleshooting a digital product, redeeming a voucher, or contacting the brand directly for warranty service).
