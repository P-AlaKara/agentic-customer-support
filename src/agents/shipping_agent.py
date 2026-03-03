"""
Shipping/Order Tracking Business Process Agent

Handles order tracking and shipping inquiries using:
- Database queries (orders table for tracking info)
- Knowledge base (shipping policies via RAG)
- Gemini API for natural language generation

Provides tracking status, estimated delivery, and shipping updates.
"""

import logging
from typing import Dict, Any, Optional

# Flexible imports
try:
    from ..event_bus import EventBus, Event
    from ..utils.database import get_db_connection, KnowledgeBaseDB
    from ..utils.gemini import get_gemini_client
    from ..utils.prompt_templates import SHIPPING_RESPONSE_TEMPLATE
except (ImportError, ValueError):
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from event_bus import EventBus, Event
    from utils.database import get_db_connection, KnowledgeBaseDB
    from utils.gemini import get_gemini_client
    from utils.prompt_templates import SHIPPING_RESPONSE_TEMPLATE


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _safe_log_agent_event(agent_name: str, event_type: str, input_data: Dict[str, Any], output_data: Dict[str, Any]):
    """Best-effort event logging to API gateway without tight coupling."""
    try:
        from ..api.gateway import log_agent_event
    except (ImportError, ValueError):
        try:
            from src.api.gateway import log_agent_event
        except (ImportError, ValueError):
            return

    try:
        log_agent_event(agent_name=agent_name, event_type=event_type, input_data=input_data, output_data=output_data)
    except Exception:
        return



class ShippingAgent:
    """
    Shipping/Order Tracking Business Process Agent.
    
    Handles:
    - Order status inquiries
    - Shipping/tracking information
    - Delivery estimates
    - Shipping policy questions
    
    Uses:
    - Database: Query orders for tracking data
    - KB: Retrieve shipping policies (RAG)
    - Gemini: Generate natural responses
    """
    
    def __init__(self, event_bus: EventBus):
        """
        Initialize Shipping Agent.
        
        Args:
            event_bus: The event bus for communication
        """
        self.bus = event_bus
        
        # Initialize connections
        try:
            db_conn = get_db_connection()
            self.kb_db = KnowledgeBaseDB(db_conn)
        except Exception as e:
            logger.warning(f"Database initialization failed: {e}")
            self.kb_db = None
        
        try:
            self.gemini = get_gemini_client()
        except Exception as e:
            logger.warning(f"Gemini initialization failed: {e}")
            self.gemini = None
        
        # Statistics
        self.stats = {
            'requests_handled': 0,
            'orders_tracked': 0,
            'policies_retrieved': 0,
            'responses_generated': 0
        }
        
        # Subscribe to events
        self.bus.subscribe('TASK_HANDLE_ORDER_TRACKING', self.handle_tracking_request)
        
        logger.info("ShippingAgent initialized")
    
    def handle_tracking_request(self, event: Event):
        """
        Main handler for order tracking requests.
        
        Expected event payload (full context):
        {
            'session_id': str,
            'customer_email': str,
            'current_intent': 'track_order',
            'entities': {'order_id': str (optional)},
            'messages': [...]
        }
        
        Publishes:
        - RESULT_SEND_RESPONSE_TO_USER: Response to customer
        """
        try:
            context = event.payload
            session_id = context['session_id']
            customer_email = context.get('customer_email')
            entities = context.get('entities', {})
            order_id = context.get('order_id') or entities.get('order_id')
            
            logger.info(f"[Shipping Agent] Handling tracking request for session {session_id}")
            self.stats['requests_handled'] += 1
            
            # Get user's last message
            user_query = self._get_last_user_message(context)
            
            order_info = context.get('order_details')
            order_status = context.get('order_status')

            if not order_id:
                response = "I can definitely help with tracking. Could you please share your order ID in the format ORD12345 so I can check the latest status?"
            else:
                # Step 1: Retrieve shipping policies/info from KB
                knowledge = self._retrieve_shipping_info()

                # Step 2: Generate response using Gemini
                response = self._generate_response(
                    user_query=user_query,
                    context=context,
                    knowledge=knowledge,
                    order_info=order_info,
                    order_id=order_id,
                    order_status=order_status
                )
            
            # Step 4: Send response to user
            self.bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
                'session_id': session_id,
                'text': response,
                'agent': 'SHIPPING_AGENT',
                'confidence': 0.95
            })
            
            if order_info:
                self.stats['orders_tracked'] += 1
            
            self.stats['responses_generated'] += 1
            logger.info(f"[Shipping Agent] Response sent for {session_id}")

            _safe_log_agent_event(
                agent_name='shipping',
                event_type='TASK_HANDLE_ORDER_TRACKING',
                input_data={'session_id': session_id, 'customer_email': customer_email, 'entities': entities},
                output_data={'response_preview': response[:160], 'order_found': bool(order_info)}
            )
            
        except Exception as e:
            logger.error(f"[Shipping Agent] Error handling tracking request: {e}", exc_info=True)
            
            # Send error fallback
            self.bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
                'session_id': context.get('session_id'),
                'text': "I apologize, but I'm having trouble looking up your order. Please provide your order number and I'll check the tracking status for you.",
                'agent': 'SHIPPING_AGENT',
                'confidence': 0.5
            })
    
    def _get_last_user_message(self, context: Dict[str, Any]) -> str:
        """Extract the last user message from context"""
        messages = context.get('messages', [])
        for msg in reversed(messages):
            if msg.get('sender') == 'USER':
                return msg.get('text', '')
        return "Where is my order?"
    
    def _retrieve_shipping_info(self) -> str:
        """
        Retrieve shipping policies from knowledge base using RAG.
        
        Returns:
            Concatenated shipping info text
        """
        if not self.kb_db:
            return "Standard shipping: 5-7 business days. Express shipping: 2-3 business days."
        
        try:
            # TODO: Implement vector search with embeddings
            # For now, return static shipping info
            self.stats['policies_retrieved'] += 1
            
            return """Shipping Information:
- Standard Shipping: 5-7 business days (Free on orders over $50)
- Express Shipping: 2-3 business days ($15)
- Overnight Shipping: Next business day ($30)
- Orders ship within 24 hours of placement
- Tracking numbers emailed when order ships
- International shipping: 10-14 business days

Status-specific guidance:
- PROCESSING: order is being prepared and should ship within 24 hours.
- SHIPPED: order has left warehouse, share expected delivery estimate.
- DELIVERED: confirm delivery and offer additional help.
- CANCELLED: explain cancellation and recommend checking payment/inventory notices."""
            
        except Exception as e:
            logger.error(f"Error retrieving shipping info: {e}")
            return "Please contact support for shipping details."
    
    def _generate_response(
        self,
        user_query: str,
        context: Dict[str, Any],
        knowledge: str,
        order_info: Optional[Dict[str, Any]],
        order_id: str,
        order_status: Optional[str]
    ) -> str:
        """
        Generate response using Gemini.
        
        Args:
            user_query: User's message
            context: Full conversation context
            knowledge: Retrieved shipping information
            order_info: Order details with tracking (if available)
        
        Returns:
            Generated response text
        """
        # Build enhanced context for Gemini
        #TODO: Give last 2-3 messages as context & modify template to instruct Gemini to use them
        enhanced_context = {
            'customer_email': context.get('customer_email'),
            'current_intent': context.get('current_intent'),
            'current_sentiment': context.get('current_sentiment'),
            'order_id': order_id,
            'order_status': order_status,
            'entities': context.get('entities', {}),
            'order_details': order_info
        }
        
        template = SHIPPING_RESPONSE_TEMPLATE
        
        if self.gemini:
            return self.gemini.generate_response(
                user_query=user_query,
                context=enhanced_context,
                knowledge=knowledge,
                template=template
            )
        else:
            # Fallback response
            return self._fallback_response(order_id, order_info, order_status)

    def _fallback_response(self, order_id: str, order_info: Optional[Dict[str, Any]], order_status: Optional[str]) -> str:
        """Simple fallback when Gemini unavailable"""
        if order_info:
            status = (order_status or order_info.get('status') or '').upper()
            
            # Build response based on status
            if status == 'DELIVERED':
                return f"Great news! Your order {order_id} has been delivered. If you have any issues, please let us know."
            elif status in {'SHIPPED', 'IN_TRANSIT'}:
                tracking = order_info.get('tracking_number', 'available in your email')
                return f"Your order {order_id} is currently in transit. Tracking number: {tracking}. Expected delivery within 2-3 business days."
            elif status == 'PROCESSING':
                return f"Your order {order_id} is being processed and will ship within 24 hours. You'll receive a tracking number via email once it ships."
            elif status == 'CANCELLED':
                return f"Your order {order_id} has been cancelled. This can happen due to payment authorization issues or stock availability. Please reply if you'd like help placing a new order."
            else:
                return f"Your order {order_id} status is: {status}. Please check your email for tracking details."
        else:
            return f"I couldn't find details for order {order_id}. Please confirm the ID (format ORD12345) and I'll check again."
    
    def get_stats(self) -> Dict[str, int]:
        """Get agent statistics"""
        return self.stats.copy()


