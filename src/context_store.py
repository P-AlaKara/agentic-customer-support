"""
This is an in-memory store that manages the "hot path" session state for
active conversations. It holds all conversation data in memory for fast access
during a live interaction.

Design Decisions:
- In-memory storage for performance (no database I/O during active sessions)
- Thread-safe using threading.Lock for concurrent access
- Session cleanup method for memory management
- Will eventually be replaced by Redis for distributed deployments
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from threading import Lock
from dataclasses import dataclass, field, asdict
from enum import Enum


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SentimentLabel(Enum):
    """Possible sentiment values"""
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    ANGRY = "ANGRY"
    URGENT = "URGENT"


class ConversationStatus(Enum):
    """Possible conversation states"""
    ACTIVE = "ACTIVE"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    ABANDONED = "ABANDONED"


@dataclass
class Message:
    """Represents a single message in the conversation"""
    sender: str  # 'USER' or 'AGENT'
    text: str
    timestamp: str
    intent_label: Optional[str] = None
    sentiment_label: Optional[str] = None
    entities: Optional[Dict[str, Any]] = None
    agent_action: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ConversationContext:
    """
    Complete context for a single active conversation session.
    
    This structure mirrors what will eventually be written to the
    'completed_conversations' and 'completed_messages' tables.
    """
    session_id: str
    start_time: str
    customer_email: Optional[str] = None
    customer_id: Optional[str] = None
    
    # Current state
    status: str = ConversationStatus.ACTIVE.value
    current_sentiment: Optional[str] = None
    current_intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    
    # Message history (last 3 for context, full log for transcription)
    messages: List[Message] = field(default_factory=list)
    
    # Extracted entities across the conversation
    entities: Dict[str, Any] = field(default_factory=dict)
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # For escalations
    escalation_reason: Optional[str] = None
    operator_id: Optional[str] = None
    
    def get_last_n_messages(self, n: int = 3) -> List[Message]:
        """Get the last N messages for context"""
        return self.messages[-n:] if len(self.messages) >= n else self.messages
    
    def get_user_message_history(self) -> List[str]:
        """Get all user messages as text list"""
        return [msg.text for msg in self.messages if msg.sender == 'USER']
    
    def add_message(self, sender: str, text: str, **kwargs) -> Message:
        """
        Add a new message to the conversation.
        
        Args:
            sender: 'USER' or 'AGENT'
            text: The message text
            **kwargs: Additional message fields (intent_label, entities, etc.)
        
        Returns:
            The created Message object
        """
        msg = Message(
            sender=sender,
            text=text,
            timestamp=datetime.utcnow().isoformat(),
            **kwargs
        )
        self.messages.append(msg)
        return msg
    
    def update_sentiment(self, sentiment: str, confidence: Optional[float] = None):
        """Update the current sentiment"""
        self.current_sentiment = sentiment
        if self.messages:
            # Also update the last message's sentiment
            self.messages[-1].sentiment_label = sentiment
    
    def update_intent(self, intent: str, confidence: float):
        """Update the current intent"""
        self.current_intent = intent
        self.intent_confidence = confidence
        if self.messages:
            # Also update the last message's intent
            self.messages[-1].intent_label = intent
    
    def merge_entities(self, entities: Dict[str, Any]):
        """Merge new entities into the context"""
        self.entities.update(entities)
        if self.messages:
            # Also attach to the last message
            if self.messages[-1].entities is None:
                self.messages[-1].entities = {}
            self.messages[-1].entities.update(entities)
    
    def escalate(self, reason: str):
        """Mark conversation as escalated"""
        self.status = ConversationStatus.ESCALATED.value
        self.escalation_reason = reason
    
    def resolve(self):
        """Mark conversation as resolved"""
        self.status = ConversationStatus.RESOLVED.value
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization or database write"""
        return {
            'session_id': self.session_id,
            'start_time': self.start_time,
            'customer_email': self.customer_email,
            'customer_id': self.customer_id,
            'status': self.status,
            'current_sentiment': self.current_sentiment,
            'current_intent': self.current_intent,
            'intent_confidence': self.intent_confidence,
            'messages': [msg.to_dict() for msg in self.messages],
            'entities': self.entities,
            'metadata': self.metadata,
            'escalation_reason': self.escalation_reason,
            'operator_id': self.operator_id
        }


