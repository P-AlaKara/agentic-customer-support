"""
Transcription Agent

Passively listens to all conversation events and logs them to the database
when a conversation ends. This implements the "write-at-end" architecture
pattern for optimal performance.

Design Decisions:
- Passive listener - never blocks the main workflow
- Writes to database only after conversation completes
- Cleans up Context Store after successful write
- Handles both successful completions and escalations
- Maps in-memory context to database schema
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

# Flexible imports
try:
    from ..event_bus import EventBus, Event
except (ImportError, ValueError):
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from event_bus import EventBus, Event


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TranscriptionAgent:
    """
    Transcription Agent for logging conversations to the database.
    
    This agent is a passive observer that:
    1. Listens to all conversation events
    2. Builds a complete transcript in memory
    3. Writes to database when conversation ends
    4. Cleans up Context Store
    
    Events that trigger database write:
    - RESULT_SEND_RESPONSE_TO_USER (with final=True)
    - RESULT_ESCALATION_COMPLETE
    - CONVERSATION_TIMEOUT
    - CONVERSATION_ABANDONED
    
    Database Tables:
    - completed_conversations (header record)
    - completed_messages (message log)
    """
    
    def __init__(self, event_bus: EventBus, context_store=None, db_connection=None):
        """
        Initialize the Transcription Agent.
        
        Args:
            event_bus: The event bus for communication
            context_store: Context store instance (for cleanup)
            db_connection: DatabaseConnection instance (optional)
                          If None, will create one from environment variables
        """
        self.bus = event_bus
        self.context_store = context_store
        
        # Initialize database connection
        if db_connection is None:
            try:
                try:
                    from ..utils.database import get_db_connection, TranscriptDB # Module mode
                except (ImportError, ValueError):
                    import sys
                    import os
                    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    from utils.database import get_db_connection, TranscriptDB   # Standalone mode
                self.db_conn = get_db_connection()
                self.db = TranscriptDB(self.db_conn)
            except Exception as e:
                logger.warning(f"Database connection failed: {e}. Transcripts will not be persisted.")
                self.db_conn = None
                self.db = None
        else:
            self.db_conn = db_connection
            try:
                from ..utils.database import TranscriptDB
                self.db = TranscriptDB(self.db_conn)
            except Exception as e:
                logger.warning(f"TranscriptDB initialization failed: {e}")
                self.db = None
        
        # Track conversations in progress (session_id -> metadata)
        self.active_transcripts: Dict[str, Dict[str, Any]] = {}
        
        # Statistics
        self.stats = {
            'transcripts_started': 0,
            'transcripts_completed': 0,
            'messages_logged': 0,
            'db_writes': 0,
            'errors': 0
        }
        
        # Subscribe to all relevant events
        self._subscribe_to_events()
        
        logger.info("TranscriptionAgent initialized")
    
    def _subscribe_to_events(self):
        """Subscribe to all conversation events"""
        # Message events
        self.bus.subscribe('NEW_USER_MESSAGE', self.log_user_message)
        self.bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', self.log_agent_response)
        
        # Analysis events (for metadata)
        self.bus.subscribe('RESULT_SENTIMENT_RECOGNIZED', self.update_sentiment)
        self.bus.subscribe('RESULT_INTENT_RECOGNIZED', self.update_intent)
        
        # Completion events
        self.bus.subscribe('RESULT_ESCALATION_COMPLETE', self.end_conversation)
        self.bus.subscribe('CONVERSATION_END', self.end_conversation)
        
        logger.debug("[Transcription] Subscribed to conversation events")
    
    def log_user_message(self, event: Event):
        """
        Log a new user message.
        
        Expected event payload:
        {
            'session_id': str,
            'text': str,
            'customer_email': str (optional)
        }
        """
        try:
            payload = event.payload
            session_id = payload['session_id']
            
            # Initialize transcript if new session
            if session_id not in self.active_transcripts:
                self.active_transcripts[session_id] = {
                    'session_id': session_id,
                    'start_time': datetime.utcnow().isoformat(),
                    'customer_email': payload.get('customer_email'),
                    'messages': [],
                    'current_sentiment': None,
                    'current_intent': None,
                    'final_status': 'ACTIVE'
                }
                self.stats['transcripts_started'] += 1
            
            # Add message to transcript
            self.active_transcripts[session_id]['messages'].append({
                'timestamp': datetime.utcnow().isoformat(),
                'sender': 'USER',
                'text': payload['text'],
                'intent_label': None,
                'sentiment_label': None,
                'entities': None
            })
            
            self.stats['messages_logged'] += 1
            logger.debug(f"[Transcription] Logged user message for {session_id}")
            
        except Exception as e:
            logger.error(f"[Transcription] Error logging user message: {e}", exc_info=True)
            self.stats['errors'] += 1
    
    def log_agent_response(self, event: Event):
        """
        Log an agent response.
        
        Expected event payload:
        {
            'session_id': str,
            'text': str,
            'agent': str,
            'final': bool (optional) - if True, triggers conversation end
        }
        """
        try:
            payload = event.payload
            session_id = payload['session_id']
            
            if session_id not in self.active_transcripts:
                logger.warning(f"[Transcription] Session {session_id} not found in active transcripts")
                return
            
            # Add agent response
            self.active_transcripts[session_id]['messages'].append({
                'timestamp': datetime.utcnow().isoformat(),
                'sender': 'AGENT',
                'text': payload['text'],
                'agent_action': {
                    'agent': payload.get('agent', 'UNKNOWN'),
                    'action': 'respond',
                    'status': 'success'
                }
            })
            
            self.stats['messages_logged'] += 1
            logger.debug(f"[Transcription] Logged agent response for {session_id}")
            
            # Check if this is a final message
            if payload.get('final', False):
                self.end_conversation(Event(
                    event_type='CONVERSATION_END',
                    payload={'session_id': session_id, 'reason': 'RESOLVED_BY_AGENT'},
                    timestamp=datetime.utcnow(),
                    event_id='auto-generated'
                ))
            
        except Exception as e:
            logger.error(f"[Transcription] Error logging agent response: {e}", exc_info=True)
            self.stats['errors'] += 1
    
    def update_sentiment(self, event: Event):
        """Update sentiment metadata for the last message"""
        try:
            payload = event.payload
            session_id = payload['session_id']
            
            if session_id not in self.active_transcripts:
                return
            
            transcript = self.active_transcripts[session_id]
            transcript['current_sentiment'] = payload['sentiment']
            
            # Update last user message with sentiment
            if transcript['messages']:
                for msg in reversed(transcript['messages']):
                    if msg['sender'] == 'USER':
                        msg['sentiment_label'] = payload['sentiment']
                        break
            
            logger.debug(f"[Transcription] Updated sentiment for {session_id}")
            
        except Exception as e:
            logger.error(f"[Transcription] Error updating sentiment: {e}", exc_info=True)
    
    def update_intent(self, event: Event):
        """Update intent metadata for the last message"""
        try:
            payload = event.payload
            session_id = payload['session_id']
            
            if session_id not in self.active_transcripts:
                return
            
            transcript = self.active_transcripts[session_id]
            transcript['current_intent'] = payload['intent']
            
            # Update last user message with intent and entities
            if transcript['messages']:
                for msg in reversed(transcript['messages']):
                    if msg['sender'] == 'USER':
                        msg['intent_label'] = payload['intent']
                        msg['entities'] = payload.get('entities')
                        break
            
            logger.debug(f"[Transcription] Updated intent for {session_id}")
            
        except Exception as e:
            logger.error(f"[Transcription] Error updating intent: {e}", exc_info=True)
    
    def end_conversation(self, event: Event):
        """
        End a conversation and write to database.
        
        Expected event payload:
        {
            'session_id': str,
            'reason': str (optional) - 'RESOLVED_BY_AGENT', 'ESCALATED', 'TIMEOUT', etc.
            'operator_id': str (optional)
        }
        """
        try:
            payload = event.payload
            session_id = payload['session_id']
            
            if session_id not in self.active_transcripts:
                logger.warning(f"[Transcription] Session {session_id} not found, cannot end")
                return
            
            transcript = self.active_transcripts[session_id]
            
            # Update final status
            reason = payload.get('reason', 'UNKNOWN')
            if 'ESCALATE' in reason or payload.get('status') == 'QUEUED':
                transcript['final_status'] = 'ESCALATED_TO_HUMAN'
                transcript['operator_id'] = payload.get('operator_id')
            else:
                transcript['final_status'] = 'RESOLVED_BY_AGENT'
            
            transcript['end_time'] = datetime.utcnow().isoformat()
            
            logger.info(f"[Transcription] Ending conversation {session_id} ({transcript['final_status']})")
            
            # Write to database
            if self.db:
                self._write_to_database(transcript)
            else:
                # If no DB, just log what would be written
                logger.info(f"[Transcription] Would write to DB: {len(transcript['messages'])} messages")
            
            # Clean up Context Store
            if self.context_store:
                self.context_store.delete(session_id)
                logger.debug(f"[Transcription] Cleaned up context for {session_id}")
            
            # Remove from active transcripts
            del self.active_transcripts[session_id]
            
            # Update statistics
            self.stats['transcripts_completed'] += 1
            
            # Publish completion notification
            self.bus.publish('TRANSCRIPT_SAVED', {
                'session_id': session_id,
                'message_count': len(transcript['messages']),
                'final_status': transcript['final_status']
            })
            
        except Exception as e:
            logger.error(f"[Transcription] Error ending conversation: {e}", exc_info=True)
            self.stats['errors'] += 1
    
    def _write_to_database(self, transcript: Dict[str, Any]):
        """
        Write transcript to database using TranscriptDB.
        
        This maps the in-memory transcript to the database schema:
        - completed_conversations table (header)
        - completed_messages table (messages)
        """
        try:
            if self.db:
                # Use the TranscriptDB class to handle the write
                success = self.db.write_conversation(transcript)
                if success:
                    self.stats['db_writes'] += 1
                    logger.info(f"Wrote conversation {transcript['session_id']} to database")
                else:
                    logger.error(f"Failed to write conversation {transcript['session_id']} to database")
            else:
                # No database connection - just log what would be written
                logger.info(f"[No DB] Would write conversation {transcript['session_id']} with {len(transcript['messages'])} messages")
            
        except Exception as e:
            logger.error(f"Database write error: {e}", exc_info=True)
            self.stats['errors'] += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get transcription statistics"""
        return {
            **self.stats,
            'active_transcripts': len(self.active_transcripts)
        }
    
    def get_active_sessions(self) -> list:
        """Get list of active session IDs"""
        return list(self.active_transcripts.keys())


