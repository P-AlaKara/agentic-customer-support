"""
Intent Recognition Agent

Classifies user queries into specific intents to route to the appropriate
Business Process Agent.

Supported Intents:
- track_order: User wants to check order status/shipping
- process_return: User wants to return/refund an item
- account_issues: User needs help with account/login/profile
- general_inquiry: Catch-all for unclear requests

Design Decisions:
- Rule-based keyword matching (fast, no dependencies)
- Can be upgraded to ML/LLM later
- Returns confidence score for escalation decisions
"""

import logging
from typing import Dict, Any, List, Tuple
import re

# Flexible import pattern
try:
    from ..event_bus import EventBus, Event
except (ImportError, ValueError):
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from event_bus import EventBus, Event


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class IntentAgent:
    """
    Intent Recognition Agent for customer support.
    
    Classifies user messages into actionable intents using keyword matching
    and pattern recognition.
    """
    
    # Intent keyword mappings
    INTENT_KEYWORDS = {
        'track_order': {
            'primary': ['track', 'tracking', 'shipped', 'shipping', 'delivery', 'deliver'],
            'secondary': ['where is', 'status', 'arrive', 'arriving', 'eta', 'when will'],
            'entities': ['order', 'package', 'shipment']
        },
        'process_return': {
            'primary': ['return', 'refund', 'exchange', 'replace', 'send back', 'take back'],
            'secondary': ['give back', 'money back'],
            'entities': ['item', 'product', 'purchase']
        },
        'account_issues': {
            'primary': ['account', 'login', 'password', 'sign in', 'log in'],
            'secondary': ['email', 'profile', 'username', 'change', 'update', 'reset'],
            'entities': ['credentials', 'access', 'settings']
        }
    }
    
    # Common phrases that indicate specific intents
    INTENT_PHRASES = {
        'track_order': [
            'where is my order',
            'track my order',
            'order status',
            'shipping status',
            'when will it arrive',
            'has it shipped',
            'tracking number',
            'delivery date'
        ],
        'process_return': [
            'want to return',
            'need to return',
            'return this',
            'get a refund',
            'send it back',
            'not satisfied',
            'wrong item',
            'defective'
        ],
        'account_issues': [
            'can\'t log in',
            'cannot log in',
            'forgot password',
            'reset password',
            'update email',
            'change password',
            'account locked',
            'can\'t access'
        ]
    }
    
    # Question patterns
    QUESTION_PATTERNS = {
        'track_order': [
            r'\bwhere\s+(is|are)\s+(my|the)?\s*(order|package|shipment)',
            r'\bwhen\s+will\s+(it|my\s+order|the\s+package)\s+(arrive|come|ship)',
            r'\b(has|did)\s+(it|my\s+order)\s+ship',
        ],
        'process_return': [
            r'\b(want|need|would\s+like)\s+to\s+return',
            r'\bhow\s+(do\s+i|can\s+i|to)\s+return',
            r'\bcan\s+i\s+(get|have)\s+a\s+refund',
        ],
        'account_issues': [
            r'\bcan\'?t\s+(log\s+in|access|sign\s+in)',
            r'\b(forgot|lost|reset)\s+(my\s+)?password',
            r'\bhow\s+(do\s+i|can\s+i|to)\s+(change|update|reset)\s+(my\s+)?(password|email)',
        ]
    }
    
    def __init__(self, event_bus: EventBus, use_ml: bool = False):
        """
        Initialize the Intent Agent.
        
        Args:
            event_bus: The event bus for communication
            use_ml: If True, use ML model; if False, use rule-based (default: False)
        """
        self.bus = event_bus
        self.use_ml = use_ml
        
        # Statistics
        self.stats = {
            'total_analyzed': 0,
            'track_order': 0,
            'process_return': 0,
            'account_issues': 0,
            'general_inquiry': 0,
            'high_confidence': 0,
            'low_confidence': 0
        }
        
        # Subscribe to events
        self.bus.subscribe('TASK_RECOGNIZE_INTENT', self.recognize_intent)
        
        logger.info(f"IntentAgent initialized (mode: {'ML' if use_ml else 'rule-based'})")
    
    def recognize_intent(self, event: Event):
        """
        Main handler: Classify the intent of a user message.
        
        Expected event payload:
        {
            'session_id': str,
            'text': str,
            'conversation_history': list (optional)
        }
        
        Publishes:
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
            text = payload['text']
            history = payload.get('conversation_history', [])
            
            logger.info(f"[Intent Agent] Analyzing message from session {session_id}")
            logger.debug(f"[Intent Agent] Text: '{text}'")
            
            # Classify intent
            if self.use_ml:
                result = self._classify_with_ml(text, history)
            else:
                result = self._classify_with_rules(text)
            
            intent = result['intent']
            confidence = result['confidence']
            entities = result.get('entities', {})
            
            # Update statistics
            self.stats['total_analyzed'] += 1
            self.stats[intent] = self.stats.get(intent, 0) + 1
            if confidence >= 0.7:
                self.stats['high_confidence'] += 1
            else:
                self.stats['low_confidence'] += 1
            
            logger.info(f"[Intent Agent] Result: {intent} (confidence: {confidence:.2f})")
            
            # Publish result
            self.bus.publish('RESULT_INTENT_RECOGNIZED', {
                'session_id': session_id,
                'intent': intent,
                'confidence': confidence,
                'entities': entities
            })
            
        except Exception as e:
            logger.error(f"[Intent Agent] Error classifying intent: {e}", exc_info=True)
            
            # Publish error event
            self.bus.publish('AGENT_ERROR', {
                'session_id': payload.get('session_id'),
                'agent_name': 'INTENT_AGENT',
                'error': str(e),
                'task': 'RECOGNIZE_INTENT'
            })
    
    def _classify_with_rules(self, text: str) -> Dict[str, Any]:
        """
        Rule-based intent classification.
        
        Algorithm:
        1. Check for exact phrase matches (highest confidence)
        2. Check for regex pattern matches
        3. Count keyword matches per intent
        4. Calculate confidence based on matches
        
        Args:
            text: The message to classify
        
        Returns:
            {'intent': str, 'confidence': float, 'entities': dict}
        """
        text_lower = text.lower()
        
        # Step 1: Check for exact phrase matches
        phrase_scores = {}
        for intent, phrases in self.INTENT_PHRASES.items():
            for phrase in phrases:
                if phrase in text_lower:
                    phrase_scores[intent] = phrase_scores.get(intent, 0) + 1
        
        if phrase_scores:
            best_intent = max(phrase_scores, key=phrase_scores.get)
            confidence = min(0.85 + phrase_scores[best_intent] * 0.05, 0.98)
            return {
                'intent': best_intent,
                'confidence': confidence,
                'entities': self._extract_entities(text_lower, best_intent)
            }
        
        # Step 2: Check for regex pattern matches
        pattern_matches = {}
        for intent, patterns in self.QUESTION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    pattern_matches[intent] = pattern_matches.get(intent, 0) + 1
        
        if pattern_matches:
            best_intent = max(pattern_matches, key=pattern_matches.get)
            confidence = min(0.80 + pattern_matches[best_intent] * 0.05, 0.95)
            return {
                'intent': best_intent,
                'confidence': confidence,
                'entities': self._extract_entities(text_lower, best_intent)
            }
        
        # Step 3: Count keyword matches
        keyword_scores = {}
        for intent, keywords in self.INTENT_KEYWORDS.items():
            score = 0
            
            # Primary keywords worth more
            for kw in keywords['primary']:
                if kw in text_lower:
                    score += 2
            
            # Secondary keywords
            for kw in keywords['secondary']:
                if kw in text_lower:
                    score += 1
            
            # Entity mentions
            for entity in keywords['entities']:
                if entity in text_lower:
                    score += 0.5
            
            if score > 0:
                keyword_scores[intent] = score
        
        if keyword_scores:
            best_intent = max(keyword_scores, key=keyword_scores.get)
            max_score = keyword_scores[best_intent]
            
            # Calculate confidence based on score
            if max_score >= 3:
                confidence = min(0.75 + (max_score - 3) * 0.05, 0.92)
            elif max_score >= 2:
                confidence = 0.70
            else:
                confidence = 0.65
            
            return {
                'intent': best_intent,
                'confidence': confidence,
                'entities': self._extract_entities(text_lower, best_intent)
            }
        
        # Default: general_inquiry with low confidence
        return {
            'intent': 'general_inquiry',
            'confidence': 0.60,
            'entities': {}
        }
    
    def _extract_entities(self, text: str, intent: str) -> Dict[str, Any]:
        """
        Extract relevant entities based on intent.
        
        Args:
            text: The message text (lowercase)
            intent: The classified intent
        
        Returns:
            Dictionary of extracted entities
        """
        entities = {'action': intent}
        
        # Extract order numbers (pattern: digits, letters, dashes)
        order_match = re.search(r'\b(order|#)\s*[:#]?\s*([A-Z0-9-]{5,})\b', text, re.IGNORECASE)
        if order_match:
            entities['order_id'] = order_match.group(2)
        
        # Extract email addresses
        email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', text)
        if email_match:
            entities['email'] = email_match.group(0)
        
        # Intent-specific entities
        if intent == 'process_return':
            # Look for product mentions
            product_keywords = ['laptop', 'phone', 'tablet', 'watch', 'shirt', 'shoes', 'dress']
            for product in product_keywords:
                if product in text:
                    entities['product'] = product
                    break
        
        elif intent == 'account_issues':
            # Look for account actions
            if 'password' in text:
                entities['issue_type'] = 'password'
            elif 'email' in text:
                entities['issue_type'] = 'email'
            elif 'login' in text or 'log in' in text or 'sign in' in text:
                entities['issue_type'] = 'login'
        
        return entities
    
    def _classify_with_ml(self, text: str, history: List[str]) -> Dict[str, Any]:
        """
        ML-based intent classification (placeholder).
        
        Options for implementation:
        1. Zero-shot classification (transformers)
        2. Fine-tuned BERT/RoBERTa
        3. LLM-based classification (GPT/Claude)
        
        Args:
            text: The message to classify
            history: Previous messages for context
        
        Returns:
            {'intent': str, 'confidence': float, 'entities': dict}
        """
        # TODO: Implement ML-based classification
        logger.warning("[Intent Agent] ML mode not implemented, falling back to rules")
        return self._classify_with_rules(text)
    
    def get_stats(self) -> Dict[str, int]:
        """Get intent classification statistics"""
        return self.stats.copy()


if __name__ == "__main__":
    """Standalone test"""
    print("=== Intent Agent Standalone Test ===\n")
    
    from event_bus import EventBus
    
    bus = EventBus()
    agent = IntentAgent(bus)
    
    test_cases = [
        "I want to return my laptop",
        "Where is my order?",
        "I can't log into my account",
        "Track order #12345",
        "Need to reset my password",
        "Send this item back",
        "Can you help me?",
    ]
    
    results = []
    def handle_result(event):
        results.append(event.payload)
        print(f"Intent: {event.payload['intent']} (confidence: {event.payload['confidence']:.2f})")
        print(f"Entities: {event.payload['entities']}\n")
    
    bus.subscribe('RESULT_INTENT_RECOGNIZED', handle_result)
    
    for i, msg in enumerate(test_cases):
        print(f"Test {i+1}: '{msg}'")
        bus.publish('TASK_RECOGNIZE_INTENT', {
            'session_id': f'test-{i}',
            'text': msg
        })
    
    print("=== Statistics ===")
    print(agent.get_stats())