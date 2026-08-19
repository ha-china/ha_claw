from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from home_assistant_intents import get_languages
from homeassistant.components import assist_pipeline, conversation
from homeassistant.components.conversation.const import HOME_ASSISTANT_AGENT
from homeassistant.components.conversation.chat_log import async_get_chat_log
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.chat_session import async_get_chat_session
from homeassistant.helpers import config_validation as cv, intent
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import ulid

from .chat_commands import ChatCommandOutcome, async_handle_chat_command
from .chat_commands import consume_stop_request, register_running_task, unregister_running_task
from .const import DOMAIN, VERSION
from .runtime import (
    get_default_agent,
    get_runtime_store,
)
from .runtime.agent.orchestrator import execute_conversation_turn
from .runtime.storage.persona_store import PersonaStore
from .runtime.storage.user_activity import set_active_user_key
from .runtime.storage.user_mapping import MappingStore
from .runtime.utils.i18n import t
from .runtime.llm.response_format import sanitize_response_text

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    agent = FallbackConversationAgent(hass, entry)
    async_add_entities([agent])
    return True


class FallbackConversationAgent(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    last_used_agent: str | None
    entry: ConfigEntry
    hass: HomeAssistant
    _attr_has_entity_name = True
    _attr_chat_response: str | None = None

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.last_used_agent = None
        self._attr_name = None
        self._attr_unique_id = entry.entry_id
        self._attr_supported_features = conversation.ConversationEntityFeature.CONTROL
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title or DOMAIN,
            manufacturer="Claw Assistant",
            model="Home Assistant AI",
            sw_version=VERSION,
        )
        self._last_active = datetime.now(UTC)

    @property
    def supported_languages(self) -> list[str]:
        return get_languages()

    @property
    def state(self) -> str:
        return self._last_active.isoformat()

    @property
    def state_attributes(self):
        attributes = super().state_attributes or {}
        attributes["entity"] = "claw_assistant.ai"
        if self._attr_chat_response is not None:
            attributes["response_content"] = self._attr_chat_response
        if self.last_used_agent is not None:
            attributes["last_used_agent"] = self.last_used_agent
        attributes["last_active"] = self._last_active.isoformat()
        return attributes

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        try:
            assist_pipeline.async_migrate_engine(
                self.hass,
                "conversation",
                self.entry.entry_id,
                self.entry.entry_id,
            )
        except AttributeError:
            pass
        conversation.async_set_agent(self.hass, self.entry, self)
        self.entry.async_on_unload(
            self.entry.add_update_listener(self._async_entry_update_listener)
        )

    async def async_will_remove_from_hass(self) -> None:
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_entry_update_listener(
        self, hass: HomeAssistant, entry: ConfigEntry
    ) -> None:
        self._attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    @staticmethod
    def _resolve_user_key(user_input: conversation.ConversationInput) -> str | None:
        # #### @C3H3-AI ha_claw#14 — _resolve_user_key()
        ctx = getattr(user_input, "context", None)
        user_id = getattr(ctx, "user_id", None) if ctx else None
        conv_id = getattr(user_input, "conversation_id", None)
        return resolve_user_key(user_id, conv_id)

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:



















        user_key = self._resolve_user_key(user_input)
        set_active_user_key(self.hass, user_key)
        # #### @C3H3-AI ha_claw#14 — persona resolve + inject
        if user_key is not None:
            PersonaStore.ensure(user_key, self.hass)
        _user_persona_prompt = PersonaStore.build_system_prompt(user_key)

        if user_input.conversation_id is None:
            user_input = conversation.ConversationInput(
                text=user_input.text,
                conversation_id=ulid.ulid(),
                language=user_input.language,
                context=getattr(user_input, "context", None),
                device_id=getattr(user_input, "device_id", None),
                agent_id=getattr(user_input, "agent_id", None),
                satellite_id=getattr(user_input, "satellite_id", None),
                extra_system_prompt=getattr(user_input, "extra_system_prompt", None),
            )

        command_outcome = await async_handle_chat_command(self.hass, user_input)
        if command_outcome is not None:
            if command_outcome.result is not None:
                return self._finalize_result(command_outcome.result)
            if command_outcome.rewritten_text is not None:
                user_input = conversation.ConversationInput(
                    text=command_outcome.rewritten_text,
                    conversation_id=user_input.conversation_id,
                    language=user_input.language,
                    context=getattr(user_input, "context", None),
                    device_id=getattr(user_input, "device_id", None),
                    agent_id=getattr(user_input, "agent_id", None),
                    satellite_id=getattr(user_input, "satellite_id", None),
                    extra_system_prompt=getattr(user_input, "extra_system_prompt", None),
                )

        current_task = asyncio.current_task()
        register_running_task(self.hass, user_input.conversation_id, current_task)
        try:
            original_async_converse = get_runtime_store(self.hass).get(
                "original_async_converse"
            )
            if not callable(original_async_converse):
                error_msg = t("hook_not_ready", user_input.language)
                intent_response = intent.IntentResponse(language=user_input.language)
                intent_response.async_set_error(
                    intent.IntentResponseErrorCode.UNKNOWN,
                    error_msg,
                )
                return self._finalize_result(
                    conversation.ConversationResult(
                        conversation_id=user_input.conversation_id,
                        response=intent_response,
                    )
                )

            if len(user_input.text or "") <= 200:
                native_result = await self._maybe_handle_native_intent(user_input)
                if native_result is not None:
                    self._record_simple_history_turn(
                        user_input,
                        native_result,
                        source="native_intent",
                    )
                    return self._finalize_result(native_result)

            extra_system_prompt = getattr(user_input, "extra_system_prompt", None)

            caller_prompt = extra_system_prompt
            # #### @C3H3-AI ha_claw#14 — merge persona into extra_system_prompt
            if _user_persona_prompt:
                if caller_prompt:
                    extra_system_prompt = f"{_user_persona_prompt}\n\n{caller_prompt}"
                else:
                    extra_system_prompt = _user_persona_prompt

            delegated_agent_id = getattr(user_input, "agent_id", None)
            if delegated_agent_id == self.entry.entry_id:
                delegated_agent_id = None

            result = await execute_conversation_turn(
                self.hass,
                self.entry,
                original_async_converse,
                text=user_input.text,
                conversation_id=user_input.conversation_id,
                context=getattr(user_input, "context", None),
                language=user_input.language,
                agent_id=delegated_agent_id,
                device_id=getattr(user_input, "device_id", None),
                satellite_id=getattr(user_input, "satellite_id", None),
                extra_system_prompt=extra_system_prompt,
                user_key=user_key,
            )
            return self._finalize_result(result)
        except asyncio.CancelledError:
            self._salvage_partial_turn(user_input)
            if consume_stop_request(self.hass, user_input.conversation_id):
                # The /stop command turn already emits the single canonical
                # confirmation; the cancelled turn stops silently (empty speech
                # produces no bubble and is never prefixed).
                response = intent.IntentResponse(language=user_input.language)
                response.async_set_speech("")
                return self._finalize_result(
                    conversation.ConversationResult(
                        conversation_id=user_input.conversation_id,
                        response=response,
                    )
                )
            raise
        finally:
            unregister_running_task(self.hass, user_input.conversation_id, current_task)

    def _record_simple_history_turn(
        self,
        user_input: conversation.ConversationInput,
        result: conversation.ConversationResult,
        *,
        source: str,
    ) -> None:
        try:
            conv_id = user_input.conversation_id
            if not conv_id or not result or not result.response:
                return
            speech = result.response.speech or {}
            plain = speech.get("plain", {}) if isinstance(speech, dict) else {}
            lang_attr = getattr(result.response, "language", None)
            assistant_text = sanitize_response_text(
                plain.get("original_speech") or plain.get("speech") or "",
                language=lang_attr,
            )
            display_text = sanitize_response_text(
                plain.get("speech") or plain.get("original_speech") or "",
                language=lang_attr,
            )
            user_text = user_input.text or ""
            if not user_text or not assistant_text:
                return

            from .conversation_utils import get_conversation_history

            history = get_conversation_history()
            turns = history.get_history(conv_id)
            if turns:
                last = turns[-1]
                if (
                    (last.user_message or "") == user_text
                    and (last.assistant_response or "") == assistant_text
                ):
                    return
            history.add_turn(
                conv_id,
                user_text,
                assistant_text,
                metadata={
                    "agent_id": self.entry.entry_id,
                    "agent_name": "Claw Assistant",
                    "assistant_display": display_text or assistant_text,
                    "language": user_input.language or "",
                    "channel": "HA",
                    "source": source,
                },
            )
        except Exception:
            _LOGGER.debug("Failed to record simple history turn", exc_info=True)

    async def _maybe_handle_native_intent(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult | None:

        text = (user_input.text or "").strip()
        if (
            not text
            or len(text) > 200
            or "\n" in text
            or text.startswith("/")
            or "```" in text
        ):
            return None

        default_agent = get_default_agent(self.hass)
        try:
            intent_result = await default_agent.async_recognize_intent(user_input)
        except Exception as err:
            _LOGGER.debug("Native intent recognition skipped: %s", err)
            return None

        if not intent_result or intent_result.unmatched_entities:
            return None

        try:
            with async_get_chat_session(
                self.hass, user_input.conversation_id
            ) as session, async_get_chat_log(
                self.hass, session, user_input
            ) as chat_log:
                intent_response = await default_agent._async_process_intent_result(
                    intent_result, user_input, chat_log
                )
        except Exception as err:
            _LOGGER.debug("Native intent processing skipped: %s", err)
            return None

        if intent_response is None:
            return None

        _LOGGER.debug("Native intent handled request: %s", user_input.text[:50])
        return conversation.ConversationResult(
            conversation_id=user_input.conversation_id,
            response=intent_response,
        )

    def _salvage_partial_turn(self, user_input: conversation.ConversationInput) -> None:
        try:
            from .conversation_utils import get_conversation_history
            from .runtime.core.state import get_conversation_status
            from .runtime.agent.agent_fallback import _get_chat_log_content

            conv_id = user_input.conversation_id
            if not conv_id:
                return

            partial_text = ""
            try:
                content_list = _get_chat_log_content(self.hass, conv_id)
                for item in reversed(content_list or []):
                    if getattr(item, "role", None) == "assistant" and getattr(item, "content", None):
                        partial_text = item.content.strip()
                        break
            except Exception:
                pass

            if not partial_text:
                status = get_conversation_status(self.hass)
                partial_text = (status.get("current_thought") or "").strip()

            if not partial_text:
                return

            history = get_conversation_history()
            history.add_turn(
                conv_id,
                user_input.text or "",
                f"[interrupted] {partial_text}",
                metadata={"interrupted": True},
            )
            _LOGGER.debug("Salvaged partial turn for %s (%d chars)", conv_id, len(partial_text))
        except Exception:
            _LOGGER.debug("Failed to salvage partial turn", exc_info=True)

    def _finalize_result(self, result: conversation.ConversationResult):
        self._last_active = datetime.now(UTC)

        if result and result.response and result.response.speech and "plain" in result.response.speech:
            lang = getattr(result.response, "language", None)
            speech = sanitize_response_text(result.response.speech["plain"]["speech"], language=lang)
            result.response.speech["plain"]["speech"] = speech
            result.response.speech["plain"]["original_speech"] = sanitize_response_text(
                result.response.speech["plain"].get("original_speech", speech), language=lang
            )
            self._attr_chat_response = speech
            self.last_used_agent = result.response.speech["plain"].get("agent_id")
            self.async_write_ha_state()
        return result


# #### @C3H3-AI ha_claw#14 — module-level helper extracted from _resolve_user_key
def resolve_user_key(user_id: str | None, conversation_id: str | None) -> str | None:
    """Resolve user_key from HA user_id or IM conversation_id.

    Priority:
    1. explicit user_id (HA App, deterministic)
    2. conversation_id -> MappingStore -> HA user_id
    3. conversation_id -> shadow:{provider}:{ext_id}
    4. None (public-only fallback)
    """
    if user_id:
        return user_id

    if conversation_id:
        mapped = MappingStore.resolve_by_conversation_id(conversation_id)
        if mapped:
            return mapped
        from .const import IM_CHANNEL_NAMES

        for prefix in IM_CHANNEL_NAMES:
            if conversation_id.lower().startswith(prefix.lower()):
                provider = prefix.rstrip(":").lower()
                rest = conversation_id[len(prefix):]
                parts = rest.split(":", 1)
                ext_id = parts[1] if len(parts) >= 2 else parts[0]
                shadow_key = f"shadow:{provider}:{ext_id}"
                PersonaStore.touch_shadow(shadow_key)
                return shadow_key

    return None