if __name__ == "__main__":
    """Standalone test"""
    print("=== Transcription Agent Test ===\n")
    
    from event_bus import EventBus
    
    bus = EventBus()
    agent = TranscriptionAgent(bus)
    
    # Simulate a conversation
    print("Test 1: New user message")
    bus.publish('NEW_USER_MESSAGE', {
        'session_id': 'test-001',
        'text': 'I want to return my laptop',
        'customer_email': 'user@example.com'
    })
    
    print("\nTest 2: Sentiment recognized")
    bus.publish('RESULT_SENTIMENT_RECOGNIZED', {
        'session_id': 'test-001',
        'sentiment': 'NEUTRAL',
        'confidence': 0.88
    })
    
    print("\nTest 3: Intent recognized")
    bus.publish('RESULT_INTENT_RECOGNIZED', {
        'session_id': 'test-001',
        'intent': 'process_return',
        'confidence': 0.95,
        'entities': {'product': 'laptop'}
    })
    
    print("\nTest 4: Agent response")
    bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
        'session_id': 'test-001',
        'text': 'I can help with your return!',
        'agent': 'RETURNS_AGENT',
        'final': True
    })
    
    print("\n=== Active Sessions ===")
    print(agent.get_active_sessions())
    
    print("\n=== Statistics ===")
    print(agent.get_stats())