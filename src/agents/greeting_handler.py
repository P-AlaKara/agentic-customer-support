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
    from ..utils.gemini import get_gemini_client
    from ..utils.localized_messages import (
        get_message,
        resolve_language_from_context,
    )
except (ImportError, ValueError):
    import sys
    import os
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from event_bus import EventBus, Event
    from utils.gemini import get_gemini_client
    from utils.localized_messages import (
        get_message,
        resolve_language_from_context,
    )


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
        self.stats = {
            'greetings_handled': 0,
            'closings_handled': 0,
            'general_inquiries_handled': 0,
            'general_inquiry_escalations': 0,
        }

        try:
            self.gemini = get_gemini_client()
        except Exception as e:
            logger.warning(f"GreetingAgent: Gemini initialization failed: {e}")
            self.gemini = None

        self.bus.subscribe('TASK_HANDLE_GREETING', self.handle_greeting)
        self.bus.subscribe('TASK_HANDLE_CLOSING', self.handle_closing)
        self.bus.subscribe('TASK_HANDLE_GENERAL_INQUIRY', self.handle_general_inquiry)
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

            language = resolve_language_from_context(context)
            response = get_message('greeting.initial', language)
            
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

            language = resolve_language_from_context(context)
            response = get_message('greeting.closing', language)

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

    def handle_general_inquiry(self, event: Event):
        """Handle ambiguous / general inquiries with a Gemini-first attempt.

        The SYSTEM_PREAMBLE in gemini.py forces out-of-scope questions to be
        politely redirected. We let Gemini attempt a response; if it raises
        or Gemini is unavailable, we fall back to escalating to a human.
        """
        try:
            context = event.payload
            session_id = context['session_id']

            logger.info(f"[Greeting Agent] Handling general inquiry for session {session_id}")
            self.stats['general_inquiries_handled'] += 1

            language = resolve_language_from_context(context)

            # Pull the user's last message out of the context
            messages = context.get('messages', []) or []
            user_query = ''
            for msg in reversed(messages):
                if msg.get('sender') == 'USER':
                    user_query = msg.get('text', '')
                    break

            if not self.gemini or self.gemini.model is None:
                logger.warning("[Greeting Agent] Gemini unavailable for general inquiry; escalating")
                self._escalate_general(session_id, reason='BPA_CANNOT_HANDLE',
                                       details={'cause': 'gemini_unavailable'})
                return

            try:
                response = self.gemini.generate_response(
                    user_query=user_query or get_message('greeting.general_inquiry_default_query', language),
                    context={
                        'current_intent': 'general_inquiry',
                        'current_sentiment': context.get('current_sentiment'),
                        'order_id': context.get('order_id'),
                        'order_status': context.get('order_status'),
                        'entities': context.get('entities', {}),
                        'language': language,
                    },
                    knowledge="",
                    template=None,
                    conversation_history=messages,
                )
            except Exception as e:
                logger.error(f"[Greeting Agent] Gemini error on general inquiry: {e}")
                self._escalate_general(session_id, reason='BPA_CANNOT_HANDLE',
                                       details={'cause': 'gemini_error', 'error': str(e)})
                return

            if not response or not response.strip():
                self._escalate_general(session_id, reason='BPA_CANNOT_HANDLE',
                                       details={'cause': 'empty_response'})
                return

            self.bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
                'session_id': session_id,
                'text': response.strip(),
                'agent': 'GREETING_AGENT',
                'confidence': 0.85
            })

            _safe_log_agent_event(
                agent_name='greeting',
                event_type='TASK_HANDLE_GENERAL_INQUIRY',
                input_data={'session_id': session_id, 'user_query_preview': (user_query or '')[:100]},
                output_data={'response_preview': response[:160], 'published_event': 'RESULT_SEND_RESPONSE_TO_USER'}
            )

        except Exception as e:
            logger.error(f"[Greeting Agent] General inquiry handler error: {e}", exc_info=True)
            try:
                session_id = event.payload.get('session_id')
                if session_id:
                    self._escalate_general(session_id, reason='BPA_CANNOT_HANDLE',
                                           details={'cause': 'handler_exception', 'error': str(e)})
            except Exception:
                pass

    def _escalate_general(self, session_id: str, reason: str, details: Dict[str, Any]):
        """Helper: ask the coordinator to escalate via REQUEST_ESCALATION."""
        self.stats['general_inquiry_escalations'] += 1
        self.bus.publish('REQUEST_ESCALATION', {
            'session_id': session_id,
            'reason': reason,
            'details': details,
            'requesting_agent': 'GREETING_AGENT'
        })

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