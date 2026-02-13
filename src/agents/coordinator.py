"""
The Coordinator is the central orchestrator that manages the conversation workflow.
It does NOT perform complex logic itself - it delegates to specialist agents.

Workflow Gates:
1. Gate 0: New message received → Trigger sentiment analysis
2. Gate 1: Sentiment check → If OK, trigger intent recognition; else escalate
3. Gate 2: Intent confidence check → If high, route to BPA; else escalate
4. Gate 3: BPA handles the query and responds directly to user

Design Decisions:
- Delegates all "heavy lifting" to specialist agents
- Manages Context Store updates
- If a specialist agent fails, Coordinator logs and can escalate
- Does NOT pass responses back through itself (BPAs respond directly to user)
"""

import logging
from typing import Optional, Dict, Any
from event_bus import EventBus, Event
from context_store import ContextStore, ConversationStatus


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CoordinatorAgent:
    """
    Central orchestrator for the multi-agent customer support system.
    
    Responsibilities:
    1. Receive new user messages
    2. Trigger sentiment analysis (Gate 1)
    3. Check sentiment and decide to continue or escalate
    4. Trigger intent recognition (Gate 2)
    5. Check intent confidence and decide to route or escalate
    6. Route to appropriate Business Process Agent
    7. Handle escalation events
    """
    
    # Configuration thresholds
    SENTIMENT_ESCALATION_LABELS = ['NEGATIVE', 'ANGRY']
    INTENT_CONFIDENCE_THRESHOLD = 0.7
    
    # Intent to agent task mapping
    INTENT_ROUTING = {
        'track_order': 'TASK_HANDLE_ORDER_TRACKING',
        'process_return': 'TASK_HANDLE_RETURNS',
        'general_inquiry': 'TASK_HANDLE_GENERAL_INQUIRY',
        'update_account': 'TASK_HANDLE_ACCOUNT'
    }
    
    def __init__(self, event_bus: EventBus, context_store: ContextStore):
        """
        Initialize the Coordinator Agent.
        
        Args:
            event_bus: The event bus for pub/sub communication
            context_store: The context store for session management
        """
        self.bus = event_bus
        self.store = context_store
        
        # Statistics
        self.stats = {
            'messages_processed': 0,
            'escalations': 0,
            'successful_routes': 0,
            'errors': 0
        }
        
        # Subscribe to events
        self._subscribe_to_events()
        
        logger.info("CoordinatorAgent initialized and subscribed to events")
    
    def _subscribe_to_events(self):
        """Subscribe to all events this agent needs to handle"""
        self.bus.subscribe('NEW_USER_MESSAGE', self.handle_new_message)
        self.bus.subscribe('RESULT_SENTIMENT_RECOGNIZED', self.handle_sentiment_result)
        self.bus.subscribe('RESULT_INTENT_RECOGNIZED', self.handle_intent_result)
        self.bus.subscribe('REQUEST_ESCALATION', self.handle_escalation_request)
        self.bus.subscribe('AGENT_ERROR', self.handle_agent_error)
    
    # ========================================================================
    # GATE 0: NEW MESSAGE RECEIVED
    # ========================================================================
    
    def handle_new_message(self, event: Event):
        """
        Gate 0: Handle a new user message from the API Gateway.
        
        Flow:
        1. Get or create session context
        2. Add message to context
        3. Publish task to Sentiment Agent
        
        Expected event payload:
        {
            'session_id': str,
            'text': str,
            'customer_email': str (optional),
            'timestamp': str (optional)
        }
        """
        try:
            payload = event.payload
            session_id = payload['session_id']
            text = payload['text']
            customer_email = payload.get('customer_email')
            
            logger.info(f"[GATE 0] New message from session {session_id}")
            self.stats['messages_processed'] += 1
            
            # Get or create context
            context = self.store.get_or_create(
                session_id=session_id,
                customer_email=customer_email
            )
            
            # Add user message to context
            self.store.add_message(
                session_id=session_id,
                sender='USER',
                text=text
            )
            
            logger.info(f"[GATE 0] Message added to context. Total messages: {len(context.messages)}")
            
            # Start the workflow: Trigger Sentiment Analysis
            logger.info(f"[GATE 0] → Publishing TASK_RECOGNIZE_SENTIMENT")
            self.bus.publish('TASK_RECOGNIZE_SENTIMENT', {
                'session_id': session_id,
                'text': text
            })
            
        except Exception as e:
            logger.error(f"[GATE 0] Error handling new message: {e}", exc_info=True)
            self.stats['errors'] += 1
            self._emergency_escalate(event.payload.get('session_id'), str(e))
    
    # ========================================================================
    # GATE 1: SENTIMENT CHECK
    # ========================================================================
    
    def handle_sentiment_result(self, event: Event):
        """
        Gate 1: Handle sentiment analysis result.
        
        Flow:
        1. Update context with sentiment
        2. Check if sentiment is acceptable
        3. If NEGATIVE/ANGRY → Escalate
        4. If OK → Proceed to Intent Recognition
        
        Expected event payload:
        {
            'session_id': str,
            'sentiment': str,
            'confidence': float (optional)
        }
        """
        try:
            payload = event.payload
            session_id = payload['session_id']
            sentiment = payload['sentiment']
            confidence = payload.get('confidence', 0.0)
            
            logger.info(f"[GATE 1] Sentiment result for {session_id}: {sentiment} (confidence: {confidence:.2f})")
            
            # Update context
            context = self.store.get(session_id)
            if context is None:
                logger.error(f"[GATE 1] Session {session_id} not found in context store")
                return
            
            context.update_sentiment(sentiment, confidence)
            
            # Gate 1 Decision: Check sentiment
            if sentiment in self.SENTIMENT_ESCALATION_LABELS:
                logger.warning(f"[GATE 1] ⚠️  Negative sentiment detected: {sentiment}")
                self._escalate(
                    session_id=session_id,
                    reason=f"NEGATIVE_SENTIMENT_{sentiment}",
                    details={'sentiment': sentiment, 'confidence': confidence}
                )
                return
            
            # Sentiment OK - proceed to Intent Recognition
            logger.info(f"[GATE 1] ✓ Sentiment acceptable: {sentiment}")
            logger.info(f"[GATE 1] → Publishing TASK_RECOGNIZE_INTENT")
            
            # Get the latest user message for intent analysis
            last_message = context.messages[-1].text if context.messages else ""
            
            self.bus.publish('TASK_RECOGNIZE_INTENT', {
                'session_id': session_id,
                'text': last_message,
                'conversation_history': context.get_user_message_history()  # Optional context
            })
            
        except Exception as e:
            logger.error(f"[GATE 1] Error handling sentiment result: {e}", exc_info=True)
            self.stats['errors'] += 1
            self._emergency_escalate(event.payload.get('session_id'), str(e))
    
    # ========================================================================
    # GATE 2: INTENT CONFIDENCE CHECK
    # ========================================================================
    
    def handle_intent_result(self, event: Event):
        """
        Gate 2: Handle intent recognition result.
        
        Flow:
        1. Update context with intent
        2. Check if confidence is high enough
        3. If low confidence → Escalate
        4. If high confidence → Route to appropriate Business Process Agent
        
        Expected event payload:
        {
            'session_id': str,
            'intent': str,
            'confidence': float,
            'entities': dict (optional)
        }
        """
        try:
            payload = event.payload
            session_id = payload['session_id']
            intent = payload['intent']
            confidence = payload['confidence']
            entities = payload.get('entities', {})
            
            logger.info(f"[GATE 2] Intent result for {session_id}: {intent} (confidence: {confidence:.2f})")
            
            # Update context
            context = self.store.get(session_id)
            if context is None:
                logger.error(f"[GATE 2] Session {session_id} not found in context store")
                return
            
            context.update_intent(intent, confidence)
            if entities:
                context.merge_entities(entities)
            
            # Gate 2 Decision: Check confidence threshold
            if confidence < self.INTENT_CONFIDENCE_THRESHOLD:
                logger.warning(f"[GATE 2] ⚠️  Low confidence: {confidence:.2f} < {self.INTENT_CONFIDENCE_THRESHOLD}")
                self._escalate(
                    session_id=session_id,
                    reason="LOW_INTENT_CONFIDENCE",
                    details={'intent': intent, 'confidence': confidence}
                )
                return
            
            # High confidence - route to Business Process Agent
            logger.info(f"[GATE 2] ✓ High confidence: {confidence:.2f}")
            self._route_to_agent(session_id, intent, context)
            
        except Exception as e:
            logger.error(f"[GATE 2] Error handling intent result: {e}", exc_info=True)
            self.stats['errors'] += 1
            self._emergency_escalate(event.payload.get('session_id'), str(e))
    
    # ========================================================================
    # ROUTING
    # ========================================================================
    
    def _route_to_agent(self, session_id: str, intent: str, context):
        """
        Route the conversation to the appropriate Business Process Agent.
        
        The BPA receives the full context and will respond directly to the
        user via the API Gateway (not back through the Coordinator).
        
        Args:
            session_id: The session ID
            intent: The recognized intent
            context: The full conversation context
        """
        # Look up the task name for this intent
        task_name = self.INTENT_ROUTING.get(intent)
        
        if task_name is None:
            logger.warning(f"[ROUTING] Unknown intent: {intent}")
            self._escalate(
                session_id=session_id,
                reason="UNKNOWN_INTENT",
                details={'intent': intent}
            )
            return
        
        logger.info(f"[ROUTING] Routing session {session_id} to {task_name}")
        self.stats['successful_routes'] += 1
        
        # Publish task with FULL CONTEXT
        # The BPA will handle the query and respond directly to the user
        self.bus.publish(task_name, context.to_dict())
    
    # ========================================================================
    # ESCALATION HANDLING
    # ========================================================================
    
    def handle_escalation_request(self, event: Event):
        """
        Handle explicit escalation requests from other agents.
        
        Expected event payload:
        {
            'session_id': str,
            'reason': str,
            'details': dict (optional),
            'requesting_agent': str (optional)
        }
        """
        payload = event.payload
        session_id = payload['session_id']
        reason = payload['reason']
        details = payload.get('details', {})
        
        logger.info(f"[ESCALATION] Request received for session {session_id}: {reason}")
        self._escalate(session_id, reason, details)
    
    def _escalate(self, session_id: str, reason: str, details: Optional[Dict[str, Any]] = None):
        """
        Escalate a conversation to a human operator.
        
        Args:
            session_id: The session to escalate
            reason: Why the escalation is happening
            details: Additional context about the escalation
        """
        logger.warning(f"[ESCALATION] Escalating session {session_id}: {reason}")
        self.stats['escalations'] += 1
        
        # Update context
        context = self.store.get(session_id)
        if context:
            context.escalate(reason)
        
        # Publish escalation event
        self.bus.publish('TASK_ESCALATE', {
            'session_id': session_id,
            'reason': reason,
            'details': details or {},
            'context': context.to_dict() if context else None
        })
    
    def _emergency_escalate(self, session_id: Optional[str], error: str):
        """Emergency escalation when something goes wrong"""
        if session_id:
            self._escalate(
                session_id=session_id,
                reason="SYSTEM_ERROR",
                details={'error': error}
            )
        else:
            logger.critical(f"Emergency escalation without session_id: {error}")
    
    # ========================================================================
    # ERROR HANDLING
    # ========================================================================
    
    def handle_agent_error(self, event: Event):
        """
        Handle errors reported by other agents.
        
        Expected event payload:
        {
            'session_id': str,
            'agent_name': str,
            'error': str,
            'task': str (optional)
        }
        """
        payload = event.payload
        session_id = payload['session_id']
        agent_name = payload['agent_name']
        error = payload['error']
        
        logger.error(f"[ERROR] Agent {agent_name} reported error for session {session_id}: {error}")
        self.stats['errors'] += 1
        
        # Escalate due to agent failure
        self._escalate(
            session_id=session_id,
            reason=f"AGENT_ERROR_{agent_name}",
            details={'agent': agent_name, 'error': error}
        )
    
    # ========================================================================
    # STATISTICS & MONITORING
    # ========================================================================
    
    def get_stats(self) -> Dict[str, int]:
        """Get coordinator statistics"""
        return {
            **self.stats,
            'active_sessions': self.store.get_active_count()
        }
    
    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific session"""
        context = self.store.get(session_id)
        return context.to_dict() if context else None


if __name__ == "__main__":
    """Demo/test code"""
    print("=== CoordinatorAgent Demo ===\n")
    
    from event_bus import get_event_bus
    from context_store import get_context_store
    
    # Initialize
    bus = get_event_bus()
    store = get_context_store()
    coordinator = CoordinatorAgent(bus, store)
    
    # Mock sentiment agent
    def mock_sentiment_agent(event: Event):
        print(f"\n[MOCK Sentiment Agent] Analyzing: {event.payload['text']}")
        bus.publish('RESULT_SENTIMENT_RECOGNIZED', {
            'session_id': event.payload['session_id'],
            'sentiment': 'NEUTRAL',
            'confidence': 0.89
        })
    
    # Mock intent agent
    def mock_intent_agent(event: Event):
        print(f"\n[MOCK Intent Agent] Analyzing: {event.payload['text']}")
        bus.publish('RESULT_INTENT_RECOGNIZED', {
            'session_id': event.payload['session_id'],
            'intent': 'process_return',
            'confidence': 0.95
        })
    
    # Mock BPA
    def mock_returns_agent(event: Event):
        context = event.payload
        print(f"\n[MOCK Returns Agent] Handling return for session {context['session_id']}")
        bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
            'session_id': context['session_id'],
            'text': 'I can help with your return!',
            'agent': 'RETURNS_AGENT'
        })
    
    # Subscribe mocks
    bus.subscribe('TASK_RECOGNIZE_SENTIMENT', mock_sentiment_agent)
    bus.subscribe('TASK_RECOGNIZE_INTENT', mock_intent_agent)
    bus.subscribe('TASK_HANDLE_RETURNS', mock_returns_agent)
    
    # Simulate workflow
    print("\n" + "="*60)
    print("SIMULATING WORKFLOW")
    print("="*60)
    
    bus.publish('NEW_USER_MESSAGE', {
        'session_id': 'test-session-001',
        'text': 'I want to return my laptop',
        'customer_email': 'user@example.com'
    })
    
    import time
    time.sleep(0.3)
    
    print("\n" + "="*60)
    print("COORDINATOR STATISTICS")
    print("="*60)
    print(coordinator.get_stats())