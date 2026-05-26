"""Regression tests covering English/Swahili language handling.

These ensure that toggling `language` to `sw` flows through to greetings,
order ID prompts, gemini-off fallbacks, and the gateway-owned fixed
response strings — the historical English-leak hotspots.
"""

from src.event_bus import EventBus
from src.context_store import ContextStore
from src.coordinator import CoordinatorAgent
from src.agents.greeting_handler import GreetingAgent
from src.agents.shipping_agent import ShippingAgent
from src.agents.returns_agent import ReturnsAgent
from src.agents.onboarding_agent import OnboardingAgent
from src.utils.gemini import GeminiClient
from src.utils.localized_messages import (
    DEFAULT_LANGUAGE,
    get_message,
    normalize_language,
    resolve_language_from_context,
)


# ---------------------------------------------------------------------------
# Localization helper
# ---------------------------------------------------------------------------

def test_normalize_language_handles_variants_and_blanks():
    assert normalize_language('sw') == 'sw'
    assert normalize_language('SW') == 'sw'
    assert normalize_language('sw-KE') == 'sw'
    assert normalize_language('en') == 'en'
    assert normalize_language('') == DEFAULT_LANGUAGE
    assert normalize_language(None) == DEFAULT_LANGUAGE
    # Unknown locales fall back to the default rather than echoing the code.
    assert normalize_language('fr') == DEFAULT_LANGUAGE


def test_resolve_language_from_context_prefers_metadata_then_top_level():
    assert resolve_language_from_context({'metadata': {'language': 'sw'}}) == 'sw'
    assert resolve_language_from_context({'language': 'sw'}) == 'sw'
    assert resolve_language_from_context({'metadata': {'language': 'sw'}, 'language': 'en'}) == 'sw'
    assert resolve_language_from_context({}) == 'en'
    assert resolve_language_from_context(None) == 'en'


def test_get_message_returns_swahili_when_requested_and_falls_back_to_english():
    sw_greeting = get_message('greeting.initial', 'sw')
    en_greeting = get_message('greeting.initial', 'en')

    assert 'Habari' in sw_greeting
    assert 'Hello' in en_greeting
    assert sw_greeting != en_greeting

    # Unknown language falls back to English copy without crashing.
    assert get_message('greeting.initial', 'xx') == en_greeting

    # Unknown key returns empty string rather than raising.
    assert get_message('non.existent.key', 'sw') == ''


def test_get_message_supports_kwargs_formatting():
    rendered = get_message('shipping.fallback.delivered', 'sw', order_id='ORD12345')
    assert 'ORD12345' in rendered
    assert 'imewasilishwa' in rendered


# ---------------------------------------------------------------------------
# Greeting agent
# ---------------------------------------------------------------------------

def test_greeting_agent_returns_swahili_when_metadata_language_is_sw():
    bus = EventBus()
    GreetingAgent(bus)

    responses = []
    bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', lambda event: responses.append(event.payload))

    bus.publish('TASK_HANDLE_GREETING', {
        'session_id': 'greet-sw',
        'metadata': {'language': 'sw'},
        'messages': [],
    })

    assert responses, 'GreetingAgent should publish a response'
    assert responses[0]['agent'] == 'GREETING_AGENT'
    assert 'Habari' in responses[0]['text']
    assert 'Hello' not in responses[0]['text']


def test_greeting_agent_returns_english_by_default():
    bus = EventBus()
    GreetingAgent(bus)

    responses = []
    bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', lambda event: responses.append(event.payload))

    bus.publish('TASK_HANDLE_GREETING', {
        'session_id': 'greet-en',
        'messages': [],
    })

    assert responses
    assert 'Hello, I am Eva' in responses[0]['text']


