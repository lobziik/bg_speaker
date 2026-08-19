"""Prompt templates for LLM narration and moderation.

The texts in this module are *defaults*. The effective prompts live in the
database under the ``prompts`` settings key (:class:`PromptSettings`) and are
edited at Settings -> Prompts; the defaults here seed a fresh install and back
the "Reset to default" button.

The narration system prompt is assembled from several editable sections:

1. ``base_system``: defines the JSON response format
2. ``language_dual`` / ``language_single``: chosen by narrator/subtitle language
   and the auto-translate setting
3. ``style_*``: dramatic flair per :class:`NarratorStyle`
4. the operator's free-form prompt from Narrator settings
5. ``formatting``: length and tone guidelines

Sections that vary with runtime state are ``string.Template`` strings using
``$placeholder`` syntax. Braces are deliberately left alone by that syntax, so a
prompt can contain literal JSON such as ``{"voice_text": "..."}``.
"""

from string import Template
from typing import assert_never

from pydantic import field_validator

from src.core.types import StrictModel
from src.models.narration import LanguageCode, NarratorStyle

# Defines the JSON response contract. Editable, but the field names must keep
# matching LLMNarrationResponse or every narration will fail to parse.
DEFAULT_BASE_SYSTEM_PROMPT = """\
You are a theatrical fantasy narrator with a rich, evocative voice.
Transform the user's message into narrative prose, as if recounting events from a
tabletop roleplaying adventure.

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

# Used when auto-translate is on and the two languages differ.
DEFAULT_LANGUAGE_DUAL_PROMPT = """\
Language Requirements:
- voice_text MUST be written in $narrator_lang_name ($narrator_lang_code)
- subtitle_text MUST be written in $subtitle_lang_name ($subtitle_lang_code)
- Both should convey the same meaning, adapted naturally for each language
- Do not include language tags or markers in the text itself
"""

# Used when both fields share a language.
DEFAULT_LANGUAGE_SINGLE_PROMPT = """\
Language Requirements:
- Both voice_text and subtitle_text MUST be written in $narrator_lang_name ($narrator_lang_code)
- Both fields should contain identical text
"""

DEFAULT_STYLE_DEFAULT_PROMPT = (
    "Use a dramatic, evocative fantasy narrator style. "
    "Add atmospheric flair without being over-the-top."
)
DEFAULT_STYLE_WHISPER_PROMPT = (
    "Speak in hushed, mysterious tones as if sharing a dark secret. "
    "The words should feel intimate and conspiratorial."
)
DEFAULT_STYLE_PROCLAIM_PROMPT = (
    "Announce with grand, theatrical proclamation! "
    "Let your words ring out with heroic grandeur!"
)
DEFAULT_STYLE_MOCK_PROMPT = (
    "Add a hint of playful mockery or sarcasm to the narration. "
    "A wry smile colors every word."
)

DEFAULT_FORMATTING_PROMPT = """\
Formatting:
- Keep responses concise (1-3 sentences)
- Refer to the user by their name in third person
- Match the tone to the message content
- Use vivid, atmospheric language
"""

DEFAULT_MODERATION_SYSTEM_PROMPT = """\
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
- Gaming terminology and fantasy violence (combat, spells, monsters)
- Mild profanity (allowed on Twitch for 18+ streams)
- Jokes and humor that aren't targeting real people or groups
- Common internet slang and memes
- Roleplay and fictional scenarios
- Competitive trash talk without real threats

