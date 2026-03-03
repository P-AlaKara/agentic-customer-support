"""Prompt templates used across agents."""

RETURNS_RESPONSE_TEMPLATE = """
1. Acknowledge the return request
2. If order_id is missing, politely ask for order ID (format ORD12345) and do not proceed.
3. If order_id is present, use only provided order details/return details.
4. Mention return status explicitly and follow status-specific guidance exactly.
5. Explain policy and clear next steps.
6. Do not invent missing fields.
7. Be empathetic and helpful.
8. Keep response to 3-4 sentences maximum.
""".strip()

SHIPPING_RESPONSE_TEMPLATE = """
1. Acknowledge the tracking inquiry
2. If order_id is missing, politely ask for order ID (format ORD12345) and do not proceed.
3. If order_id is present, use only provided order details/status.
4. If order info available, provide specific tracking details:
   - Order number
   - Current status (e.g., "In Transit", "Delivered", "Processing")
   - Tracking number (if available)
   - Estimated delivery date
5. Mention status explicitly and follow status-specific policy instructions.
6. If no order details are available for provided order_id, say so and ask to confirm it.
7. Do not invent missing fields.
8. Be reassuring and helpful.
9. Keep response to 3-4 sentences maximum.
10. Provide actionable next steps.
""".strip()
