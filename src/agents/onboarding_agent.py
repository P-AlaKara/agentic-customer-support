"""
Onboarding Business Process Agent

Handles onboarding and getting started requests using:
- Knowledge base (onboarding guidance via RAG placeholder)
- Gemini API for natural language generation

Provides first-time setup guidance for:
- Account creation
- First login
- Getting started steps
- Welcome tour orientation
"""

import logging
from typing import Dict, Any

# Flexible imports
try:
    from ..event_bus import EventBus, Event
    from ..utils.database import get_db_connection, KnowledgeBaseDB
    from ..utils.gemini import get_gemini_client
    from ..utils.localized_messages import (
        get_message,
        resolve_language_from_context,
    )
    from ..utils.prompt_templates import ONBOARDING_RESPONSE_TEMPLATE
except (ImportError, ValueError):
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from event_bus import EventBus, Event
    from utils.database import get_db_connection, KnowledgeBaseDB
    from utils.gemini import get_gemini_client
    from utils.localized_messages import (
        get_message,
        resolve_language_from_context,
    )
    from utils.prompt_templates import ONBOARDING_RESPONSE_TEMPLATE


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


class OnboardingAgent:
    """
    Onboarding Business Process Agent.

    Handles:
    - New account setup guidance
    - First login assistance
    - "How do I get started" onboarding questions
    - Welcome tour information

    Uses:
    - KB: Retrieve onboarding guidance (RAG)
    - Gemini: Generate natural responses
    """

    def __init__(self, event_bus: EventBus):
        """Initialize Onboarding Agent."""
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
            'guides_provided': 0,
            'policies_retrieved': 0,
            'responses_generated': 0
        }

        # Subscribe to events
        self.bus.subscribe('TASK_HANDLE_ONBOARDING', self.handle_onboarding_request)

        logger.info("OnboardingAgent initialized")

    def handle_onboarding_request(self, event: Event):
        """
        Main handler for onboarding requests.

        Expected event payload (full context):
        {
            'session_id': str,
            'customer_email': str,
            'current_intent': 'onboarding',
            'entities': dict,
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

            logger.info(f"[Onboarding Agent] Handling onboarding request for session {session_id}")
            self.stats['requests_handled'] += 1

            language = resolve_language_from_context(context)

            user_query = self._get_last_user_message(context, language)
            knowledge = self._retrieve_onboarding_info(language)
            response = self._generate_response(
                user_query=user_query,
                context=context,
                knowledge=knowledge,
                language=language,
            )

            self.bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
                'session_id': session_id,
                'text': response,
                'agent': 'ONBOARDING_AGENT',
                'confidence': 0.95
            })

            self.stats['guides_provided'] += 1
            self.stats['responses_generated'] += 1
            logger.info(f"[Onboarding Agent] Response sent for {session_id}")

            _safe_log_agent_event(
                agent_name='onboarding',
                event_type='TASK_HANDLE_ONBOARDING',
                input_data={'session_id': session_id, 'customer_email': customer_email, 'entities': entities},
                output_data={'response_preview': response[:160], 'published_event': 'RESULT_SEND_RESPONSE_TO_USER'}
            )

        except Exception as e:
            logger.error(f"[Onboarding Agent] Error handling onboarding request: {e}", exc_info=True)

            language = 'en'
            try:
                language = resolve_language_from_context(event.payload)
            except Exception:
                language = 'en'

            self.bus.publish('RESULT_SEND_RESPONSE_TO_USER', {
                'session_id': event.payload.get('session_id') if isinstance(event.payload, dict) else None,
                'text': get_message('onboarding.exception_fallback', language),
                'agent': 'ONBOARDING_AGENT',
                'confidence': 0.5
            })

    def _get_last_user_message(self, context: Dict[str, Any], language: str = 'en') -> str:
        """Extract the last user message from context."""
        messages = context.get('messages', [])
        for msg in reversed(messages):
            if msg.get('sender') == 'USER':
                return msg.get('text', '')
        return get_message('onboarding.default_user_query', language)

    def _retrieve_onboarding_info(self, language: str = 'en') -> str:
        """
        Retrieve onboarding guidance from knowledge base using RAG.

        Returns:
            Concatenated onboarding guidance text
        """
        if not self.kb_db:
            return get_message('onboarding.static_kb_short', language)

        try:
            # TODO: Implement vector search with embeddings
            # For now, return static onboarding guidance. We deliberately keep
            # this static block in English because Gemini is instructed to
            # restate KB facts in the selected reply language; if the model
            # is unavailable we fall through to the localized templates in
            # `_fallback_response`.
            self.stats['policies_retrieved'] += 1
            return """Onboarding Guidance:
- Account creation: Click Sign Up, provide name/email, create a strong password, and verify your email.
- First login: Use your verified email + password; if login fails, use Forgot Password and retry.
- Getting started: Complete profile basics, set notification preferences, and review account settings.
- Welcome tour: Open the dashboard tour to learn navigation, key features, and where to find help resources.
- If verification email is missing: check spam/promotions and request a resend.
- Security best practices: enable multi-factor authentication and keep profile contact info up to date."""
        except Exception as e:
            logger.error(f"Error retrieving onboarding info: {e}")
            return get_message('onboarding.kb_unavailable_fallback', language)

    def _generate_response(
        self,
        user_query: str,
        context: Dict[str, Any],
        knowledge: str,
        language: str = 'en',
    ) -> str:
        """Generate onboarding response using Gemini or fallback."""
        enhanced_context = {
            'customer_email': context.get('customer_email'),
            'current_intent': context.get('current_intent'),
            'current_sentiment': context.get('current_sentiment'),
            'entities': context.get('entities', {}),
            'onboarding_stage': context.get('entities', {}).get('onboarding_stage'),
            'language': language,
        }

        if self.gemini:
            return self.gemini.generate_response(
                user_query=user_query,
                context=enhanced_context,
                knowledge=knowledge,
                template=ONBOARDING_RESPONSE_TEMPLATE
            )

        return self._fallback_response(user_query, language)

    def _fallback_response(self, user_query: str, language: str = 'en') -> str:
        """Simple fallback when Gemini unavailable."""
        text = (user_query or '').lower()

        if any(phrase in text for phrase in ['create account', 'sign up', 'register']):
            return get_message('onboarding.fallback.create_account', language)
        if any(phrase in text for phrase in ['first login', 'log in', 'sign in']):
            return get_message('onboarding.fallback.first_login', language)
        if any(phrase in text for phrase in ['get started', 'getting started', 'start using']):
            return get_message('onboarding.fallback.getting_started', language)
        if 'welcome tour' in text:
            return get_message('onboarding.fallback.welcome_tour', language)

        return get_message('onboarding.fallback.default', language)

    def get_stats(self) -> Dict[str, int]:
        """Get agent statistics."""
        return self.stats.copy()