if __name__ == "__main__":
    """Standalone test"""
    print("=== Shipping Agent Test ===\n")
    
    # Load environment variables
    try:
        from dotenv import load_dotenv
        load_dotenv()
        print("✅ Environment variables loaded\n")
    except ImportError:
        print("⚠️  python-dotenv not installed\n")
    
    from event_bus import EventBus
    
    bus = EventBus()
    agent = ShippingAgent(bus)
    
    # Subscribe to results
    def handle_response(event):
        print(f"Response: {event.payload['text']}")
    
    bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', handle_response)
    
    # Test 1: Tracking request without order number
    print("Test 1: General tracking inquiry")
    bus.publish('TASK_HANDLE_ORDER_TRACKING', {
        'session_id': 'test-001',
        'customer_email': 'customer@example.com',
        'current_intent': 'track_order',
        'current_sentiment': 'NEUTRAL',
        'entities': {},
        'messages': [
            {'sender': 'USER', 'text': 'Where is my order?'}
        ]
    })
    
    print("\n" + "="*60 + "\n")
    
    # Test 2: Tracking with order number
    print("Test 2: Tracking with order number")
    bus.publish('TASK_HANDLE_ORDER_TRACKING', {
        'session_id': 'test-002',
        'customer_email': 'customer@example.com',
        'current_intent': 'track_order',
        'current_sentiment': 'NEUTRAL',
        'entities': {'order_id': 'ORD-12345'},
        'messages': [
            {'sender': 'USER', 'text': 'Track order ORD-12345'}
        ]
    })
    
    print("\n=== Statistics ===")
    print(agent.get_stats())
