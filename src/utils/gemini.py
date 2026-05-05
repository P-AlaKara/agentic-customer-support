"""
Gemini API Utilities

Handles interactions with Google's Gemini API for generating
natural language responses in Business Process Agents.

Uses gemini-1.5-flash for fast, cost-effective responses.
"""

import os
import logging
from typing import Dict, Any, List, Optional

try:
    from .debug_log import agent_debug_log
    from .localized_messages import (
        get_message,
        language_label,
        normalize_language,
    )
except (ImportError, ValueError):
    from utils.debug_log import agent_debug_log
    from utils.localized_messages import (
        get_message,
        language_label,
        normalize_language,
    )

try:
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logging.warning("google-generativeai not installed. Gemini features will use fallbacks.")


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Hardcoded here to keep the agent persona consistent. If you rename the agent
# in the UI (templates/chat.html AGENT_NAME), update this constant too.
AGENT_NAME = "Rehema"

SYSTEM_PREAMBLE = f"""You are {AGENT_NAME}, an AI customer support agent for an e-commerce platform.

SCOPE: You only assist with the following topics:
- Order tracking and shipping status
- Returns, refunds, and exchanges
- Account and login issues
- Onboarding for new customers

OUT OF SCOPE: Politely decline anything outside that scope (e.g., general knowledge, creative writing, personal advice, financial, legal, or medical guidance). Always redirect using the same meaning as: "I can only help with orders, returns, and account issues. Is there something in those areas I can assist with?" Translate the redirect into the active reply language when it is not English.

SAFETY: Do not engage with harmful, abusive, hateful, or sexually explicit content. Stay calm and redirect the customer to a support topic. If the customer becomes abusive, respond once with a calm acknowledgement and ask them to rephrase respectfully.

LANGUAGE: The application has a selected reply language (provided in CONTEXT as `Reply Language`). You MUST write the entire customer-visible response in that language.
- If `Reply Language` is Swahili, respond fully in Swahili, even if the customer wrote in English, the conversation history is English, or the policy/knowledge text is English.
- If `Reply Language` is English, respond in English.
- Translate any English policy facts, status names, or template instructions into the reply language when speaking to the customer. Keep order IDs, tracking numbers, and product names verbatim.
- If `Reply Language` is missing, mirror the customer's most recent message language.

OUTPUT STYLE (TTS-safe):
- Use plain prose with proper punctuation. Your response may be read aloud by text-to-speech.
- Do not use Markdown formatting: no asterisks, no bullets, no headers, no backticks.
- Numbered lists are acceptable when listing more than two steps. Spell numbers out where they read naturally.
- Be empathetic, clear, and concise. Prefer 2-4 sentences unless step-by-step instructions are genuinely required.
- Never invent fields you do not have (order ID, tracking number, dates, etc.). Ask for them instead.
""".strip()