def test_greeting_agent_closing_localized_to_swahili():
    bus = EventBus()
    GreetingAgent(bus)

    responses = []
    bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', lambda event: responses.append(event.payload))

    bus.publish('TASK_HANDLE_CLOSING', {
        'session_id': 'close-sw',
        'metadata': {'language': 'sw'},
        'messages': [],
    })

    assert responses
    assert 'Asante' in responses[0]['text']
    assert responses[0]['final'] is True


# ---------------------------------------------------------------------------
# Shipping agent
# ---------------------------------------------------------------------------

def test_shipping_agent_swahili_missing_order_id_prompt():
    bus = EventBus()
    shipping = ShippingAgent(bus)
    shipping.gemini = None

    responses = []
    bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', lambda event: responses.append(event.payload))

    bus.publish('TASK_HANDLE_ORDER_TRACKING', {
        'session_id': 'ship-sw',
        'entities': {},
        'metadata': {'language': 'sw'},
        'messages': [{'sender': 'USER', 'text': 'Pakiti yangu iko wapi?'}],
    })

    assert responses
    text = responses[0]['text']
    assert 'kufuatilia' in text or 'oda' in text
    assert 'Could you please' not in text
    assert 'ORDxxxxx' in text


def test_shipping_agent_english_missing_order_id_prompt_unchanged():
    bus = EventBus()
    shipping = ShippingAgent(bus)
    shipping.gemini = None

    responses = []
    bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', lambda event: responses.append(event.payload))

    bus.publish('TASK_HANDLE_ORDER_TRACKING', {
        'session_id': 'ship-en',
        'entities': {},
        'metadata': {'language': 'en'},
        'messages': [{'sender': 'USER', 'text': 'Where is my package?'}],
    })

    assert responses
    text = responses[0]['text']
    assert 'tracking' in text.lower()
    assert 'ORDxxxxx' in text


def test_shipping_fallback_response_in_swahili_for_in_transit():
    bus = EventBus()
    shipping = ShippingAgent(bus)
    shipping.gemini = None

    response = shipping._fallback_response(
        order_id='ORD12345',
        order_info={'status': 'SHIPPED', 'tracking_number': 'TRK001'},
        order_status='SHIPPED',
        language='sw',
    )

    assert 'ORD12345' in response
    assert 'TRK001' in response
    assert 'njiani' in response or 'siku' in response
    assert 'currently in transit' not in response


def test_shipping_fallback_response_no_order_info_in_swahili():
    bus = EventBus()
    shipping = ShippingAgent(bus)
    shipping.gemini = None

    response = shipping._fallback_response(
        order_id='ORD99999',
        order_info=None,
        order_status=None,
        language='sw',
    )

    assert 'ORD99999' in response
    assert 'Sikuweza' in response
    assert "I couldn't" not in response


# ---------------------------------------------------------------------------
# Returns agent
# ---------------------------------------------------------------------------

def test_returns_agent_swahili_missing_order_id_prompt():
    bus = EventBus()
    returns = ReturnsAgent(bus)
    returns.gemini = None

    responses = []
    bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', lambda event: responses.append(event.payload))

    bus.publish('TASK_HANDLE_RETURNS', {
        'session_id': 'ret-sw',
        'entities': {},
        'metadata': {'language': 'sw'},
        'messages': [{'sender': 'USER', 'text': 'Ningependa kurudisha bidhaa'}],
    })

    assert responses
    text = responses[0]['text']
    assert 'marejesho' in text
    assert 'Please share' not in text
    assert 'ORDxxxxx' in text


def test_returns_fallback_response_in_swahili_for_approved_status():
    bus = EventBus()
    returns = ReturnsAgent(bus)
    returns.gemini = None

    response = returns._fallback_response(
        order_id='ORD12345',
        order_status='APPROVED',
        order_info={'order_id': 'ORD12345'},
        language='sw',
    )

    assert 'ORD12345' in response
    assert 'yameidhinishwa' in response or 'lebo' in response
    assert 'is approved' not in response


