"""
Simple, in-memory publish-subscribe event bus that allows
agents to communicate asynchronously without direct coupling.

Design Decisions:
- Thread-safe using threading.Lock for concurrent access
- Supports multiple subscribers per event type
- Includes error handling to prevent one failing subscriber from breaking others
- Provides logging for debugging and monitoring
"""

import logging
from typing import Callable, Dict, List, Any
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
import json


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class Event:
    """
    Represents a single event in the system.
    
    Attributes:
        event_type: The type/name of the event (e.g., 'NEW_USER_MESSAGE')
        payload: The data associated with this event (dict)
        timestamp: When the event was created
        event_id: Optional unique identifier for tracking
    """
    event_type: str
    payload: Dict[str, Any]
    timestamp: datetime
    event_id: str = None
    
    def __post_init__(self):
        """Generate event_id if not provided"""
        if self.event_id is None:
            import uuid
            self.event_id = str(uuid.uuid4())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for logging/debugging"""
        return {
            'event_id': self.event_id,
            'event_type': self.event_type,
            'payload': self.payload,
            'timestamp': self.timestamp.isoformat()
        }


class EventBus:
    """
    A simple, thread-safe in-memory event bus for agent communication.
    
    This implements the publish-subscribe pattern where:
    - Publishers send events without knowing who will receive them
    - Subscribers register callbacks for specific event types
    - The EventBus routes events to all registered subscribers
    
    Example Usage:
        bus = EventBus()
        
        # Subscribe to an event
        def handle_message(event):
            print(f"Received: {event.payload}")
        
        bus.subscribe('NEW_USER_MESSAGE', handle_message)
        
        # Publish an event
        bus.publish('NEW_USER_MESSAGE', {'text': 'Hello', 'session_id': '123'})
    """
    
    def __init__(self):
        """Initialize the event bus with empty subscriber registry"""
        # Dictionary mapping event_type -> list of callback functions
        self._subscribers: Dict[str, List[Callable]] = {}
        
        # Thread lock for safe concurrent access
        self._lock = Lock()
        
        # Statistics for monitoring
        self._stats = {
            'published': 0,
            'delivered': 0,
            'errors': 0
        }
        
        logger.info("EventBus initialized")
    
    def subscribe(self, event_type: str, callback: Callable[[Event], None]) -> None:
        """
        Subscribe to a specific event type.
        
        Args:
            event_type: The type of event to listen for (e.g., 'TASK_RECOGNIZE_SENTIMENT')
            callback: Function to call when this event is published.
                     Must accept one argument: the Event object
        
        Example:
            def my_handler(event):
                print(event.payload)
            
            bus.subscribe('MY_EVENT', my_handler)
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            
            self._subscribers[event_type].append(callback)
            logger.info(f"Subscribed to '{event_type}'. Total subscribers: {len(self._subscribers[event_type])}")
    
    def unsubscribe(self, event_type: str, callback: Callable[[Event], None]) -> None:
        """
        Unsubscribe a specific callback from an event type.
        
        Args:
            event_type: The event type to unsubscribe from
            callback: The exact callback function to remove
        """
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(callback)
                    logger.info(f"Unsubscribed from '{event_type}'")
                except ValueError:
                    logger.warning(f"Callback not found for '{event_type}'")
    
    def publish(self, event_type: str, payload: Dict[str, Any]) -> Event:
        """
        Publish an event to all subscribers.
        
        Args:
            event_type: The type of event being published
            payload: The data to send with this event (must be a dict)
        
        Returns:
            The Event object that was created and published
        
        Example:
            bus.publish('RESULT_SENTIMENT_RECOGNIZED', {
                'session_id': '123',
                'sentiment': 'POSITIVE',
                'confidence': 0.92
            })
        """
        # Create the event
        event = Event(
            event_type=event_type,
            payload=payload,
            timestamp=datetime.utcnow()
        )
        
        self._stats['published'] += 1
        logger.info(f"Publishing event '{event_type}' [ID: {event.event_id}]")
        logger.debug(f"Event payload: {json.dumps(payload, indent=2)}")
        
        # Get subscribers (make a copy to avoid holding lock during callbacks)
        with self._lock:
            subscribers = self._subscribers.get(event_type, []).copy()
        
        # Deliver to all subscribers
        if not subscribers:
            logger.warning(f"No subscribers for event type '{event_type}'")
        else:
            for callback in subscribers:
                try:
                    callback(event)
                    self._stats['delivered'] += 1
                except Exception as e:
                    # Log error but continue delivering to other subscribers
                    self._stats['errors'] += 1
                    logger.error(
                        f"Error in subscriber callback for '{event_type}': {e}",
                        exc_info=True
                    )
        
        return event
    
    def get_stats(self) -> Dict[str, int]:
        """
        Get statistics about event bus usage.
        
        Returns:
            Dictionary with 'published', 'delivered', and 'errors' counts
        """
        return self._stats.copy()
    
    def get_subscribers(self, event_type: str = None) -> Dict[str, int]:
        """
        Get information about current subscribers.
        
        Args:
            event_type: If provided, returns count for that event type only.
                       If None, returns counts for all event types.
        
        Returns:
            Dictionary mapping event_type -> subscriber count
        """
        with self._lock:
            if event_type:
                return {event_type: len(self._subscribers.get(event_type, []))}
            else:
                return {
                    event_type: len(callbacks) 
                    for event_type, callbacks in self._subscribers.items()
                }
    
    def clear_all_subscribers(self) -> None:
        """
        Remove all subscribers. Useful for testing or shutdown.
        """
        with self._lock:
            count = sum(len(subs) for subs in self._subscribers.values())
            self._subscribers.clear()
            logger.info(f"Cleared all subscribers (removed {count} callbacks)")


# Global singleton instance (optional - you can also instantiate as needed)
_global_event_bus = None


def get_event_bus() -> EventBus:
    """
    Get the global singleton EventBus instance.
    
    This pattern ensures all agents use the same event bus.
    
    Returns:
        The global EventBus instance
    """
    global _global_event_bus
    if _global_event_bus is None:
        _global_event_bus = EventBus()
    return _global_event_bus


if __name__ == "__main__":
    """
    Demo/test code showing how to use the EventBus
    """
    print("=== EventBus Demo ===\n")
    
    # Create event bus
    bus = EventBus()
    
    # Define some sample handlers
    def sentiment_handler(event: Event):
        print(f"[Sentiment Agent] Received: {event.payload}")
        # Simulate processing
        sentiment = "POSITIVE" if "good" in event.payload.get('text', '').lower() else "NEUTRAL"
        # Publish result
        bus.publish('RESULT_SENTIMENT_RECOGNIZED', {
            'session_id': event.payload['session_id'],
            'sentiment': sentiment
        })
    
    def coordinator_handler(event: Event):
        print(f"[Coordinator] Sentiment result: {event.payload['sentiment']}")
    
    # Subscribe
    bus.subscribe('TASK_RECOGNIZE_SENTIMENT', sentiment_handler)
    bus.subscribe('RESULT_SENTIMENT_RECOGNIZED', coordinator_handler)
    
    # Publish an event
    print("\n--- Publishing Event ---")
    bus.publish('TASK_RECOGNIZE_SENTIMENT', {
        'session_id': 'test-123',
        'text': 'This product is really good!'
    })
    
    print("\n--- Stats ---")
    print(bus.get_stats())
    print("\n--- Subscribers ---")
    print(bus.get_subscribers())