class ContextStore:
    """
    In-memory store for active conversation contexts.
    
    This is the "hot path" storage that holds all active sessions in memory
    for fast access. Once a conversation ends, the Transcription Agent will
    write it to the database (cold storage).
    
    Thread-safe for concurrent access from multiple agents.
    """
    
    def __init__(self):
        """Initialize the context store"""
        self._contexts: Dict[str, ConversationContext] = {}
        self._lock = Lock()
        
        # Statistics
        self._stats = {
            'sessions_created': 0,
            'sessions_active': 0,
            'sessions_ended': 0,
            'total_messages': 0
        }
        
        logger.info("ContextStore initialized")
    
    def create_session(self, session_id: str, customer_email: Optional[str] = None) -> ConversationContext:
        """
        Create a new conversation session.
        
        Args:
            session_id: Unique identifier for this session
            customer_email: Optional customer email
        
        Returns:
            The created ConversationContext
        
        Raises:
            ValueError: If session_id already exists
        """
        with self._lock:
            if session_id in self._contexts:
                raise ValueError(f"Session {session_id} already exists")
            
            context = ConversationContext(
                session_id=session_id,
                start_time=datetime.utcnow().isoformat(),
                customer_email=customer_email
            )
            
            self._contexts[session_id] = context
            self._stats['sessions_created'] += 1
            self._stats['sessions_active'] += 1
            
            logger.info(f"Created new session: {session_id}")
            return context
    
    def get(self, session_id: str) -> Optional[ConversationContext]:
        """
        Get a conversation context by session ID.
        
        Args:
            session_id: The session to retrieve
        
        Returns:
            The ConversationContext or None if not found
        """
        with self._lock:
            return self._contexts.get(session_id)
    
    def get_or_create(self, session_id: str, **kwargs) -> ConversationContext:
        """
        Get existing session or create new one if it doesn't exist.
        
        Args:
            session_id: The session ID
            **kwargs: Arguments to pass to create_session if creating
        
        Returns:
            The ConversationContext
        """
        context = self.get(session_id)
        if context is None:
            context = self.create_session(session_id, **kwargs)
        return context
    
    def update(self, session_id: str, **updates) -> Optional[ConversationContext]:
        """
        Update fields in a conversation context.
        
        Args:
            session_id: The session to update
            **updates: Fields to update (e.g., current_sentiment='POSITIVE')
        
        Returns:
            The updated ConversationContext or None if session not found
        """
        with self._lock:
            context = self._contexts.get(session_id)
            if context is None:
                logger.warning(f"Cannot update non-existent session: {session_id}")
                return None
            
            # Update fields
            for key, value in updates.items():
                if hasattr(context, key):
                    setattr(context, key, value)
                else:
                    logger.warning(f"Unknown field '{key}' in context update")
            
            return context
    
    def add_message(self, session_id: str, sender: str, text: str, **kwargs) -> Optional[Message]:
        """
        Add a message to a conversation.
        
        Args:
            session_id: The session to add the message to
            sender: 'USER' or 'AGENT'
            text: The message text
            **kwargs: Additional message fields
        
        Returns:
            The created Message or None if session not found
        """
        with self._lock:
            context = self._contexts.get(session_id)
            if context is None:
                logger.warning(f"Cannot add message to non-existent session: {session_id}")
                return None
            
            msg = context.add_message(sender, text, **kwargs)
            self._stats['total_messages'] += 1
            
            logger.debug(f"Added {sender} message to session {session_id}")
            return msg
    
    def delete(self, session_id: str) -> Optional[ConversationContext]:
        """
        Remove a session from the store.
        
        This should be called by the Transcription Agent after writing
        to the database.
        
        Args:
            session_id: The session to delete
        
        Returns:
            The deleted ConversationContext or None if not found
        """
        with self._lock:
            context = self._contexts.pop(session_id, None)
            if context:
                self._stats['sessions_active'] -= 1
                self._stats['sessions_ended'] += 1
                logger.info(f"Deleted session: {session_id}")
            return context
    
    def get_all_sessions(self) -> List[str]:
        """Get list of all active session IDs"""
        with self._lock:
            return list(self._contexts.keys())
    
    def get_active_count(self) -> int:
        """Get count of active sessions"""
        with self._lock:
            return len(self._contexts)
    
    def get_stats(self) -> Dict[str, int]:
        """Get store statistics"""
        with self._lock:
            return self._stats.copy()
    
    def clear_all(self):
        """
        Clear all sessions. Use with caution!
        Primarily for testing or emergency cleanup.
        """
        with self._lock:
            count = len(self._contexts)
            self._contexts.clear()
            self._stats['sessions_active'] = 0
            logger.warning(f"Cleared all sessions (removed {count} contexts)")


# Global singleton instance
_global_context_store = None


def get_context_store() -> ContextStore:
    """
    Get the global singleton ContextStore instance.
    
    Returns:
        The global ContextStore instance
    """
    global _global_context_store
    if _global_context_store is None:
        _global_context_store = ContextStore()
    return _global_context_store


if __name__ == "__main__":
    """Demo/test code"""
    print("=== ContextStore Demo ===\n")
    
    store = ContextStore()
    
    # Create a session
    print("1. Creating session...")
    context = store.create_session("session-123", customer_email="user@example.com")
    print(f"   Created: {context.session_id}")
    
    # Add messages
    print("\n2. Adding messages...")
    store.add_message("session-123", "USER", "I need to return my laptop")
    store.add_message("session-123", "AGENT", "I can help with that!")
    
    # Update sentiment and intent
    print("\n3. Updating context...")
    context = store.get("session-123")
    context.update_sentiment("NEUTRAL", 0.85)
    context.update_intent("process_return", 0.95)
    context.merge_entities({"product": "laptop"})
    
    # Get the context
    print("\n4. Retrieving context...")
    context = store.get("session-123")
    print(f"   Session ID: {context.session_id}")
    print(f"   Messages: {len(context.messages)}")
    print(f"   Intent: {context.current_intent} (confidence: {context.intent_confidence})")
    print(f"   Entities: {context.entities}")
    
    # Show last messages
    print("\n5. Last 3 messages:")
    for msg in context.get_last_n_messages(3):
        print(f"   {msg.sender}: {msg.text}")
    
    # Statistics
    print("\n6. Store statistics:")
    print(f"   {store.get_stats()}")
    
    # Convert to dict (for database write)
    print("\n7. Context as dictionary (ready for DB):")
    import json
    print(json.dumps(context.to_dict(), indent=2))