def test_returns_fallback_response_no_order_info_in_swahili():
    bus = EventBus()
    returns = ReturnsAgent(bus)
    returns.gemini = None

    response = returns._fallback_response(
        order_id='ORD404',
        order_status=None,
        order_info=None,
        language='sw',
    )

    assert 'ORD404' in response
    assert 'Sikuweza' in response


# ---------------------------------------------------------------------------
# Onboarding agent
# ---------------------------------------------------------------------------

def test_onboarding_agent_returns_swahili_fallback_when_gemini_unavailable():
    bus = EventBus()
    onboarding = OnboardingAgent(bus)
    onboarding.gemini = None

    responses = []
    bus.subscribe('RESULT_SEND_RESPONSE_TO_USER', lambda event: responses.append(event.payload))

    bus.publish('TASK_HANDLE_ONBOARDING', {
        'session_id': 'onboard-sw',
        'entities': {},
        'metadata': {'language': 'sw'},
        'messages': [{'sender': 'USER', 'text': 'Ninaanzaje?'}],
    })

    assert responses
    assert responses[0]['agent'] == 'ONBOARDING_AGENT'
    assert 'Karibu' in responses[0]['text'] or 'ziara' in responses[0]['text']
    assert 'Welcome!' not in responses[0]['text']


# ---------------------------------------------------------------------------
# Gemini fallbacks
# ---------------------------------------------------------------------------

def test_gemini_fallback_response_uses_swahili_when_metadata_language_is_sw():
    client = GeminiClient.__new__(GeminiClient)
    client.api_key = None
    client.model = None
    client.model_name = 'gemini-test'

    response = client._fallback_response(
        user_query='Where is my order?',
        context={'current_intent': 'track_order', 'metadata': {'language': 'sw'}},
        knowledge='',
    )

    assert 'kufuatilia' in response or 'oda' in response
    assert 'I can help you track your order' not in response


def test_gemini_build_prompt_announces_swahili_reply_language():
    client = GeminiClient.__new__(GeminiClient)
    client.api_key = None
    client.model = None
    client.model_name = 'gemini-test'

    prompt = client._build_prompt(
        user_query='Where is my order ORD12345?',
        context={'metadata': {'language': 'sw'}, 'current_intent': 'track_order'},
        knowledge='Standard shipping: 5-7 business days.',
        template=None,
        conversation_history=None,
    )

    assert 'Reply Language: Swahili' in prompt
    assert 'Write the entire response in Swahili' in prompt


# ---------------------------------------------------------------------------
# Coordinator routing preserves language
# ---------------------------------------------------------------------------

def test_coordinator_routes_metadata_language_into_bpa_payload():
    bus = EventBus()
    store = ContextStore()
    coordinator = CoordinatorAgent(bus, store)

    routed = []
    bus.subscribe('TASK_HANDLE_ORDER_TRACKING', lambda event: routed.append(event.payload))

    context = store.get_or_create('route-sw', customer_email='user@example.com')
    context.metadata['language'] = 'sw'
    context.add_message('USER', 'Where is my order?')
    context.update_intent('track_order', 0.95)

    coordinator._route_to_agent('route-sw', 'track_order', context)

    assert routed
    payload_metadata = routed[0].get('metadata') or {}
    assert payload_metadata.get('language') == 'sw'


# ---------------------------------------------------------------------------
# Gateway-owned fixed strings
# ---------------------------------------------------------------------------

def test_chat_endpoint_returns_swahili_idle_response_for_sw_language(client):
    response = client.post('/chat', json={
        'message': 'Habari',
        'customer_email': 'sw-user@example.com',
        'language': 'sw',
    })

    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'idle'
    assert 'andika' in body['response'] or 'sema' in body['response']
    assert 'To get started' not in body['response']


def test_chat_endpoint_returns_english_idle_response_by_default(client):
    response = client.post('/chat', json={
        'message': 'just typing something',
        'customer_email': 'en-user@example.com',
    })

    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'idle'
    assert 'To get started' in body['response']
