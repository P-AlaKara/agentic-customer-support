"""
Intent Recognition Agent

Classifies user queries into specific intents to route to the appropriate
Business Process Agent.

Supported Intents:
- track_order: User wants to check order status/shipping
- process_return: User wants to return/refund an item
- account_issues: User needs help with account/login/profile
- onboarding: User needs help getting started as a new customer
- general_inquiry: Catch-all for unclear requests

Design Decisions:
- Rule-based keyword matching (fast, no dependencies)
- Can be upgraded to ML/LLM later
- Returns confidence score for escalation decisions
"""

import json
import logging
import re
import time
from typing import Dict, Any, List, Tuple, Optional

# Flexible import pattern
try:
    from ..event_bus import EventBus, Event
    from ..utils.gemini import get_gemini_client
    from ..utils.claude import get_claude_client
except (ImportError, ValueError):
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from event_bus import EventBus, Event
    from utils.gemini import get_gemini_client
    from utils.claude import get_claude_client


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



class IntentAgent:
    """
    Intent Recognition Agent for customer support.
    
    Classifies user messages into actionable intents using keyword matching
    and pattern recognition.
    """

    ORDER_ID_PATTERN = re.compile(r"\b(ORD\d{5})\b", re.IGNORECASE)

    # Intent keyword mappings — includes English and Swahili so the rule-based
    # path stays useful as a fast fallback even for Swahili input.
    INTENT_KEYWORDS = {
        'track_order': {
            'primary': [
                'track', 'tracking', 'shipped', 'shipping', 'delivery', 'deliver', 'status',
                # Swahili
                'fuatilia', 'kufuatilia', 'usafirishaji', 'kusafirisha', 'kupeleka', 'imepelekwa', 'imefika', 'hali ya agizo'
            ],
            'secondary': [
                'where is', 'arrive', 'arriving', 'eta', 'when will',
                # Swahili
                'iko wapi', 'itafika', 'lini itafika', 'inakuja'
            ],
            'entities': ['order', 'package', 'shipment', 'agizo', 'kifurushi', 'mzigo']
        },
        'process_return': {
            'primary': [
                'return', 'refund', 'exchange', 'replace', 'send back', 'take back',
                # Swahili
                'rudisha', 'kurudisha', 'rejesha', 'kurejesha', 'rudishiwa', 'rudisheni', 'badilisha bidhaa'
            ],
            'secondary': [
                'give back', 'money back',
                # Swahili
                'rejesha pesa', 'pesa zangu', 'kurudishiwa pesa'
            ],
            'entities': ['item', 'product', 'purchase', 'bidhaa', 'kitu', 'ununuzi']
        },
        'account_issues': {
            'primary': [
                'account', 'login', 'password', 'sign in', 'log in',
                # Swahili
                'akaunti', 'ingia', 'kuingia', 'nywila', 'neno la siri'
            ],
            'secondary': [
                'email', 'profile', 'username', 'change', 'update', 'reset',
                # Swahili
                'barua pepe', 'wasifu', 'jina la mtumiaji', 'badilisha', 'sasisha', 'rekebisha'
            ],
            'entities': ['credentials', 'access', 'settings', 'mipangilio']
        },
        'onboarding': {
            'primary': [
                'onboarding', 'onboard', 'get started', 'getting started', 'new account',
                'create account', 'sign up', 'register', 'first login', 'welcome tour',
                # Swahili
                'anza', 'kuanza', 'fungua akaunti', 'sajili', 'kujisajili', 'mtumiaji mpya', 'kuingia kwa mara ya kwanza'
            ],
            'secondary': [
                'first time', 'setup', 'walkthrough', 'tutorial', 'introduction', 'start here',
                # Swahili
                'mara ya kwanza', 'sanidi', 'mafunzo', 'utangulizi', 'anzia hapa'
            ],
            'entities': ['tour', 'guide', 'profile', 'preferences', 'mwongozo', 'wasifu']
        },
        'greeting': {
            'primary': [
                'hello', 'hi', 'hey', 'greetings', 'good morning', 'good afternoon', 'good evening',
                # Swahili
                'habari', 'jambo', 'hujambo', 'mambo', 'salamu', 'shikamoo', 'habari yako', 'asubuhi njema', 'mchana mwema', 'jioni njema'
            ],
            'secondary': [
                'how are you', 'thanks', 'thank you', 'help',
                # Swahili
                'asante', 'asanteni', 'shukrani', 'msaada', 'naomba msaada'
            ],
            'entities': []
        },
        'close_conversation': {
            'primary': [
                'bye', 'goodbye', 'that is all', "that's all", 'done', 'no thanks',
                # Swahili
                'kwaheri', 'kwaherini', 'ndio hivyo', 'hiyo ni yote', 'nimemaliza', 'hapana asante'
            ],
            'secondary': [
                'thank you bye', 'all good', 'nothing else',
                # Swahili
                'asante kwaheri', 'sawa', 'hakuna kitu kingine'
            ],
            'entities': []
        },
        'request_human': {
            'primary': [
                'human', 'agent', 'representative', 'operator', 'real person', 'live agent',
                # Swahili
                'binadamu', 'mtu halisi', 'wakala', 'mwakilishi', 'opereta'
            ],
            'secondary': [
                'speak to', 'talk to', 'connect me', 'someone real',
                # Swahili
                'ongea na', 'zungumza na', 'unganisha na', 'mtu wa kweli', 'mtu halisi'
            ],
            'entities': []
        }
    }
    
    # Common phrases that indicate specific intents (English + Swahili)
    INTENT_PHRASES = {
        'track_order': [
            'where is my order',
            'status of my order',
            'track my order',
            'order status',
            'shipping status',
            'when will it arrive',
            'has it shipped',
            'tracking number',
            'delivery date',
            # Swahili
            'agizo langu liko wapi',
            'fuatilia agizo langu',
            'hali ya agizo langu',
            'agizo lipo wapi',
            'kifurushi changu kiko wapi',
            'lini agizo langu litafika',
            'nataka kufuatilia agizo'
        ],
        'process_return': [
            'want to return',
            'need to return',
            'return this',
            'get a refund',
            'send it back',
            'not satisfied',
            'wrong item',
            'defective',
            # Swahili
            'nataka kurudisha',
            'ningependa kurudisha',
            'naomba kurudisha bidhaa',
            'rudisha bidhaa',
            'nirejeshe pesa',
            'naomba rejesho',
            'bidhaa mbaya',
            'bidhaa iliyoharibika',
            'sio bidhaa niliyoagiza'
        ],
        'account_issues': [
            "can't log in",
            'cannot log in',
            'forgot password',
            'reset password',
            'update email',
            'change password',
            'account locked',
            "can't access",
            # Swahili
            'siwezi kuingia',
            'nimesahau nywila',
            'nimesahau neno la siri',
            'badilisha nywila',
            'rekebisha nywila',
            'akaunti yangu imefungwa',
            'siwezi kufikia akaunti'
        ],
        'onboarding': [
            'how do i get started',
            'getting started',
            'i am new here',
            'new customer setup',
            'help me create an account',
            'create my account',
            'first login help',
            'show me the welcome tour',
            # Swahili
            'nianzeje',
            'ninaanzaje',
            'mimi ni mtumiaji mpya',
            'nisaidie kufungua akaunti',
            'fungua akaunti mpya',
            'msaada wa kuingia mara ya kwanza'
        ],
        'greeting': [
            'hi',
            'hello',
            'hey',
            'hi there',
            'hello there',
            'good morning',
            'good afternoon',
            'good evening',
            'hey there',
            'greetings',
            # Swahili
            'habari',
            'jambo',
            'hujambo',
            'mambo',
            'salamu',
            'shikamoo',
            'habari yako',
            'asubuhi njema',
            'mchana mwema',
            'jioni njema'
        ],
        'close_conversation': [
            'bye',
            'goodbye',
            'that is all',
            "that's all",
            'nothing else',
            'all good thanks',
            'no thanks bye',
            # Swahili
            'kwaheri',
            'kwaherini',
            'hiyo ni yote',
            'ndio hivyo',
            'nimemaliza',
            'hapana asante',
            'asante kwaheri'
        ],
        'request_human': [
            'speak to a human',
            'talk to a human',
            'speak to an agent',
            'talk to an agent',
            'speak to a person',
            'talk to a person',
            'speak to someone',
            'talk to someone',
            'speak to a real person',
            'talk to a real person',
            'connect me to a human',
            'connect me to an agent',
            'connect me to a person',
            'connect me to someone',
            'human agent',
            'live agent',
            'real person',
            'i want a human',
            'i need a human',
            'get me a human',
            'transfer me to a human',
            'transfer me to an agent',
            # Swahili
            'nataka kuongea na binadamu',
            'nataka kuongea na mtu halisi',
            'nataka kuzungumza na wakala',
            'naomba kuongea na binadamu',
            'unganisha na binadamu',
            'nipe wakala',
            'nipe mtu halisi',
            'wakala wa binadamu',
            'mtu wa kweli'
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
        ],
        'onboarding': [
            r'\bhow\s+(do\s+i|can\s+i)\s+(get\s+started|start)',
            r'\b(help\s+me\s+)?(create|set\s*up)\s+(an\s+)?account',
            r'\b(first\s+login|welcome\s+tour|new\s+user\s+guide)',
        ],
        'request_human': [
            r'\b(speak|talk|chat)\s+(to|with)\s+(a\s+)?(human|person|agent|operator|representative|someone)\b',
            r'\bconnect\s+me\s+(to|with)\s+(a\s+)?(human|person|agent|operator|someone)\b',
            r'\b(transfer|escalate)\s+(me\s+)?to\s+(a\s+)?(human|agent|operator|person)\b',
            r'\b(real|live)\s+(person|agent|human)\b',
            r'\bi\s+(want|need)\s+(a\s+)?(human|real person|live agent)\b',
        ]
    }
    
    # Intents the ML classifier is allowed to return
    ML_VALID_INTENTS = {
        'track_order', 'process_return', 'account_issues', 'onboarding',
        'greeting', 'close_conversation', 'request_human', 'general_inquiry'
    }

    def __init__(self, event_bus: EventBus, use_ml: bool = False):
        """
        Initialize the Intent Agent.

        Args:
            event_bus: The event bus for communication
            use_ml: If True, force ML mode for all messages; if False, default
                to rule-based but switch to ML automatically when the
                conversation language is non-English (e.g. Swahili).
        """
        self.bus = event_bus
        self.use_ml = use_ml

        try:
            self.gemini = get_gemini_client()
        except Exception as e:
            logger.warning(f"IntentAgent: Gemini initialization failed: {e}")
            self.gemini = None

        try:
            self.claude = get_claude_client()
        except Exception as e:
            logger.warning(f"IntentAgent: Claude initialization failed: {e}")
            self.claude = None
        
        # Statistics
        self.stats = {
            'total_analyzed': 0,
            'track_order': 0,
            'process_return': 0,
            'account_issues': 0,
            'onboarding': 0,
            'close_conversation': 0,
            'request_human': 0,
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
            language = (payload.get('language') or 'en').lower()

            logger.info(f"[Intent Agent] Analyzing message from session {session_id} (lang={language})")
            logger.debug(f"[Intent Agent] Text: '{text}'")

            t0 = time.perf_counter()
            # Classify intent. Primary path: keyword rules for English,
            # Gemini ML for non-English (or when explicitly forced via use_ml).
            # Secondary path: Claude is invoked only when the primary classifier
            # produces a no-confident-match sentinel.
            use_ml = self.use_ml or language != 'en'
            used_claude = False
            if use_ml:
                result = self._classify_with_ml(text, history)
                if result.get('_no_match') or result.get('intent') == 'general_inquiry':
                    claude_result = self._classify_with_claude(text, history)
                    if claude_result is not None:
                        used_claude = True
                        result = claude_result
            else:
                result = self._classify_with_rules(text)
                if result.get('_no_match'):
                    claude_result = self._classify_with_claude(text, history)
                    if claude_result is not None:
                        used_claude = True
                        result = claude_result

            result.pop('_no_match', None)

            intent = result['intent']
            confidence = result['confidence']
            entities = result.get('entities', {})

            if used_claude:
                path = 'claude'
            elif use_ml:
                path = 'gemini'
            else:
                path = 'rules'
            logger.info(
                f"[PERF] session={session_id} stage=intent duration_ms={int((time.perf_counter() - t0) * 1000)} "
                f"path={path} intent={intent} confidence={confidence:.2f}"
            )
            
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

            _safe_log_agent_event(
                agent_name='intent',
                event_type='TASK_RECOGNIZE_INTENT',
                input_data={'session_id': session_id, 'text': text[:100]},
                output_data={'intent': intent, 'confidence': confidence, 'entities': entities, 'published_event': 'RESULT_INTENT_RECOGNIZED'}
            )
            
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
        
        # Default: general_inquiry with low confidence. The `_no_match` flag
        # signals to the dispatcher that no rule fired so the Claude fallback
        # should be attempted. The flag is stripped before publishing.
        return {
            'intent': 'general_inquiry',
            'confidence': 0.60,
            'entities': {},
            '_no_match': True
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
        
        # Extract order IDs (exact format: ORD + 5 digits)
        order_match = self.ORDER_ID_PATTERN.search(text)
        if order_match:
            entities['order_id'] = order_match.group(1).upper()
        
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

        elif intent == 'onboarding':
            if any(phrase in text for phrase in ['create account', 'sign up', 'register', 'new account']):
                entities['onboarding_stage'] = 'account_creation'
            elif any(phrase in text for phrase in ['first login', 'log in', 'sign in']):
                entities['onboarding_stage'] = 'first_login'
            elif any(phrase in text for phrase in ['welcome tour', 'tour', 'walkthrough', 'tutorial']):
                entities['onboarding_stage'] = 'welcome_tour'
            elif 'get started' in text or 'getting started' in text:
                entities['onboarding_stage'] = 'getting_started'

        return entities
    
    def _classify_with_ml(self, text: str, history: List[str]) -> Dict[str, Any]:
        """LLM-based intent classification using Gemini.

        Returns the rule-based classification if Gemini is unavailable, or
        if the response is not parseable. Entities are still extracted via
        the rule-based extractor so order_id, email, etc. continue to work.
        """
        if not self.gemini or self.gemini.model is None:
            logger.warning("[Intent Agent] Gemini unavailable; falling back to rule-based classification")
            return self._classify_with_rules(text)

        history_block = ""
        if history:
            recent = history[-3:]
            history_lines = [f"- {h}" for h in recent if h]
            if history_lines:
                history_block = "RECENT MESSAGES (oldest first):\n" + "\n".join(history_lines) + "\n\n"

        prompt = (
            "You are an intent classifier for a customer-support assistant on an "
            "e-commerce platform. The customer may write in English, Swahili, or a mix.\n\n"
            "Allowed intents: [track_order, process_return, account_issues, onboarding, "
            "greeting, close_conversation, request_human, general_inquiry]\n\n"
            "Definitions:\n"
            "- track_order: customer asks about order/shipping status, tracking, delivery time.\n"
            "- process_return: customer wants to return an item, request refund, or exchange.\n"
            "- account_issues: login problems, password reset, account locked, profile/email changes.\n"
            "- onboarding: new customer wants help getting started, creating an account, first login.\n"
            "- greeting: customer is just saying hello or making small talk.\n"
            "- close_conversation: customer indicates they are done.\n"
            "- request_human: customer explicitly asks to speak to a human/agent/person.\n"
            "- general_inquiry: anything else, including unclear or out-of-scope questions.\n\n"
            f"{history_block}"
            f"CUSTOMER MESSAGE: \"{text}\"\n\n"
            "Respond with ONLY a JSON object on a single line, no Markdown, no extra text. "
            "Example: {\"intent\":\"process_return\",\"confidence\":0.92}\n"
            "The confidence is a float between 0 and 1."
        )

        try:
            response = self.gemini.model.generate_content(
                prompt,
                generation_config={
                    'temperature': 0.0,
                    'top_p': 1.0,
                    'max_output_tokens': 80,
                }
            )
            raw = (response.text or '').strip()
            parsed = self._parse_ml_response(raw)
            if parsed is None:
                logger.warning(f"[Intent Agent] Could not parse ML response: {raw!r}; using rules")
                return self._classify_with_rules(text)

            intent = parsed.get('intent') or 'general_inquiry'
            try:
                confidence = float(parsed.get('confidence', 0.7))
            except (TypeError, ValueError):
                confidence = 0.7
            confidence = max(0.0, min(confidence, 1.0))

            if intent not in self.ML_VALID_INTENTS:
                intent = 'general_inquiry'

            entities = self._extract_entities(text.lower(), intent)
            return {'intent': intent, 'confidence': confidence, 'entities': entities}

        except Exception as e:
            logger.error(f"[Intent Agent] ML classification error: {e}")
            return self._classify_with_rules(text)

    def _classify_with_claude(self, text: str, history: List[str]) -> Optional[Dict[str, Any]]:
        """Claude-backed classification, used only as a no-match fallback.

        Returns None if the Claude client is unavailable or fails to parse —
        the dispatcher then keeps the keyword-sentinel result, which carries a
        sub-0.7 confidence and causes the coordinator to escalate.
        """
        if not self.claude:
            return None

        logger.info("[Intent Agent] Invoking Claude fallback classifier")
        parsed = self.claude.classify_intent(text, history)
        if parsed is None:
            logger.warning("[Intent Agent] Claude fallback failed; will escalate via low-confidence sentinel")
            return None

        intent = parsed.get('intent') or 'general_inquiry'
        if intent not in self.ML_VALID_INTENTS:
            intent = 'general_inquiry'

        try:
            confidence = float(parsed.get('confidence', 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        confidence = max(0.0, min(confidence, 1.0))

        entities = self._extract_entities(text.lower(), intent)
        return {'intent': intent, 'confidence': confidence, 'entities': entities}

    @staticmethod
    def _parse_ml_response(raw: str) -> Optional[Dict[str, Any]]:
        """Best-effort JSON parser tolerating Markdown fences."""
        if not raw:
            return None
        # Strip Markdown code fences if Gemini returns them despite instructions
        cleaned = raw.strip()
        if cleaned.startswith('```'):
            cleaned = re.sub(r'^```(?:json)?', '', cleaned).strip()
            cleaned = re.sub(r'```$', '', cleaned).strip()
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        # Fallback: extract first {...} block
        match = re.search(r'\{[^{}]*\}', cleaned)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                return None
        return None
    
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
