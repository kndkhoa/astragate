"""
Unit tests for the OpenAI-compatible gateway endpoints.
"""
import uuid
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException, status
from app.api.gateway import chat_completions, embeddings, get_models
from app.services.credit import InsufficientCreditError
from app.services.guardrail import GuardrailResult


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


@pytest.fixture
def mock_virtual_key():
    vk = MagicMock()
    vk.id = uuid.uuid4()
    vk.user_id = uuid.uuid4()
    vk.is_active = True
    vk.rate_limit_rpm = None
    return vk


@pytest.fixture
def mock_model():
    model = MagicMock()
    model.id = uuid.uuid4()
    model.provider_id = uuid.uuid4()
    model.model_id = "groq/llama-3.1-8b-instant"
    model.display_name = "Llama 3.1 8B"
    model.input_price_per_1m = Decimal("0.05")
    model.output_price_per_1m = Decimal("0.08")
    model.is_active = True
    model.created_at = MagicMock()
    model.created_at.timestamp.return_value = 1677610602.0
    return model


@pytest.fixture
def mock_provider():
    p = MagicMock()
    p.id = uuid.uuid4()
    p.name = "groq"
    p.display_name = "Groq"
    return p


def _make_scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_scalars_result(values):
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = values
    scalars_mock.first.return_value = values[0] if values else None
    result.scalars.return_value = scalars_mock
    return result


# ── Tests: GET /v1/models ─────────────────────────────────────────────────────


class TestGetModels:
    @pytest.mark.asyncio
    async def test_get_models_success(self, mock_db, mock_model, mock_provider):
        mock_model.provider = mock_provider
        mock_db.execute.return_value = _make_scalars_result([mock_model])

        response = await get_models(db=mock_db)

        assert response["object"] == "list"
        assert len(response["data"]) == 1
        model_data = response["data"][0]
        assert model_data["id"] == "llama-3.1-8b"
        assert model_data["owned_by"] == "groq"
        assert model_data["created"] == 1677610602


# ── Tests: POST /v1/chat/completions ──────────────────────────────────────────


