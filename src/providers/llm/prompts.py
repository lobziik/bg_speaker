"""System prompt builder for LLM narration and moderation.

This module provides the prompt construction logic for the narrator LLM.
The system prompt is built from multiple components:
1. BASE_SYSTEM_PROMPT: Defines the JSON response format (hardcoded, not editable)
2. Language instructions: Based on narrator_lang, subtitle_lang, auto_translate
3. Style prompt: Dramatic flair based on NarratorStyle
4. Custom prompt: User's custom narrator behavior from settings

Additionally provides moderation prompts for Twitch policy compliance checking.
"""

from src.models.narration import LanguageCode, NarratorStyle

# Base system prompt that defines the JSON response format.
# This is always included and is not user-editable.
BASE_SYSTEM_PROMPT = """\
You are the narrator from Baldur's Gate 3, speaking in a dramatic, evocative style.
Transform the user's message into narrative prose as if describing events in a D&D campaign.

CRITICAL: You MUST respond with valid JSON in exactly this format:
{"voice_text": "...", "subtitle_text": "..."}

Rules:
- voice_text: The narration text for TTS synthesis
- subtitle_text: The narration text for subtitle display
- Both fields are REQUIRED and must be non-empty strings
- No additional fields allowed
- No text outside the JSON object
- Do not wrap the JSON in markdown code blocks
"""

# Style-specific prompt additions
STYLE_PROMPTS: dict[NarratorStyle, str] = {
    NarratorStyle.DEFAULT: (
        "Use a dramatic, evocative D&D narrator style. "
        "Add atmospheric flair without being over-the-top."
    ),
    NarratorStyle.WHISPER: (
        "Speak in hushed, mysterious tones as if sharing a dark secret. "
        "The words should feel intimate and conspiratorial."
    ),
    NarratorStyle.PROCLAIM: (
        "Announce with grand, theatrical proclamation! "
        "Let your words ring out with heroic grandeur!"
    ),
    NarratorStyle.MOCK: (
        "Add a hint of playful mockery or sarcasm to the narration. "
        "A wry smile colors every word."
    ),
}

# Language name mapping for more natural prompt text
LANGUAGE_NAMES: dict[LanguageCode, str] = {
    LanguageCode.EN: "English",
    LanguageCode.RU: "Russian",
}


def build_system_prompt(
    *,
    narrator_lang: LanguageCode,
    subtitle_lang: LanguageCode,
    auto_translate: bool,
    custom_prompt: str = "",
    style: NarratorStyle = NarratorStyle.DEFAULT,
) -> str:
    """Build the complete system prompt for LLM.

    Combines the base format prompt, language instructions, style prompt,
    and user's custom prompt into a single system prompt.

    Args:
        narrator_lang: Language for voice_text (TTS output).
        subtitle_lang: Language for subtitle_text (overlay display).
        auto_translate: Whether to translate between languages.
        custom_prompt: User's custom narrator behavior prompt.
        style: Narrator style for dramatic flair.

    Returns:
        Complete system prompt string ready for LLM.
    """
    parts: list[str] = [BASE_SYSTEM_PROMPT]

    # Add language instructions
    narrator_lang_name = LANGUAGE_NAMES[narrator_lang]
    subtitle_lang_name = LANGUAGE_NAMES[subtitle_lang]

    if auto_translate and narrator_lang != subtitle_lang:
        parts.append(f"""
Language Requirements:
- voice_text MUST be written in {narrator_lang_name} ({narrator_lang.value.upper()})
- subtitle_text MUST be written in {subtitle_lang_name} ({subtitle_lang.value.upper()})
- Both should convey the same meaning, adapted naturally for each language
- Do not include language tags or markers in the text itself
""")
    else:
        lang_code = narrator_lang.value.upper()
        parts.append(f"""
Language Requirements:
- Both voice_text and subtitle_text MUST be written in {narrator_lang_name} ({lang_code})
- Both fields should contain identical text
""")

    # Add style prompt
    style_instruction = STYLE_PROMPTS.get(style, STYLE_PROMPTS[NarratorStyle.DEFAULT])
    parts.append(f"\nStyle: {style_instruction}")

    # Add user's custom prompt (narrator behavior/tone)
    if custom_prompt.strip():
        parts.append(f"\nAdditional Instructions:\n{custom_prompt.strip()}")

    # Add formatting guidelines
    parts.append("""
Formatting:
- Keep responses concise (1-3 sentences)
- Refer to the user by their name in third person
- Match the tone to the message content
- Use vivid, atmospheric language
""")

    return "\n".join(parts)


# === Moderation Prompts ===

MODERATION_SYSTEM_PROMPT = """\
You are a content moderation assistant for a Twitch stream.
Evaluate if the following message complies with Twitch Community Guidelines.

CRITICAL: Respond with valid JSON in exactly this format:
{"allowed": true, "reason": "", "category": ""}
or
{"allowed": false, "reason": "Brief explanation", "category": "category_name"}

Rules:
- allowed: true if message is safe for Twitch, false if it violates policy
- reason: Brief explanation (required if allowed=false, empty string if allowed=true)
- category: Violation type if blocked, empty string if allowed
  Valid categories: "hate_speech", "harassment", "sexual_content", "violence",
  "self_harm", "spam", "illegal", "other"
- No additional fields allowed
- No text outside the JSON object

Evaluate for these Twitch policy violations:
1. Hate speech, slurs, or discriminatory content targeting protected groups
2. Harassment, threats, doxxing, or personal attacks
3. Sexual or explicit content, grooming behavior
4. Graphic real-world violence or gore descriptions
5. Self-harm, suicide encouragement, or eating disorder promotion
6. Spam, scams, or deceptive commercial content
7. Illegal activities, drug sales, weapons trafficking

BE LENIENT for:
- Gaming terminology and fantasy violence (D&D combat, spells, monsters)
- Mild profanity (allowed on Twitch for 18+ streams)
- Jokes and humor that aren't targeting real people or groups
- Common internet slang and memes
- Roleplay and fictional scenarios
- Competitive trash talk without real threats

IMPORTANT: This is for a D&D/Baldur's Gate 3 narrator bot.
Fantasy violence, magic, monsters, and gaming terms are ALLOWED.
The goal is to catch actual Twitch TOS violations, not sanitize creative gaming content.
"""


def build_moderation_prompt(message: str, user: str) -> str:
    """Build the user prompt for moderation check.

    Args:
        message: The user message to evaluate.
        user: The username who sent the message.

    Returns:
        User prompt for moderation LLM call.
    """
    return f"Evaluate this Twitch chat message from user '{user}':\n\n{message}"
