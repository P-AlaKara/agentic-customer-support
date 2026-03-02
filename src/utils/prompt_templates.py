"""Prompt templates used across agents."""

RETURNS_RESPONSE_TEMPLATE = """
1. Acknowledge the return request
2. Explain the return policy clearly
3. If order info available, mention specific order details
4. Provide next steps (what customer needs to do)
5. Be empathetic and helpful
6. Keep response to 3-4 sentences maximum
""".strip()

SHIPPING_RESPONSE_TEMPLATE = """
1. Acknowledge the tracking inquiry
2. If order info available, provide specific tracking details:
   - Order number
   - Current status (e.g., "In Transit", "Delivered", "Processing")
   - Tracking number (if available)
   - Estimated delivery date
3. If no order found, ask for order number politely
4. Be reassuring and helpful
5. Keep response to 3-4 sentences maximum
6. Provide actionable next steps
""".strip()