IMPORTANT: This is for a fantasy narrator bot on a gaming stream.
Fantasy violence, magic, monsters, and gaming terms are ALLOWED.
The goal is to catch actual Twitch TOS violations, not sanitize creative gaming content.
"""

DEFAULT_MODERATION_USER_PROMPT = (
    "Evaluate this Twitch chat message from user '$user':\n\n$message"
)

# Language name mapping for more natural prompt text
LANGUAGE_NAMES: dict[LanguageCode, str] = {
    LanguageCode.EN: "English",
    LanguageCode.RU: "Russian",
}

# Placeholders each templated section may use, and which it cannot do without.
LANGUAGE_DUAL_PLACEHOLDERS = frozenset({
    "narrator_lang_name",
    "narrator_lang_code",
    "subtitle_lang_name",
    "subtitle_lang_code",
})
LANGUAGE_DUAL_REQUIRED = frozenset({"narrator_lang_name", "subtitle_lang_name"})

LANGUAGE_SINGLE_PLACEHOLDERS = frozenset({"narrator_lang_name", "narrator_lang_code"})
LANGUAGE_SINGLE_REQUIRED = frozenset({"narrator_lang_name"})

MODERATION_USER_PLACEHOLDERS = frozenset({"user", "message"})
MODERATION_USER_REQUIRED = frozenset({"message"})


def validate_template(
    value: str,
    *,
    field: str,
    allowed: frozenset[str],
    required: frozenset[str],
) -> str:
    """Check that a prompt template only uses placeholders the app can fill.

    Runs when settings are saved and again when they are loaded, so a prompt
    that would blow up mid-narration is rejected while the operator is still
    looking at the form.

    Args:
        value: Template text to check.
        field: Field name, for error messages.
        allowed: Placeholder names the app substitutes for this field.
        required: Placeholders the template cannot usefully omit.

    Returns:
        The template unchanged.

    Raises:
        ValueError: If the syntax is invalid, an unknown placeholder is used,
            or a required placeholder is missing.
    """
    template = Template(value)

    if not template.is_valid():
        raise ValueError(
            f"{field}: invalid placeholder syntax. Use $name or ${{name}}, "
            f"and write $$ for a literal dollar sign."
        )

    identifiers = set(template.get_identifiers())

    unknown = sorted(identifiers - allowed)
    if unknown:
        raise ValueError(
            f"{field}: unknown placeholder(s) {unknown}. Available: {sorted(allowed)}"
        )

    missing = sorted(required - identifiers)
    if missing:
        raise ValueError(
            f"{field}: missing required placeholder(s) {missing} - without them the "
            f"model never sees that part of the input."
        )

    return value


class PromptSettings(StrictModel):
    """Editable prompt sections, stored under the ``prompts`` settings key.

    Attributes:
        base_system: JSON response contract for narration. The field names it
            describes must stay in sync with ``LLMNarrationResponse``.
        language_dual: Language instructions when voice and subtitle languages
            differ and auto-translate is on.
        language_single: Language instructions when both share a language.
        style_default: Flair for the default narrator style.
        style_whisper: Flair for the whisper style.
        style_proclaim: Flair for the proclaim style.
        style_mock: Flair for the mock style.
        formatting: Length and tone guidelines appended last.
        moderation_system: System prompt for the Twitch-policy check.
        moderation_user: User prompt carrying the message under review.
    """

    base_system: str = DEFAULT_BASE_SYSTEM_PROMPT
    language_dual: str = DEFAULT_LANGUAGE_DUAL_PROMPT
    language_single: str = DEFAULT_LANGUAGE_SINGLE_PROMPT
    style_default: str = DEFAULT_STYLE_DEFAULT_PROMPT
    style_whisper: str = DEFAULT_STYLE_WHISPER_PROMPT
    style_proclaim: str = DEFAULT_STYLE_PROCLAIM_PROMPT
    style_mock: str = DEFAULT_STYLE_MOCK_PROMPT
    formatting: str = DEFAULT_FORMATTING_PROMPT
    moderation_system: str = DEFAULT_MODERATION_SYSTEM_PROMPT
    moderation_user: str = DEFAULT_MODERATION_USER_PROMPT

    @field_validator("*")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        """Reject empty sections - a blank prompt silently degrades narration."""
        if not value.strip():
            raise ValueError("Prompt section cannot be empty")
        return value

    @field_validator("language_dual")
    @classmethod
    def check_language_dual(cls, value: str) -> str:
        """Validate the dual-language template's placeholders."""
        return validate_template(
            value,
            field="language_dual",
            allowed=LANGUAGE_DUAL_PLACEHOLDERS,
            required=LANGUAGE_DUAL_REQUIRED,
        )

    @field_validator("language_single")
    @classmethod
    def check_language_single(cls, value: str) -> str:
        """Validate the single-language template's placeholders."""
        return validate_template(
            value,
            field="language_single",
            allowed=LANGUAGE_SINGLE_PLACEHOLDERS,
            required=LANGUAGE_SINGLE_REQUIRED,
        )

    @field_validator("moderation_user")
    @classmethod
    def check_moderation_user(cls, value: str) -> str:
        """Validate the moderation user prompt's placeholders."""
        return validate_template(
            value,
            field="moderation_user",
            allowed=MODERATION_USER_PLACEHOLDERS,
            required=MODERATION_USER_REQUIRED,
        )

    def style_prompt(self, style: NarratorStyle) -> str:
        """Return the flair section for a narrator style.

        Args:
            style: Style requested for this narration.

        Returns:
            The configured prompt text for that style.
        """
        match style:
            case NarratorStyle.DEFAULT:
                return self.style_default
            case NarratorStyle.WHISPER:
                return self.style_whisper
            case NarratorStyle.PROCLAIM:
                return self.style_proclaim
            case NarratorStyle.MOCK:
                return self.style_mock
            case _:
                assert_never(style)


