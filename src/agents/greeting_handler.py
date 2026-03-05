"""
Greeting Agent

Handles initial greetings and helps users get started.
This agent responds when users say "hi", "hello", etc. without
a specific request.
"""

import logging
from typing import Dict, Any
from pathlib import Path

try:
    from ..event_bus import EventBus, Event
except (ImportError, ValueError):
    import sys
    import os
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from event_bus import EventBus, Event


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



class GreetingAgent:
    """
    Greeting Agent - handles initial conversation starters.
    
    Responds to greetings and prompts user to state their need.
    """
    
    def __init__(self, event_bus: EventBus):
        self.bus = event_bus
        self.stats = {'greetings_handled': 0, 'closings_handled': 0}
        
        self.bus.subscribe('TASK_HANDLE_GREETING', self.handle_greeting)
        self.bus.subscribe('TASK_HANDLE_CLOSING', self.handle_closing)
        logger.info("GreetingAgent initialized")
    
    def handle_greeting(self, event: Event):
        """
        Handle greeting intent.
        
        Expected payload:
        {
            'session_id': str,
            'messages': [...],
            ...
        }
        """
        try:
            context = event.payload
            session_id = context['session_id']
            
            logger.info(f"[Greeting Agent] Handling greeting for session {session_id}")
            self.stats['greetings_handled'] += 1
            
            # Send friendly greeting with options
            response = (
                "Hello! I'm here to help you today. "
                "I can assist with:\n\n"
                "Order Tracking, \n"
                "Returns & Refunds and \n"
                "Account Issues\n\n"
                "What can I help you with?"
            )
            
            self.bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
                'session_id': session_id,
                'text': response,
                'agent': 'GREETING_AGENT',
                'confidence': 1.0
            })

            _safe_log_agent_event(
                agent_name='greeting',
                event_type='TASK_HANDLE_GREETING',
                input_data={'session_id': session_id},
                output_data={'response_preview': response[:160], 'published_event': 'RESULT_SEND_RESPONSE_TO_USER'}
            )
            
        except Exception as e:
            logger.error(f"[Greeting Agent] Error: {e}", exc_info=True)
    

    def handle_closing(self, event: Event):
        """Handle explicit close intent and gracefully end conversation."""
        try:
            context = event.payload
            session_id = context['session_id']

            logger.info(f"[Greeting Agent] Handling closing intent for session {session_id}")
            self.stats['closings_handled'] += 1

            response = (
                "You’re welcome — happy to help. "
                "I’ll close this conversation now. "
                "If you need anything else, just start a new message anytime!"
            )

            self.bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
                'session_id': session_id,
                'text': response,
                'agent': 'GREETING_AGENT',
                'confidence': 1.0,
                'final': True
            })

            _safe_log_agent_event(
                agent_name='greeting',
                event_type='TASK_HANDLE_CLOSING',
                input_data={'session_id': session_id},
                output_data={'response_preview': response[:160], 'final': True, 'published_event': 'RESULT_SEND_RESPONSE_TO_USER'}
            )

        except Exception as e:
            logger.error(f"[Greeting Agent] Closing handler error: {e}", exc_info=True)

    def get_stats(self) -> Dict[str, int]:
        return self.stats.copy()


if __name__ == "__main__":
    from event_bus import EventBus
    
    bus = EventBus()
    agent = GreetingAgent(bus)
    
    def print_response(event):
        print(f"Response: {event.payload['text']}")
    
    bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', print_response)
    
    bus.publish('TASK_HANDLE_GREETING', {
        'session_id': 'test',
        'messages': []
    })
    
    print(f"Stats: {agent.get_stats()}")