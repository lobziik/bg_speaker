"""Tests for the editable prompt templates."""

import pytest
from pydantic import ValidationError

from src.models.narration import LanguageCode, NarratorStyle
from src.providers.llm.prompts import (
    DEFAULT_BASE_SYSTEM_PROMPT,
    PromptSettings,
    build_moderation_prompt,
    build_system_prompt,
)


class TestPromptSettingsValidation:
    """Templates are validated on the way in, not on the way to the model."""

    def test_defaults_are_valid(self) -> None:
        """The shipped defaults satisfy their own validation rules."""
        prompts = PromptSettings()

        assert prompts.base_system == DEFAULT_BASE_SYSTEM_PROMPT
        assert "$narrator_lang_name" in prompts.language_single
        assert "$message" in prompts.moderation_user

    def test_blank_section_rejected(self) -> None:
        """An empty section would silently degrade narration."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            PromptSettings(base_system="   ")

    def test_unknown_placeholder_rejected(self) -> None:
        """A placeholder the app never fills is a typo, not a feature."""
        with pytest.raises(ValidationError, match="unknown placeholder"):
            PromptSettings(language_single="Speak $klingon_lang_name")

    def test_missing_required_placeholder_rejected(self) -> None:
        """Dropping $message would send the moderator nothing to judge."""
        with pytest.raises(ValidationError, match="missing required placeholder"):
            PromptSettings(moderation_user="Evaluate the message from $user")

    def test_invalid_placeholder_syntax_rejected(self) -> None:
        """A stray dollar sign is caught with a pointer to the $$ escape."""
        with pytest.raises(ValidationError, match=r"invalid placeholder syntax"):
            PromptSettings(moderation_user="Judge $message, costs $ 5")

    def test_escaped_dollar_accepted(self) -> None:
        """$$ renders a literal dollar sign."""
        prompts = PromptSettings(moderation_user="Judge $message (worth 5$$)")

        rendered = build_moderation_prompt(prompts, message="hi", user="Bob")

        assert rendered == "Judge hi (worth 5$)"

    def test_braces_are_not_placeholders(self) -> None:
        """JSON examples in prompts survive - $-templates leave braces alone."""
        prompts = PromptSettings()

        result = build_system_prompt(
            prompts,
            narrator_lang=LanguageCode.EN,
            subtitle_lang=LanguageCode.EN,
            auto_translate=False,
        )

        assert '{"voice_text": "...", "subtitle_text": "..."}' in result


class TestStyleSelection:
    """Every narrator style maps to an editable section."""

    @pytest.mark.parametrize("style", list(NarratorStyle))
    def test_every_style_has_a_prompt(self, style: NarratorStyle) -> None:
        """No style falls through to an empty flair section."""
        assert PromptSettings().style_prompt(style).strip()

    def test_style_prompt_is_used(self) -> None:
        """The selected style's text ends up in the assembled prompt."""
        prompts = PromptSettings(style_whisper="Barely audible.")

        result = build_system_prompt(
            prompts,
            narrator_lang=LanguageCode.EN,
            subtitle_lang=LanguageCode.EN,
            auto_translate=False,
            style=NarratorStyle.WHISPER,
        )

        assert "Style: Barely audible." in result


class TestSystemPromptAssembly:
    """Section order and language branch selection."""

    def test_dual_language_branch(self) -> None:
        """Differing languages with auto-translate use the dual template."""
        prompts = PromptSettings()

        result = build_system_prompt(
            prompts,
            narrator_lang=LanguageCode.EN,
            subtitle_lang=LanguageCode.RU,
            auto_translate=True,
        )

        assert "voice_text MUST be written in English (EN)" in result
        assert "subtitle_text MUST be written in Russian (RU)" in result

    def test_single_language_branch_when_translation_off(self) -> None:
        """With auto-translate off both fields share the narrator language."""
        prompts = PromptSettings()

        result = build_system_prompt(
            prompts,
            narrator_lang=LanguageCode.RU,
            subtitle_lang=LanguageCode.EN,
            auto_translate=False,
        )

        assert "Both voice_text and subtitle_text MUST be written in Russian (RU)" in result
        assert "subtitle_text MUST be written in English" not in result

    def test_custom_prompt_appended(self) -> None:
        """The operator's narrator prompt is included as extra instructions."""
        result = build_system_prompt(
            PromptSettings(),
            narrator_lang=LanguageCode.EN,
            subtitle_lang=LanguageCode.EN,
            auto_translate=False,
            custom_prompt="  Mention the weather.  ",
        )

        assert "Additional Instructions:\nMention the weather." in result

    def test_custom_prompt_omitted_when_blank(self) -> None:
        """A blank custom prompt adds no empty section."""
        result = build_system_prompt(
            PromptSettings(),
            narrator_lang=LanguageCode.EN,
            subtitle_lang=LanguageCode.EN,
            auto_translate=False,
            custom_prompt="   ",
        )

        assert "Additional Instructions" not in result

    def test_sections_appear_in_order(self) -> None:
        """Contract, then language, then style, then custom, then formatting."""
        prompts = PromptSettings()

        result = build_system_prompt(
            prompts,
            narrator_lang=LanguageCode.EN,
            subtitle_lang=LanguageCode.RU,
            auto_translate=True,
            custom_prompt="Be brief.",
            style=NarratorStyle.MOCK,
        )

        positions = [
            result.index("CRITICAL: You MUST respond with valid JSON"),
            result.index("Language Requirements"),
            result.index("Style:"),
            result.index("Additional Instructions"),
            result.index("Formatting:"),
        ]
        assert positions == sorted(positions)

    def test_edited_section_reaches_the_prompt(self) -> None:
        """Editing a section is what actually changes the model's instructions."""
        prompts = PromptSettings(base_system="Speak only in limericks. Return JSON.")

        result = build_system_prompt(
            prompts,
            narrator_lang=LanguageCode.EN,
            subtitle_lang=LanguageCode.EN,
            auto_translate=False,
        )

        assert result.startswith("Speak only in limericks. Return JSON.")


class TestModerationPrompt:
    """Moderation user prompt rendering."""

    def test_substitutes_user_and_message(self) -> None:
        """Both placeholders are filled from the redemption."""
        result = build_moderation_prompt(
            PromptSettings(), message="hello there", user="DragonSlayer"
        )

        assert "DragonSlayer" in result
        assert "hello there" in result

    def test_custom_template(self) -> None:
        """A rewritten template is used verbatim."""
        prompts = PromptSettings(moderation_user="MSG=$message")

        assert build_moderation_prompt(prompts, message="hi", user="Bob") == "MSG=hi"
