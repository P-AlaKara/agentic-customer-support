"""
Returns Business Process Agent

Handles return and refund requests using:
- Database queries (orders, returns tables)
- Knowledge base (return policies via RAG)
- Gemini API for natural language generation
"""

import logging
from typing import Dict, Any, Optional

# Flexible imports
try:
    from ..event_bus import EventBus, Event
    from ..utils.database import get_db_connection, OrdersDB, KnowledgeBaseDB
    from ..utils.gemini import get_gemini_client
    from ..utils.prompt_templates import RETURNS_RESPONSE_TEMPLATE
except (ImportError, ValueError):
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from event_bus import EventBus, Event
    from utils.database import get_db_connection, OrdersDB, KnowledgeBaseDB
    from utils.gemini import get_gemini_client
    from utils.prompt_templates import RETURNS_RESPONSE_TEMPLATE


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



class ReturnsAgent:
    """
    Returns Business Process Agent.
    
    Handles:
    - Return eligibility checks
    - Return policy information
    - Return request creation
    - Refund status inquiries
    
    Uses:
    - Database: Query orders and returns
    - KB: Retrieve return policies (RAG)
    - Gemini: Generate natural responses
    """
    
    def __init__(self, event_bus: EventBus):
        """
        Initialize Returns Agent.
        
        Args:
            event_bus: The event bus for communication
        """
        self.bus = event_bus
        
        # Initialize connections
        try:
            db_conn = get_db_connection()
            self.orders_db = OrdersDB(db_conn)
            self.kb_db = KnowledgeBaseDB(db_conn)
        except Exception as e:
            logger.warning(f"Database initialization failed: {e}")
            self.orders_db = None
            self.kb_db = None
        
        try:
            self.gemini = get_gemini_client()
        except Exception as e:
            logger.warning(f"Gemini initialization failed: {e}")
            self.gemini = None
        
        # Statistics
        self.stats = {
            'requests_handled': 0,
            'returns_created': 0,
            'policies_retrieved': 0,
            'responses_generated': 0
        }
        
        # Subscribe to events
        self.bus.subscribe('TASK_HANDLE_RETURNS', self.handle_return_request)
        
        logger.info("ReturnsAgent initialized")
    
    def handle_return_request(self, event: Event):
        """
        Main handler for return requests.
        
        Expected event payload (full context):
        {
            'session_id': str,
            'customer_email': str,
            'current_intent': 'process_return',
            'entities': {'product': str, 'order_id': str (optional)},
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
            
            logger.info(f"[Returns Agent] Handling return for session {session_id}")
            self.stats['requests_handled'] += 1
            
            # Get user's last message
            user_query = self._get_last_user_message(context)
            
            # Step 1: Retrieve relevant knowledge
            knowledge = self._retrieve_return_policies()
            
            # Step 2: Check for order information
            order_info = self._get_order_info(customer_email, entities.get('order_id'))
            
            # Step 3: Generate response using Gemini
            response = self._generate_response(
                user_query=user_query,
                context=context,
                knowledge=knowledge,
                order_info=order_info
            )
            
            # Step 4: Send response to user
            self.bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
                'session_id': session_id,
                'text': response,
                'agent': 'RETURNS_AGENT',
                'confidence': 0.95
            })
            
            self.stats['responses_generated'] += 1
            logger.info(f"[Returns Agent] Response sent for {session_id}")

            _safe_log_agent_event(
                agent_name='returns',
                event_type='TASK_HANDLE_RETURNS',
                input_data={'session_id': session_id, 'customer_email': customer_email, 'entities': entities},
                output_data={'response_preview': response[:160], 'order_found': bool(order_info)}
            )
            
        except Exception as e:
            logger.error(f"[Returns Agent] Error handling return: {e}", exc_info=True)
            
            # Send error fallback
            self.bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
                'session_id': context.get('session_id'),
                'text': "I apologize, but I'm having trouble processing your return request. Please contact our support team directly for assistance.",
                'agent': 'RETURNS_AGENT',
                'confidence': 0.5
            })
    
    def _get_last_user_message(self, context: Dict[str, Any]) -> str:
        """Extract the last user message from context"""
        messages = context.get('messages', [])
        for msg in reversed(messages):
            if msg.get('sender') == 'USER':
                return msg.get('text', '')
        return "I want to return an item"
    
    def _retrieve_return_policies(self) -> str:
        """
        Retrieve return policies from knowledge base using RAG.
        
        Returns:
            Concatenated policy text
        """
        if not self.kb_db:
            return "Standard return policy: Items can be returned within 30 days of purchase."
        
        try:
            # TODO: Implement vector search with embeddings
            # For now, return static policy
            self.stats['policies_retrieved'] += 1
            
            return """Return Policy:
