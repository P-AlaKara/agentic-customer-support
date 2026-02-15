"""
Sentiment Recognition Agent

This agent analyzes the emotional tone of user messages to determine if
the conversation should continue or be escalated to a human operator.

Design Decisions:
- Uses both rule-based and ML approaches (configurable)
- Fast and lightweight for the "hot path"
- Returns sentiment label + confidence score
- Subscribes to: TASK_RECOGNIZE_SENTIMENT
- Publishes: RESULT_SENTIMENT_RECOGNIZED
"""

import logging
from typing import Dict, Any, Optional
import re

# Import handling for both standalone and package execution
try:
    # Try relative import (when run as part of package)
    from ..event_bus import EventBus, Event
except (ImportError, ValueError):
    # Fall back to direct import (when run standalone)
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from event_bus import EventBus, Event


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SentimentAgent:
    """
    Sentiment Recognition Agent for customer support conversations.
    
    This is the first "gate" in the workflow - it analyzes the emotional
    tone of the user's message to determine if human intervention is needed.
    
    Sentiment Labels:
    - POSITIVE: Happy, satisfied customer
    - NEUTRAL: Calm, matter-of-fact tone
    - NEGATIVE: Disappointed, unhappy
    - ANGRY: Frustrated, hostile
    - URGENT: Time-sensitive, needs immediate attention
    
    Current Implementation:
    - Rule-based keyword matching (fast, no external dependencies)
    - Can be upgraded to transformer-based model later
    """
    
    # Keyword dictionaries for rule-based classification
    ANGRY_KEYWORDS = [
        'angry', 'furious', 'outraged', 'livid', 'enraged',
        'hate', 'terrible', 'worst', 'awful', 'horrible',
        'disgusting', 'unacceptable', 'ridiculous', 'pathetic',
        'scam', 'fraud', 'steal', 'rip off', 'ripped off'
    ]
    
    NEGATIVE_KEYWORDS = [
        'bad', 'poor', 'disappointed', 'unhappy', 'frustrated',
        'upset', 'annoyed', 'dissatisfied', 'unsatisfied',
        'problem', 'issue', 'complaint', 'wrong', 'broken',
        'not working', 'doesn\'t work', 'failed'
    ]
    
    URGENT_KEYWORDS = [
        'urgent', 'asap', 'immediately', 'right now', 'now',
        'emergency', 'critical', 'urgent matter', 'time sensitive',
        'deadline', 'expiring', 'expire'
    ]
    
    POSITIVE_KEYWORDS = [
        'great', 'excellent', 'amazing', 'wonderful', 'fantastic',
        'love', 'perfect', 'awesome', 'brilliant', 'thank',
        'appreciate', 'helpful', 'satisfied', 'happy'
    ]
    
    # Intensifiers that boost sentiment
    INTENSIFIERS = ['very', 'extremely', 'really', 'so', 'absolutely', 'totally']
    
    # Negations that can flip sentiment
    NEGATIONS = ['not', 'no', 'never', 'neither', 'nobody', 'nothing', 'don\'t', 'doesn\'t', 'didn\'t']
    
    def __init__(self, event_bus: EventBus, use_ml: bool = False):
        """
        Initialize the Sentiment Agent.
        
        Args:
            event_bus: The event bus for communication
            use_ml: If True, use ML model; if False, use rule-based (default: False)
        """
        self.bus = event_bus
        self.use_ml = use_ml
        
        # Statistics
        self.stats = {
            'total_analyzed': 0,
            'positive': 0,
            'neutral': 0,
            'negative': 0,
            'angry': 0,
            'urgent': 0
        }
        
        # Subscribe to events
        self.bus.subscribe('TASK_RECOGNIZE_SENTIMENT', self.analyze_sentiment)
        
        logger.info(f"SentimentAgent initialized (mode: {'ML' if use_ml else 'rule-based'})")
    
    def analyze_sentiment(self, event: Event):
        """
        Main handler: Analyze sentiment of a user message.
        
        Expected event payload:
        {
            'session_id': str,
            'text': str
        }
        
        Publishes:
        {
            'session_id': str,
            'sentiment': str,
            'confidence': float,
            'details': dict (optional)
        }
        """
        try:
            payload = event.payload
            session_id = payload['session_id']
            text = payload['text']
            
            logger.info(f"[Sentiment Agent] Analyzing message from session {session_id}")
            logger.debug(f"[Sentiment Agent] Text: '{text}'")
            
            # Analyze sentiment
            if self.use_ml:
                result = self._analyze_with_ml(text)
            else:
                result = self._analyze_with_rules(text)
            
            sentiment = result['sentiment']
            confidence = result['confidence']
            
            # Update statistics
            self.stats['total_analyzed'] += 1
            self.stats[sentiment.lower()] = self.stats.get(sentiment.lower(), 0) + 1
            
            logger.info(f"[Sentiment Agent] Result: {sentiment} (confidence: {confidence:.2f})")
            
            # Publish result
            self.bus.publish('RESULT_SENTIMENT_RECOGNIZED', {
                'session_id': session_id,
                'sentiment': sentiment,
                'confidence': confidence,
                'details': result.get('details', {})
            })
            
        except Exception as e:
            logger.error(f"[Sentiment Agent] Error analyzing sentiment: {e}", exc_info=True)
            
            # Publish error event
            self.bus.publish('AGENT_ERROR', {
                'session_id': payload.get('session_id'),
                'agent_name': 'SENTIMENT_AGENT',
                'error': str(e),
                'task': 'RECOGNIZE_SENTIMENT'
            })
    
    def _analyze_with_rules(self, text: str) -> Dict[str, Any]:
        """
        Rule-based sentiment analysis using keyword matching.
        
        Algorithm:
        1. Normalize text (lowercase, handle negations)
        2. Count keywords in each category
        3. Check for intensifiers
        4. Apply scoring rules
        5. Return sentiment + confidence
        
        Args:
            text: The message to analyze
        
        Returns:
            {'sentiment': str, 'confidence': float, 'details': dict}
        """
        text_lower = text.lower()
        
        # Tokenize into words
        words = re.findall(r'\b\w+\b', text_lower)
        
        # Count matches in each category
        angry_count = sum(1 for word in self.ANGRY_KEYWORDS if word in text_lower)
        negative_count = sum(1 for word in self.NEGATIVE_KEYWORDS if word in text_lower)
        urgent_count = sum(1 for word in self.URGENT_KEYWORDS if word in text_lower)
        positive_count = sum(1 for word in self.POSITIVE_KEYWORDS if word in text_lower)
        
        # Check for intensifiers (boosts confidence)
        has_intensifier = any(word in words for word in self.INTENSIFIERS)
        intensifier_boost = 0.05 if has_intensifier else 0.0
        
        # Check for negations (can flip sentiment)
        has_negation = any(word in words for word in self.NEGATIONS)
        
        # Handle negation (simple approach: flip positive/negative)
        if has_negation:
            positive_count, negative_count = negative_count, positive_count
        
        # Check for multiple exclamation marks or caps (indicates strong emotion)
        exclamation_count = text.count('!')
        caps_ratio = sum(1 for c in text if c.isupper()) / max(len(text), 1)
        emotion_boost = min(exclamation_count * 0.02 + caps_ratio * 0.1, 0.15)
        
        # Decision logic with confidence scoring
        if angry_count >= 1:
            sentiment = 'ANGRY'
            confidence = min(0.85 + (angry_count - 1) * 0.05 + emotion_boost + intensifier_boost, 0.98)
        
        elif urgent_count >= 1:
            sentiment = 'URGENT'
            confidence = min(0.80 + (urgent_count - 1) * 0.05 + emotion_boost, 0.95)
        
        elif negative_count >= 2:
            sentiment = 'NEGATIVE'
            confidence = min(0.75 + (negative_count - 2) * 0.05 + intensifier_boost, 0.92)
        
        elif positive_count >= 2:
            sentiment = 'POSITIVE'
            confidence = min(0.80 + (positive_count - 2) * 0.05, 0.95)
        
        elif negative_count == 1:
            sentiment = 'NEGATIVE'
            confidence = 0.65  # Lower confidence for single keyword
        
        elif positive_count == 1:
            sentiment = 'POSITIVE'
            confidence = 0.70
        
        else:
            sentiment = 'NEUTRAL'
            confidence = 0.88  # High confidence in neutrality if no keywords found
        
        return {
            'sentiment': sentiment,
            'confidence': confidence,
            'details': {
                'angry_keywords': angry_count,
                'negative_keywords': negative_count,
                'positive_keywords': positive_count,
                'urgent_keywords': urgent_count,
                'has_intensifier': has_intensifier,
                'has_negation': has_negation,
                'emotion_boost': emotion_boost
            }
        }
    
    def _analyze_with_ml(self, text: str) -> Dict[str, Any]:
        """
        ML-based sentiment analysis using a transformer model.
        
        Uses: distilbert-base-uncased-finetuned-sst-2-english
        - Lightweight (67M parameters vs 110M for BERT)
        - Fast inference
        - Good accuracy for sentiment
        
        Fallback: Rule-based if model not available
        
        Args:
            text: The message to analyze
        
        Returns:
            {'sentiment': str, 'confidence': float, 'details': dict}
        """
        try:
            from transformers import pipeline
            
            # Initialize sentiment pipeline (cached after first call)
            if not hasattr(self, '_sentiment_pipeline'):
                logger.info("[Sentiment Agent] Loading ML model (distilbert-sst-2)...")
                self._sentiment_pipeline = pipeline(
                    "sentiment-analysis",
                    model="distilbert-base-uncased-finetuned-sst-2-english",
                    device=-1  # CPU (-1), or 0 for GPU
                )
            
            # Run inference
            result = self._sentiment_pipeline(text[:512])[0]  # Limit to 512 tokens
            
            # Map model output to our sentiment labels
            # Model returns: POSITIVE or NEGATIVE
            label = result['label']
            score = result['score']
            
            if label == 'NEGATIVE' and score >= 0.90:
                sentiment = 'ANGRY'
                confidence = min(score + 0.05, 0.98)
            elif label == 'NEGATIVE':
                sentiment = 'NEGATIVE'
                confidence = score
            elif label == 'POSITIVE' and score >= 0.90:
                sentiment = 'POSITIVE'
                confidence = score
            else:
                sentiment = 'NEUTRAL'
                confidence = 1.0 - score  # Uncertainty means neutral
            
            # Check for urgency keywords (model doesn't detect this)
            if any(word in text.lower() for word in self.URGENT_KEYWORDS):
                sentiment = 'URGENT'
                confidence = min(confidence + 0.1, 0.95)
            
            return {
                'sentiment': sentiment,
                'confidence': confidence,
                'details': {
                    'model': 'distilbert-sst-2',
                    'raw_label': label,
                    'raw_score': score
                }
            }
            
        except ImportError:
            logger.warning("[Sentiment Agent] transformers not installed, falling back to rules")
            return self._analyze_with_rules(text)
        
        except Exception as e:
            logger.error(f"[Sentiment Agent] ML inference error: {e}, falling back to rules")
            return self._analyze_with_rules(text)
    
    def get_stats(self) -> Dict[str, int]:
        """Get sentiment analysis statistics"""
        return self.stats.copy()