class GeminiClient:
    """
    Client for Google Gemini API.
    
    Handles response generation for Business Process Agents using
    context, policies, and templates.
    """
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.5-flash"):
        """
        Initialize Gemini client.
        
        Args:
            api_key: Gemini API key (reads from GEMINI_API_KEY env if None)
            model: Model to use (default: gemini-1.5-flash for speed/cost)
        """
        self.api_key = api_key or os.getenv('GEMINI_API_KEY')
        self.model_name = model
        self.model = None
        
        if not self.api_key:
            logger.warning("No GEMINI_API_KEY found. Gemini features will use fallbacks.")
            return
        
        if not GENAI_AVAILABLE:
            logger.warning("google-generativeai not installed. Install with: pip install google-generativeai")
            return
        
        try:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(self.model_name)
            logger.info(f"Gemini client initialized ({self.model_name})")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini: {e}")
    
    def generate_response(
        self,
        user_query: str,
        context: Dict[str, Any],
        knowledge: str = "",
        template: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Generate a response using Gemini.

        Args:
            user_query: The user's question/request
            context: Conversation context (customer email, entities, etc.)
            knowledge: Retrieved knowledge from database/KB
            template: Response template/guidelines (optional)
            conversation_history: Recent messages in the conversation. Each
                entry should be a dict with `sender` ("USER"/"AGENT") and
                `text`. The last 5 are used to give Gemini multi-turn context.

        Returns:
            Generated response text
        """
        if not self.model:
            logger.warning("Gemini not available, using fallback")
            #region agent log
            agent_debug_log(
                "src/utils/gemini.py:118",
                "gemini unavailable fallback selected",
                {
                    "model_available": False,
                    "context_language": context.get('language'),
                    "metadata_language": (context.get('metadata') or {}).get('language') if isinstance(context.get('metadata'), dict) else None,
                    "intent": context.get('current_intent'),
                    "history_count": len(conversation_history or []),
                },
                "H4",
            )
            #endregion
            return self._fallback_response(user_query, context, knowledge)

        try:
            prompt = self._build_prompt(
                user_query=user_query,
                context=context,
                knowledge=knowledge,
                template=template,
                conversation_history=conversation_history,
            )
            #region agent log
            agent_debug_log(
                "src/utils/gemini.py:128",
                "gemini prompt inputs before generation",
                {
                    "model_available": bool(self.model),
                    "context_language": context.get('language'),
                    "metadata_language": (context.get('metadata') or {}).get('language') if isinstance(context.get('metadata'), dict) else None,
                    "intent": context.get('current_intent'),
                    "history_count": len(conversation_history or []),
                    "prompt_mentions_ui_language": "UI language" in prompt or "selected language" in prompt,
                },
                "H3",
            )
            #endregion

            response = self.model.generate_content(
                prompt,
                generation_config={
                    'temperature': 0.7,
                    'top_p': 0.9,
                    'max_output_tokens': 1024,
                }
            )

            return response.text.strip()

        except Exception as e:
            logger.error(f"Gemini generation error: {e}")
            return self._fallback_response(user_query, context, knowledge)

    def _build_prompt(
        self,
        user_query: str,
        context: Dict[str, Any],
        knowledge: str,
        template: Optional[str],
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build the prompt for Gemini, preceded by the system preamble."""

        prompt = f"{SYSTEM_PREAMBLE}\n\n"

        if conversation_history:
            recent = conversation_history[-5:]
            prompt += "CONVERSATION HISTORY (most recent last):\n"
            for msg in recent:
                sender = (msg.get('sender') or '').upper() or 'USER'
                text = msg.get('text', '')
                role = 'Customer' if sender == 'USER' else AGENT_NAME
                prompt += f"- {role}: {text}\n"
            prompt += "\n"

        prompt += f"USER QUERY:\n{user_query}\n\n"

        reply_language_code = self._resolve_reply_language(context)
        reply_language_label = language_label(reply_language_code)

        prompt += "CONTEXT:\n"
        prompt += f"- Reply Language: {reply_language_label} ({reply_language_code})\n"
        prompt += f"- Intent: {context.get('current_intent', 'Unknown')}\n"
        prompt += f"- Sentiment: {context.get('current_sentiment', 'Neutral')}\n"
        prompt += f"- Order ID: {context.get('order_id', 'Unknown')}\n"
        prompt += f"- Order Status: {context.get('order_status', 'Unknown')}\n"
        prompt += f"- Entities: {context.get('entities', {})}\n"
        prompt += f"- Order Details: {context.get('order_details', {})}\n"
        prompt += f"- Return Details: {context.get('return_details', {})}\n"

        if knowledge:
            prompt += f"\nRELEVANT POLICY / KNOWLEDGE:\n{knowledge}\n"

        if template:
            prompt += f"\nRESPONSE GUIDELINES:\n{template}\n"

        prompt += f"""
INSTRUCTIONS:
1. Follow the SYSTEM PREAMBLE rules (scope, safety, language, TTS-safe output) at all times.
2. Address the customer's most recent query directly, using the conversation history for continuity.
3. Use only the information provided in CONTEXT and RELEVANT POLICY. Do not invent details.
4. Keep the response concise and TTS-friendly (no Markdown, no asterisks, no headers).
5. End with a clear next step or question if appropriate.
6. Write the entire response in {reply_language_label}. Translate policy facts, status names, and any English snippets in CONTEXT or KNOWLEDGE into {reply_language_label}; keep order IDs, tracking numbers, and product names verbatim.

Generate the response:"""

        return prompt

    @staticmethod
    def _resolve_reply_language(context: Dict[str, Any]) -> str:
        """Pick the reply language from the agent-supplied context."""
        if not isinstance(context, dict):
            return "en"
        metadata = context.get('metadata')
        if isinstance(metadata, dict):
            meta_lang = metadata.get('language')
            if meta_lang:
                return normalize_language(meta_lang)
        return normalize_language(context.get('language'))
    
    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """Generate an embedding vector for `text`.

        Uses the same `text-embedding-004` model as embed.py so the query
        vector lives in the same space as the indexed kb_articles.
        Returns None if embeddings are unavailable (no API key, package
        missing, or API error). Callers should treat None as "RAG disabled"
        and fall back to static knowledge.
        """
        if not self.api_key or not GENAI_AVAILABLE:
            return None

        text = (text or '').strip()
        if not text:
            return None

        try:
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=text,
                task_type="retrieval_query",
            )
            embedding = result.get('embedding') if isinstance(result, dict) else getattr(result, 'embedding', None)
            if embedding is None:
                return None
            return list(embedding)
        except Exception as e:
            logger.warning(f"Gemini embedding error: {e}")
            return None

    def _fallback_response(
        self,
        user_query: str,
        context: Dict[str, Any],
        knowledge: str
    ) -> str:
        """Simple fallback when Gemini unavailable"""
        intent = context.get('current_intent', 'general_inquiry')
        language = self._resolve_reply_language(context)

        fallback_keys = {
            'process_return': 'gemini.fallback.process_return',
            'track_order': 'gemini.fallback.track_order',
            'account_issues': 'gemini.fallback.account_issues',
            'onboarding': 'gemini.fallback.onboarding',
            'general_inquiry': 'gemini.fallback.general_inquiry',
        }

        key = fallback_keys.get(intent, 'gemini.fallback.default')
        return get_message(key, language)


# Global instance
_global_gemini_client = None


def get_gemini_client() -> GeminiClient:
    """Get global Gemini client singleton"""
    global _global_gemini_client
    if _global_gemini_client is None:
        _global_gemini_client = GeminiClient()
    return _global_gemini_client


if __name__ == "__main__":
    """Test Gemini client"""
    print("=== Gemini Client Test ===\n")
    try:
        from dotenv import load_dotenv
        load_dotenv()
        print("✅ Environment variables loaded")
    except ImportError:
        print("⚠️  python-dotenv not installed")
    
    client = GeminiClient()
    
    # Test response generation
    response = client.generate_response(
        user_query="I want to return my laptop",
        context={
            'customer_email': 'test@example.com',
            'current_intent': 'process_return',
            'current_sentiment': 'NEUTRAL',
            'entities': {'product': 'laptop'}
        },
        knowledge="Return policy: Items can be returned within 30 days of purchase. Refunds processed within 5-7 business days.",
        template="Be empathetic. Explain the policy clearly. Ask for order number."
    )
    
    print("Generated Response:")
    print(response)