- Items can be returned within 30 days of purchase
- Items must be in original condition with tags attached
- Refunds processed within 5-7 business days
- Original shipping costs are non-refundable
- Return shipping is free for defective items"""
            
        except Exception as e:
            logger.error(f"Error retrieving policies: {e}")
            return "Please contact support for return policy details."
    
    def _get_order_info(self, email: Optional[str], order_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Get order information from database.
        
        Args:
            email: Customer email
            order_id: Order ID (if available)
        
        Returns:
            Order dict or None
        """
        if not self.orders_db:
            return None
        
        try:
            if order_id:
                # Look up specific order
                return self.orders_db.get_order(order_id)
            elif email:
                # Get customer's recent orders
                orders = self.orders_db.get_orders_by_email(email)
                return orders[0] if orders else None
        except Exception as e:
            logger.error(f"Error fetching order: {e}")
        
        return None
    
    def _generate_response(
        self,
        user_query: str,
        context: Dict[str, Any],
        knowledge: str,
        order_info: Optional[Dict[str, Any]]
    ) -> str:
        """
        Generate response using Gemini.
        
        Args:
            user_query: User's message
            context: Full conversation context
            knowledge: Retrieved policy information
            order_info: Order details (if available)
        
        Returns:
            Generated response text
        """
        # Build enhanced context for Gemini
        #TODO: Give last 2-3 messages as context & modify template to instruct Gemini to use them
        enhanced_context = {
            'customer_email': context.get('customer_email'),
            'current_intent': context.get('current_intent'),
            'current_sentiment': context.get('current_sentiment'),
            'entities': context.get('entities', {}),
            'order_info': order_info
        }
        
        template = RETURNS_RESPONSE_TEMPLATE
        
        if self.gemini:
            return self.gemini.generate_response(
                user_query=user_query,
                context=enhanced_context,
                knowledge=knowledge,
                template=template
            )
        else:
            # Fallback response
            return self._fallback_response(order_info)
    
    def _fallback_response(self, order_info: Optional[Dict[str, Any]]) -> str:
        """Simple fallback when Gemini unavailable"""
        if order_info:
            return f"I can help you return items from order {order_info.get('order_id')}. Our return policy allows returns within 30 days. Please provide details about which item you'd like to return."
        else:
            return "I can help you with your return. Our policy allows returns within 30 days of purchase. Please provide your order number so I can look up your order details."
    
    def get_stats(self) -> Dict[str, int]:
        """Get agent statistics"""
        return self.stats.copy()


if __name__ == "__main__":
    """Standalone test"""
    print("=== Returns Agent Test ===\n")
    
    # Load environment variables
    try:
        from dotenv import load_dotenv
        load_dotenv()
        print("✅ Environment variables loaded\n")
    except ImportError:
        print("⚠️  python-dotenv not installed\n")
    
    from event_bus import EventBus
    
    bus = EventBus()
    agent = ReturnsAgent(bus)
    
    # Subscribe to results
    def handle_response(event):
        print(f"Response: {event.payload['text']}")
    
    bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', handle_response)
    
    # Test return request
    bus.publish('TASK_HANDLE_RETURNS', {
        'session_id': 'test-001',
        'customer_email': 'customer@example.com',
        'current_intent': 'process_return',
        'current_sentiment': 'NEUTRAL',
        'entities': {'product': 'laptop'},
        'messages': [
            {'sender': 'USER', 'text': 'I want to return my laptop'}
        ]
    })
    
    print("\n=== Statistics ===")
    print(agent.get_stats())