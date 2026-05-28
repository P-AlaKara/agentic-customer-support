"""Localized customer-visible message lookups.

Centralizes the fixed strings the backend emits directly (greetings, missing
order ID prompts, fallbacks, gateway responses, etc.) so a single language
toggle keeps the customer-facing surface in the selected language.

Gemini-generated text is steered separately via prompt instructions; this
module covers the deterministic paths where templates and hardcoded text
would otherwise leak English into a Swahili session.
"""

from typing import Any, Dict, Optional


DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "sw")


def normalize_language(language: Optional[str]) -> str:
    """Normalize a raw language hint to one of `SUPPORTED_LANGUAGES`."""
    if not language:
        return DEFAULT_LANGUAGE
    code = str(language).lower().strip()
    if code.startswith("sw"):
        return "sw"
    return DEFAULT_LANGUAGE


def resolve_language_from_context(context: Optional[Dict[str, Any]]) -> str:
    """Read the active language hint from a routed agent payload.

    Coordinator publishes `context.to_dict()` to BPAs, so the language ends
    up under `metadata.language`. Some payloads (e.g. NEW_USER_MESSAGE) carry
    a top-level `language` field instead, so we accept both.
    """
    if not isinstance(context, dict):
        return DEFAULT_LANGUAGE

    metadata = context.get("metadata")
    if isinstance(metadata, dict):
        meta_lang = metadata.get("language")
        if meta_lang:
            return normalize_language(meta_lang)

    return normalize_language(context.get("language"))