class TestChatCompletions:
    @pytest.mark.asyncio
    async def test_chat_completions_missing_model(self, mock_db, mock_virtual_key):
        with pytest.raises(HTTPException) as exc_info:
            await chat_completions(
                body={},
                request=MagicMock(),
                background_tasks=MagicMock(),
                db=mock_db,
                virtual_key=mock_virtual_key,
            )
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_chat_completions_model_not_found(self, mock_db, mock_virtual_key):
        mock_db.execute.return_value = _make_scalar_result(None)

        with pytest.raises(HTTPException) as exc_info:
            await chat_completions(
                body={"model": "non-existent-model"},
                request=MagicMock(),
                background_tasks=MagicMock(),
                db=mock_db,
                virtual_key=mock_virtual_key,
            )
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    @patch("app.api.gateway.hold_credit")
    async def test_chat_completions_insufficient_credit(
        self, mock_hold_credit, mock_db, mock_virtual_key, mock_model
    ):
        mock_db.execute.return_value = _make_scalar_result(mock_model)
        mock_hold_credit.side_effect = InsufficientCreditError(
            balance=Decimal("0"), required=Decimal("1")
        )

        with pytest.raises(HTTPException) as exc_info:
            await chat_completions(
                body={"model": "llama-3.1-8b"},
                request=MagicMock(),
                background_tasks=MagicMock(),
                db=mock_db,
                virtual_key=mock_virtual_key,
            )
        assert exc_info.value.status_code == status.HTTP_402_PAYMENT_REQUIRED

    @pytest.mark.asyncio
    @patch("app.api.gateway.hold_credit")
    @patch("app.api.gateway.check_input")
    @patch("app.api.gateway.record_violation")
    @patch("app.api.gateway.release_hold")
    async def test_chat_completions_input_guardrail_violation(
        self,
        mock_release_hold,
        mock_record_violation,
        mock_check_input,
        mock_hold_credit,
        mock_db,
        mock_virtual_key,
        mock_model,
    ):
        mock_db.execute.return_value = _make_scalar_result(mock_model)
        mock_check_input.return_value = GuardrailResult(
            violated=True, keyword_matched="banned_word", content_snippet="bad text"
        )

        with pytest.raises(HTTPException) as exc_info:
            await chat_completions(
                body={
                    "model": "llama-3.1-8b",
                    "messages": [{"role": "user", "content": "banned_word"}],
                },
                request=MagicMock(),
                background_tasks=MagicMock(),
                db=mock_db,
                virtual_key=mock_virtual_key,
            )

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        mock_record_violation.assert_called_once()
        mock_release_hold.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.gateway.hold_credit")
    @patch("app.api.gateway.check_input")
    @patch("app.api.gateway.resolve_provider_for_request")
    @patch("app.api.gateway.release_hold")
    async def test_chat_completions_hard_stop_blocking(
        self,
        mock_release_hold,
        mock_resolve_provider,
        mock_check_input,
        mock_hold_credit,
        mock_db,
        mock_virtual_key,
        mock_model,
    ):
        mock_db.execute.return_value = _make_scalar_result(mock_model)
        mock_check_input.return_value = GuardrailResult(violated=False)

        decision = MagicMock()
        decision.should_block = True
        mock_resolve_provider.return_value = decision

        with pytest.raises(HTTPException) as exc_info:
            await chat_completions(
                body={"model": "llama-3.1-8b"},
                request=MagicMock(),
                background_tasks=MagicMock(),
                db=mock_db,
                virtual_key=mock_virtual_key,
            )

        assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        mock_release_hold.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.gateway.hold_credit")
    @patch("app.api.gateway.check_input")
    @patch("app.api.gateway.resolve_provider_for_request")
    @patch("app.api.gateway.get_client")
    @patch("app.api.gateway.check_output")
    async def test_chat_completions_non_streaming_success(
        self,
        mock_check_output,
        mock_get_client,
        mock_resolve_provider,
        mock_check_input,
        mock_hold_credit,
        mock_db,
        mock_virtual_key,
        mock_model,
    ):
        mock_db.execute.return_value = _make_scalar_result(mock_model)
        mock_check_input.return_value = GuardrailResult(violated=False)
        mock_check_output.return_value = GuardrailResult(violated=False)

        # Provider routing mock
        provider_status = MagicMock()
        provider_status.provider_id = uuid.uuid4()
        decision = MagicMock()
        decision.should_block = False
        decision.is_fallback = False
        decision.provider = provider_status
        mock_resolve_provider.return_value = decision

        # LiteLLM Client mock
        client = AsyncMock()
        client.post_chat = AsyncMock(
            return_value={
                "id": "chatcmpl-123",
                "choices": [
                    {"message": {"role": "assistant", "content": "Hello world"}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            }
        )
        mock_get_client.return_value = client

        bg = MagicMock()

        response = await chat_completions(
            body={"model": "llama-3.1-8b"},
            request=MagicMock(),
            background_tasks=bg,
            db=mock_db,
            virtual_key=mock_virtual_key,
        )

        assert response["id"] == "chatcmpl-123"
        bg.add_task.assert_called_once()  # post_process_usage enqueued


# ── Tests: POST /v1/embeddings ────────────────────────────────────────────────


class TestEmbeddings:
    @pytest.mark.asyncio
    @patch("app.api.gateway.hold_credit")
    @patch("app.api.gateway.check_input")
    @patch("app.api.gateway.resolve_provider_for_request")
    @patch("app.api.gateway.get_client")
    async def test_embeddings_success(
        self,
        mock_get_client,
        mock_resolve_provider,
        mock_check_input,
        mock_hold_credit,
        mock_db,
        mock_virtual_key,
        mock_model,
    ):
        mock_db.execute.return_value = _make_scalar_result(mock_model)
        mock_check_input.return_value = GuardrailResult(violated=False)

        provider_status = MagicMock()
        provider_status.provider_id = uuid.uuid4()
        decision = MagicMock()
        decision.should_block = False
        decision.is_fallback = False
        decision.provider = provider_status
        mock_resolve_provider.return_value = decision

        client = AsyncMock()
        client.post_embeddings = AsyncMock(
            return_value={
                "object": "list",
                "data": [{"object": "embedding", "embedding": [0.1, 0.2], "index": 0}],
                "usage": {"prompt_tokens": 5},
            }
        )
        mock_get_client.return_value = client

        bg = MagicMock()

        response = await embeddings(
            body={"model": "llama-3.1-8b", "input": "test"},
            request=MagicMock(),
            background_tasks=bg,
            db=mock_db,
            virtual_key=mock_virtual_key,
        )

        assert response["object"] == "list"
        assert len(response["data"]) == 1
        bg.add_task.assert_called_once()