# For standalone testing
if __name__ == "__main__":
    """Demo/test the sentiment agent"""
    print("=== Sentiment Agent Demo ===\n")
    
    # Need to import from parent for standalone execution
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from event_bus import EventBus
    
    bus = EventBus()
    agent = SentimentAgent(bus)
    
    # Test cases
    test_messages = [
        "I want to return my laptop",
        "This is terrible! I am so angry about this product!",
        "I need help ASAP, this is urgent!",
        "Thank you so much, you've been very helpful!",
        "This product is not bad at all",  # Negation test
        "HELP ME NOW!! This is UNACCEPTABLE!!!",  # Emotion test
    ]
    
    # Mock result handler
    results = []
    def handle_result(event):
        results.append(event.payload)
        print(f"Result: {event.payload['sentiment']} (confidence: {event.payload['confidence']:.2f})")
        print(f"Details: {event.payload['details']}\n")
    
    bus.subscribe('RESULT_SENTIMENT_RECOGNIZED', handle_result)
    
    # Test each message
    for i, msg in enumerate(test_messages):
        print(f"Test {i+1}: '{msg}'")
        bus.publish('TASK_RECOGNIZE_SENTIMENT', {
            'session_id': f'test-{i}',
            'text': msg
        })
    
    print("\n=== Statistics ===")
    print(agent.get_stats())