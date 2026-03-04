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
    import google.generativeai as genai
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logging.warning("google-generativeai not installed. Gemini features will use fallbacks.")


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
        template: Optional[str] = None
    ) -> str:
        """
        Generate a response using Gemini.
        
        Args:
            user_query: The user's question/request
            context: Conversation context (customer email, entities, etc.)
            knowledge: Retrieved knowledge from database/KB
            template: Response template/guidelines (optional)
        
        Returns:
            Generated response text
        """
        if not self.model:
            logger.warning("Gemini not available, using fallback")
            return self._fallback_response(user_query, context, knowledge)
        
        try:
            prompt = self._build_prompt(user_query, context, knowledge, template)
            
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
        template: Optional[str]
    ) -> str:
        """Build the prompt for Gemini"""
        
        prompt = f"""You are a helpful customer support agent. Generate a professional, friendly response.

USER QUERY:
{user_query}

CONTEXT:
- Customer Email: {context.get('customer_email', 'Unknown')}
- Intent: {context.get('current_intent', 'Unknown')}
- Sentiment: {context.get('current_sentiment', 'Neutral')}
- Order ID: {context.get('order_id', 'Unknown')}
- Order Status: {context.get('order_status', 'Unknown')}
- Entities: {context.get('entities', {})}
- Order Details: {context.get('order_details', {})}
- Return Details: {context.get('return_details', {})}
"""
        
        if knowledge:
            prompt += f"\nRELEVANT INFORMATION:\n{knowledge}\n"
        
        if template:
            prompt += f"\nRESPONSE GUIDELINES:\n{template}\n"
        
        prompt += """
INSTRUCTIONS:
1. Be professional and empathetic
2. Address the user's query directly
3. Use the relevant information provided
4. Keep response concise (2-3 sentences)
5. Include next steps or call-to-action if applicable
6. Do not make up information not in the context

Generate the response:"""
        
        return prompt
    
    def _fallback_response(
        self,
        user_query: str,
        context: Dict[str, Any],
        knowledge: str
    ) -> str:
        """Simple fallback when Gemini unavailable"""
        intent = context.get('current_intent', 'general_inquiry')
        
        fallback_templates = {
            'process_return': "I can help you with your return request. Please provide your order number and I'll check the status for you.",
            'track_order': "I can help you track your order. Please provide your order number and I'll look up the shipping status.",
            'account_issues': "I can assist you with your account. Please let me know what specific issue you're experiencing.",
            'general_inquiry': "I'm here to help! Please provide more details about what you need assistance with."
        }
        
        return fallback_templates.get(intent, "I'm here to help! How can I assist you today?")


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