def build_system_prompt(
    prompts: PromptSettings,
    *,
    narrator_lang: LanguageCode,
    subtitle_lang: LanguageCode,
    auto_translate: bool,
    custom_prompt: str = "",
    style: NarratorStyle = NarratorStyle.DEFAULT,
) -> str:
    """Build the complete system prompt for LLM narration.

    Combines the stored sections - format contract, language instructions,
    style flair, the operator's custom prompt and formatting guidelines - into
    a single system prompt.

    Args:
        prompts: Prompt sections loaded from settings.
        narrator_lang: Language for voice_text (TTS output).
        subtitle_lang: Language for subtitle_text (overlay display).
        auto_translate: Whether to translate between languages.
        custom_prompt: Operator's custom narrator behaviour prompt.
        style: Narrator style for dramatic flair.

    Returns:
        Complete system prompt string ready for the LLM.
    """
    parts: list[str] = [prompts.base_system]

    narrator_lang_name = LANGUAGE_NAMES[narrator_lang]
    subtitle_lang_name = LANGUAGE_NAMES[subtitle_lang]

    if auto_translate and narrator_lang != subtitle_lang:
        language_section = Template(prompts.language_dual).substitute(
            narrator_lang_name=narrator_lang_name,
            narrator_lang_code=narrator_lang.value.upper(),
            subtitle_lang_name=subtitle_lang_name,
            subtitle_lang_code=subtitle_lang.value.upper(),
        )
    else:
        language_section = Template(prompts.language_single).substitute(
            narrator_lang_name=narrator_lang_name,
            narrator_lang_code=narrator_lang.value.upper(),
        )

    parts.append(f"\n{language_section}")
    parts.append(f"\nStyle: {prompts.style_prompt(style)}")

    if custom_prompt.strip():
        parts.append(f"\nAdditional Instructions:\n{custom_prompt.strip()}")

    parts.append(f"\n{prompts.formatting}")

    return "\n".join(parts)


def build_moderation_prompt(prompts: PromptSettings, message: str, user: str) -> str:
    """Build the user prompt for the moderation check.

    Args:
        prompts: Prompt sections loaded from settings.
        message: The user message to evaluate.
        user: The username who sent the message.

    Returns:
        User prompt for the moderation LLM call.
    """
    return Template(prompts.moderation_user).substitute(user=user, message=message)