_MESSAGES: Dict[str, Dict[str, str]] = {
    # Greeting agent
    "greeting.initial": {
        "en": (
            "Hello, I am Eva, your support assistant. "
            "I can help you with order tracking, returns and refunds, and account issues. "
            "What can I help you with today?"
        ),
        "sw": (
            "Habari, mimi ni Eva, msaidizi wako wa huduma kwa wateja. "
            "Ninaweza kukusaidia kufuatilia oda, marejesho na marejesho ya pesa, na masuala ya akaunti. "
            "Ningekusaidiaje leo?"
        ),
    },
    "greeting.closing": {
        "en": (
            "Thanks for reaching out. "
            "This conversation is now closed. "
            "Feel free to start a new chat any time you need help."
        ),
        "sw": (
            "Asante kwa kuwasiliana nasi. "
            "Mazungumzo haya sasa yamefungwa. "
            "Karibu kuanzisha mazungumzo mapya wakati wowote unapohitaji msaada."
        ),
    },
    "greeting.general_inquiry_default_query": {
        "en": "The customer asked a general question.",
        "sw": "Mteja ameuliza swali la jumla.",
    },

    # Shipping agent
    "shipping.ask_order_id": {
        "en": (
            "I can definitely help with tracking. "
            "Could you please share your order ID so I can check the latest status? "
            "This ID was sent to you in your order confirmation email along with other order details."
        ),
        "sw": (
            "Ninaweza kukusaidia kufuatilia oda yako. "
            "Tafadhali nipe nambari ya oda yako ili niweze kuangalia hali ya hivi karibuni. "
            "Nambari hii ulitumiwa kwenye barua pepe yako ya uthibitisho wa oda pamoja na maelezo mengine ya oda."
        ),
    },
    "shipping.exception_fallback": {
        "en": (
            "I apologize, but I'm having trouble looking up your order. "
            "Please provide your order number and I'll check the tracking status for you."
        ),
        "sw": (
            "Samahani, nina shida kuangalia oda yako sasa hivi. "
            "Tafadhali nipe nambari ya oda yako na nitakagua hali ya usafirishaji."
        ),
    },
    "shipping.default_user_query": {
        "en": "Where is my order?",
        "sw": "Oda yangu iko wapi?",
    },
    "shipping.fallback.delivered": {
        "en": "Great news! Your order {order_id} has been delivered. If you have any issues, please let us know.",
        "sw": "Habari njema! Oda yako {order_id} imewasilishwa. Iwapo una matatizo yoyote, tafadhali tujulishe.",
    },
    "shipping.fallback.in_transit": {
        "en": "Your order {order_id} is currently in transit. Tracking number: {tracking}. Expected delivery within 2-3 business days.",
        "sw": "Oda yako {order_id} kwa sasa iko njiani. Nambari ya kufuatilia: {tracking}. Inatarajiwa kuwasilishwa ndani ya siku 2-3 za kazi.",
    },
    "shipping.fallback.tracking_via_email": {
        "en": "available in your email",
        "sw": "inapatikana kwenye barua pepe yako",
    },
    "shipping.fallback.processing": {
        "en": "Your order {order_id} is being processed and will ship within 24 hours. You'll receive a tracking number via email once it ships.",
        "sw": "Oda yako {order_id} inashughulikiwa na itasafirishwa ndani ya saa 24. Utapokea nambari ya kufuatilia kupitia barua pepe mara itakaposafirishwa.",
    },
    "shipping.fallback.cancelled": {
        "en": "Your order {order_id} has been cancelled. This can happen due to payment authorization issues or stock availability. Please reply if you'd like help placing a new order.",
        "sw": "Oda yako {order_id} imeghairiwa. Hii inaweza kutokea kutokana na masuala ya idhini ya malipo au upatikanaji wa bidhaa. Tafadhali jibu iwapo ungependa msaada wa kuagiza upya.",
    },
    "shipping.fallback.other_status": {
        "en": "Your order {order_id} status is: {status}. Please check your email for tracking details.",
        "sw": "Hali ya oda yako {order_id} ni: {status}. Tafadhali angalia barua pepe yako kwa maelezo ya kufuatilia.",
    },
    "shipping.fallback.no_order_info": {
        "en": "I couldn't find details for order {order_id}. Please confirm the ID (format ORD12345) and I'll check again.",
        "sw": "Sikuweza kupata maelezo ya oda {order_id}. Tafadhali thibitisha nambari (muundo ORD12345) ili niangalie tena.",
    },
    "shipping.static_kb_short": {
        "en": "Standard shipping: 5-7 business days. Express shipping: 2-3 business days.",
        "sw": "Usafirishaji wa kawaida: siku 5-7 za kazi. Usafirishaji wa haraka: siku 2-3 za kazi.",
    },

    # Returns agent
    "returns.ask_order_id": {
        "en": (
            "I can help with your return. "
            "Please share your order ID so I can review the return status. "
            "This ID was sent to you in your order confirmation email along with other order details."
        ),
        "sw": (
            "Ninaweza kukusaidia na marejesho yako. "
            "Tafadhali nipe nambari ya oda yako ili niweze kuangalia hali ya marejesho. "
            "Nambari hii ulitumiwa kwenye barua pepe yako ya uthibitisho wa oda pamoja na maelezo mengine ya oda."
        ),
    },
    "returns.exception_fallback": {
        "en": (
            "I apologize, but I'm having trouble processing your return request. "
            "Please contact our support team directly for assistance."
        ),
        "sw": (
            "Samahani, nina shida kushughulikia ombi lako la marejesho. "
            "Tafadhali wasiliana na timu yetu ya msaada moja kwa moja kwa msaada zaidi."
        ),
    },
    "returns.default_user_query": {
        "en": "I want to return an item",
        "sw": "Ningependa kurudisha bidhaa",
    },
    "returns.fallback.no_order_info": {
        "en": "I couldn't locate details for order {order_id}. Please confirm the order ID (format ORD12345) so I can help with your return.",
        "sw": "Sikuweza kupata maelezo ya oda {order_id}. Tafadhali thibitisha nambari ya oda (muundo ORD12345) ili nikusaidie na marejesho yako.",
    },
    "returns.fallback.requested": {
        "en": "Your return for order {order_id} is currently under review. Please allow 1-2 business days for an update.",
        "sw": "Ombi lako la marejesho la oda {order_id} kwa sasa linakaguliwa. Tafadhali subiri siku 1-2 za kazi kupata taarifa.",
    },
    "returns.fallback.approved": {
        "en": "Your return for order {order_id} is approved. Please pack the item securely and use the return label sent to your email.",
        "sw": "Marejesho yako ya oda {order_id} yameidhinishwa. Tafadhali funga bidhaa kwa usalama na utumie lebo ya marejesho iliyotumwa kwenye barua pepe yako.",
    },
    "returns.fallback.received": {
        "en": "We've received your returned item for order {order_id}. Your refund should appear within 5-7 business days.",
        "sw": "Tumepokea bidhaa yako iliyorudishwa ya oda {order_id}. Marejesho yako ya pesa yataonekana ndani ya siku 5-7 za kazi.",
    },
    "returns.fallback.rejected": {
        "en": "Your return for order {order_id} was rejected based on inspection results. Please reply and we can walk through the next available options.",
        "sw": "Marejesho yako ya oda {order_id} yamekataliwa kulingana na matokeo ya ukaguzi. Tafadhali jibu ili tukupitishe katika chaguzi zinazopatikana.",
    },
    "returns.fallback.default": {
        "en": "I can help with return options for order {order_id}. Please tell me which item you'd like to return, and I'll guide you through the next steps.",
        "sw": "Ninaweza kukusaidia na chaguzi za marejesho kwa oda {order_id}. Tafadhali niambie ni bidhaa ipi ungependa kurudisha, nami nitakuongoza katika hatua zinazofuata.",
    },
    "returns.static_kb_short": {
        "en": "Standard return policy: Items can be returned within 30 days of purchase.",
        "sw": "Sera ya kawaida ya marejesho: Bidhaa zinaweza kurudishwa ndani ya siku 30 baada ya kununua.",
    },

    # Onboarding agent
    "onboarding.exception_fallback": {
        "en": (
            "I apologize, but I'm having trouble with onboarding guidance right now. "
            "I can still help—please tell me if you need account setup, first login, or getting started steps."
        ),
        "sw": (
            "Samahani, nina shida kutoa mwongozo wa kuanzishwa sasa hivi. "
            "Bado naweza kukusaidia—tafadhali niambie kama unahitaji msaada wa kuanzisha akaunti, kuingia mara ya kwanza, au hatua za kuanza."
        ),
    },
    "onboarding.default_user_query": {
        "en": "How do I get started?",
        "sw": "Ninaanzaje?",
    },
    "onboarding.static_kb_short": {
        "en": "To get started: create your account, verify your email, complete your profile, and sign in.",
        "sw": "Ili kuanza: unda akaunti yako, thibitisha barua pepe yako, kamilisha wasifu wako, na uingie.",
    },
    "onboarding.kb_unavailable_fallback": {
        "en": "Please contact support for onboarding guidance.",
        "sw": "Tafadhali wasiliana na timu ya msaada kwa mwongozo wa kuanzisha akaunti.",
    },
    "onboarding.fallback.create_account": {
        "en": "To create an account, click Sign Up, enter your name and email, choose a strong password, and verify your email from the confirmation message.",
        "sw": "Ili kuunda akaunti, bofya Jisajili, weka jina na barua pepe yako, chagua nenosiri imara, kisha thibitisha barua pepe yako kupitia ujumbe wa uthibitisho.",
    },
    "onboarding.fallback.first_login": {
        "en": "For first login, use the email you verified and your password. If it fails, select Forgot Password to reset and then sign in again.",
        "sw": "Kwa kuingia mara ya kwanza, tumia barua pepe uliyothibitisha na nenosiri lako. Iwapo itashindikana, chagua Umesahau Nenosiri ili kubadilisha kisha uingie tena.",
    },
    "onboarding.fallback.getting_started": {
        "en": "Great question—start by completing your profile, setting preferences, and taking the welcome tour from your dashboard so you can find key features quickly.",
        "sw": "Swali zuri—anza kwa kukamilisha wasifu wako, kuweka mapendeleo, na kuchukua ziara ya ukaribishaji kutoka kwa dashibodi ili upate vipengele muhimu haraka.",
    },
    "onboarding.fallback.welcome_tour": {
        "en": "You can launch the welcome tour from your dashboard help icon. It walks through navigation, core features, and where to find support resources.",
        "sw": "Unaweza kuanzisha ziara ya ukaribishaji kupitia ikoni ya msaada kwenye dashibodi yako. Inakuelekeza katika urambazaji, vipengele muhimu, na mahali pa kupata rasilimali za msaada.",
    },
    "onboarding.fallback.default": {
        "en": "Welcome! I can help you get started with account setup, first login, and the welcome tour. Tell me which step you'd like to do first.",
        "sw": "Karibu! Ninaweza kukusaidia kuanzisha akaunti, kuingia mara ya kwanza, na ziara ya ukaribishaji. Niambie ni hatua ipi ungependa kuanza nayo.",
    },

    # Gemini generic fallbacks (used when the model is unavailable)
    "gemini.fallback.process_return": {
        "en": "I can help you with your return request. Please provide your order number and I'll check the status for you.",
        "sw": "Ninaweza kukusaidia na ombi lako la marejesho. Tafadhali nipe nambari ya oda yako nami nitakagua hali yake.",
    },
    "gemini.fallback.track_order": {
        "en": "I can help you track your order. Please provide your order number and I'll look up the shipping status.",
        "sw": "Ninaweza kukusaidia kufuatilia oda yako. Tafadhali nipe nambari ya oda yako nami nitakagua hali ya usafirishaji.",
    },
    "gemini.fallback.account_issues": {
        "en": "I can assist you with your account. Please let me know what specific issue you're experiencing.",
        "sw": "Ninaweza kukusaidia na akaunti yako. Tafadhali niambie ni tatizo gani mahususi unalokumbana nalo.",
    },
    "gemini.fallback.onboarding": {
        "en": "Welcome! I can help you create your account, complete first login, and get started quickly. Tell me where you'd like to begin.",
        "sw": "Karibu! Ninaweza kukusaidia kuunda akaunti yako, kukamilisha kuingia mara ya kwanza, na kuanza haraka. Niambie ungependa kuanzia wapi.",
    },
    "gemini.fallback.general_inquiry": {
        "en": "I'm here to help! Please provide more details about what you need assistance with.",
        "sw": "Niko hapa kukusaidia! Tafadhali toa maelezo zaidi kuhusu unachohitaji msaada nacho.",
    },
    "gemini.fallback.default": {
        "en": "I'm here to help! How can I assist you today?",
        "sw": "Niko hapa kukusaidia! Ningekusaidiaje leo?",
    },

    # Gateway-owned fixed strings
    "gateway.idle_wake_phrase": {
        "en": "To get started, just type or say \"Hello Eva\".",
        "sw": "Ili kuanza, andika tu au sema \"Hello Eva\".",
    },
    "gateway.operator_delivered": {
        "en": "Your message was delivered to a human operator.",
        "sw": "Ujumbe wako umewasilishwa kwa opereta wa kibinadamu.",
    },
    "gateway.processing_timeout": {
        "en": "Your message is being processed. Please wait a moment...",
        "sw": "Ujumbe wako unashughulikiwa. Tafadhali subiri kidogo...",
    },
    "gateway.voice_processing_timeout": {
        "en": "I'm still processing your voice message. Please try again in a moment.",
        "sw": "Bado ninashughulikia ujumbe wako wa sauti. Tafadhali jaribu tena baada ya muda mfupi.",
    },
    "gateway.escalation.manual_request": {
        "en": "Connecting you with a human agent now. Please hold on.",
        "sw": "Ninakuunganisha na wakala wa kibinadamu sasa. Tafadhali subiri.",
    },
    "gateway.escalation.generic": {
        "en": (
            "I am connecting you with a human support agent now. "
            "Please hold on \u2014 they will be with you shortly."
        ),
        "sw": (
            "Ninakuunganisha na wakala wa msaada wa kibinadamu sasa. "
            "Tafadhali subiri \u2014 watakuwa nawe muda mfupi."
        ),
    },
}


def get_message(key: str, language: Optional[str] = None, **kwargs: Any) -> str:
    """Look up a localized message by key, falling back to English.

    `kwargs` are forwarded to `str.format` so callers can interpolate values
    like order ID or status without losing localization.
    """
    lang = normalize_language(language)
    bundle = _MESSAGES.get(key)
    if not bundle:
        return ""

    template = bundle.get(lang) or bundle.get(DEFAULT_LANGUAGE) or ""
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template


def language_label(language: Optional[str]) -> str:
    """Return the human-readable label for a normalized language code."""
    code = normalize_language(language)
    return {
        "en": "English",
        "sw": "Swahili",
    }.get(code, "English")
