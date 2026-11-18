"""
Unit and Integration Tests for AI VMS Task Generator.
Covers endpoints, validation, heuristic screening, AI orchestration,
clarification handling, and error states.
"""

import json
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.services import prompt_service
from app.services.ai_service import (
    AIService,
    AIServiceAuthError,
    AIServiceConnectionError,
    AIServiceError,
    AIServiceRateLimitError,
    AIServiceTimeoutError,
)
from app.services.task_service import TaskService


client = TestClient(app)


# ==============================================================================
# 1. Base Endpoint Tests
# ==============================================================================

def test_root_endpoint():
    """Verify that root endpoint returns API metadata and documentation links."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "AI VMS Task Generator"
    assert data["status"] == "operational"
    assert "documentation" in data
    assert data["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_health_check_endpoint():
    """Verify health check returns service and model status."""
    response = client.get("/api/v1/tasks/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert "nvidia_api_key_configured" in data


def test_info_endpoint():
    """Verify info endpoint returns model guidelines and option constraints."""
    response = client.get("/api/v1/tasks/info")
    assert response.status_code == 200
    data = response.json()
    assert data["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert data["options_range"]["minimum"] == 4
    assert data["options_range"]["maximum"] == 5
    assert len(data["response_styles"]) >= 5


def test_ui_and_static_endpoints():
    """Verify that static UI and /ui route return the HTML application."""
    ui_resp = client.get("/ui")
    assert ui_resp.status_code == 200
    assert "AI VMS Task Generator" in ui_resp.text
    assert "<!DOCTYPE html>" in ui_resp.text

    static_resp = client.get("/static/index.html")
    assert static_resp.status_code == 200
    assert "AI VMS Task Generator" in static_resp.text


# ==============================================================================
# 2. Input Validation Tests
# ==============================================================================

def test_generate_task_empty_input():
    """Empty input should fail validation with 422 Unprocessable Entity."""
    response = client.post("/api/v1/tasks/generate", json={"raw_input": ""})
    assert response.status_code == 422
    data = response.json()
    assert data["error_type"] == "ValidationError"


def test_generate_task_too_short_input():
    """Input with fewer than 2 characters should fail schema validation."""
    response = client.post("/api/v1/tasks/generate", json={"raw_input": "x"})
    assert response.status_code == 422
    data = response.json()
    assert data["error_type"] == "ValidationError"


def test_generate_task_invalid_num_options():
    """num_options outside range [4, 5] should fail schema validation."""
    # Under range
    response = client.post(
        "/api/v1/tasks/generate",
        json={"raw_input": "camera 3 is broken please fix", "num_options": 2}
    )
    assert response.status_code == 422

    # Over range
    response = client.post(
        "/api/v1/tasks/generate",
        json={"raw_input": "camera 3 is broken please fix", "num_options": 10}
    )
    assert response.status_code == 422


# ==============================================================================
# 3. Heuristic Clarification Tests (Meaningless / Gibberish Inputs)
# ==============================================================================

@pytest.mark.parametrize("gibberish_input", [
    "asdfghjkl",
    "1111111111",
    "??????????",
    "aaaaaaa",
    "bug",
    "fix",
    "help",
    "...",
    "qwrtpsdfg",
])
def test_clarification_triggered_for_meaningless_input(gibberish_input):
    """
    Inputs with pure repetition, keyboard smash, or single ambiguous terms
    must return a clarification prompt instead of fabricating incorrect tasks.
    """
    response = client.post(
        "/api/v1/tasks/generate",
        json={"raw_input": gibberish_input}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "clarification_needed"
    assert data["is_clarification_needed"] is True
    assert data["clarification_question"] is not None
    assert len(data["options"]) == 0
    assert data["total_options"] == 0


# ==============================================================================
# 4. Successful AI Task Generation Tests (Mocked AI Service)
# ==============================================================================

MOCK_FIVE_OPTIONS_AI_JSON = json.dumps({
    "needs_clarification": False,
    "clarification_question": None,
    "options": [
        {
            "id": 1,
            "title": "Resolve Camera 3 Video Stream Failure Post-Reboot on Parking Lot Server",
            "description": "Camera 3 in the North parking lot displays a black screen after system reboot. Immediate restoration of live video feed is required."
        },
        {
            "id": 2,
            "title": "Investigate RTSP Stream Timeout on Parking Lot Camera 3 Following Reboot",
            "description": "Scheduled reboot caused RTSP stream interruption on Camera 3. Diagnostics indicate camera is powered but video feed is not reaching the monitoring matrix."
        },
        {
            "id": 3,
            "title": "Restore RTSP Ingestion Pipeline for North Parking Lot Camera 3",
            "description": "Media server failed to re-establish RTSP session with Camera 3 after host restart. Re-verification of ONVIF driver credentials and network socket is needed."
        },
        {
            "id": 4,
            "title": "Address Parking Lot Surveillance Blind Spot Caused by Camera 3 Outage",
            "description": "Critical security coverage of the North Parking entrance is down due to persistent black screen on Camera 3 after host restart."
        },
        {
            "id": 5,
            "title": "Fix Automatic Reconnection Defect on Camera 3 Following VMS Server Reboot",
            "description": "When the VMS host rebooted, Camera 3 failed to reconnect automatically, causing a persistent black screen in the operator console."
        }
    ]
})


@patch.object(AIService, "generate_completion", new_callable=AsyncMock)
def test_successful_task_generation_5_options(mock_generate):
    """
    Test generating 5 distinct task options from rough, informal user notes.
    """
    mock_generate.return_value = MOCK_FIVE_OPTIONS_AI_JSON

    payload = {
        "raw_input": "camera 3 screen black after reboot on parking lot vms server fix urgent",
        "num_options": 5
    }

    response = client.post("/api/v1/tasks/generate", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "success"
    assert data["is_clarification_needed"] is False
    assert data["clarification_question"] is None
    assert data["total_options"] == 5
    assert len(data["options"]) == 5
    assert data["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"

    # Verify each option contains required title and description
    for idx, opt in enumerate(data["options"], start=1):
        assert opt["id"] == idx
        assert len(opt["title"]) > 5
        assert len(opt["description"]) > 10


@patch.object(AIService, "generate_completion", new_callable=AsyncMock)
def test_successful_task_generation_4_options(mock_generate):
    """
    Test requesting 4 options returns exactly 4 candidate options.
    """
    mock_generate.return_value = MOCK_FIVE_OPTIONS_AI_JSON

    payload = {
        "raw_input": "visitor badge print button not working when visitor has long name throws 500",
        "num_options": 4
    }

    response = client.post("/api/v1/tasks/generate", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "success"
    assert data["total_options"] == 4
    assert len(data["options"]) == 4


@patch.object(AIService, "generate_completion", new_callable=AsyncMock)
def test_ai_returned_clarification_response(mock_generate):
    """
    Test when AI model itself determines the input is too vague and requests clarification.
    """
    ai_clarification_json = json.dumps({
        "needs_clarification": True,
        "clarification_question": "Could you please specify which server was rebooted and what camera model is experiencing the issue?",
        "options": []
    })
    mock_generate.return_value = ai_clarification_json

    payload = {
        "raw_input": "server rebooted and something is not working please check",
        "num_options": 5
    }

    response = client.post("/api/v1/tasks/generate", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "clarification_needed"
    assert data["is_clarification_needed"] is True
    assert "clarification_question" in data
    assert data["clarification_question"] == "Could you please specify which server was rebooted and what camera model is experiencing the issue?"
    assert len(data["options"]) == 0
    assert data["total_options"] == 0


# ==============================================================================
# 5. Upstream AI Error Handling Tests
# ==============================================================================

@patch.object(AIService, "generate_completion", new_callable=AsyncMock)
def test_ai_auth_error_handling(mock_generate):
    """Missing or invalid API key should raise 503 Service Unavailable."""
    mock_generate.side_effect = AIServiceAuthError("NVIDIA_API_KEY is not configured.")

    response = client.post(
        "/api/v1/tasks/generate",
        json={"raw_input": "camera 3 screen black after reboot on parking lot vms server fix urgent"}
    )
    assert response.status_code == 503
    data = response.json()
    assert "NVIDIA_API_KEY" in data["message"]


@patch.object(AIService, "generate_completion", new_callable=AsyncMock)
def test_ai_rate_limit_error_handling(mock_generate):
    """Rate limit error should map to 503 Service Unavailable."""
    mock_generate.side_effect = AIServiceRateLimitError("Rate limit exceeded.")

    response = client.post(
        "/api/v1/tasks/generate",
        json={"raw_input": "camera 3 screen black after reboot on parking lot vms server fix urgent"}
    )
    assert response.status_code == 503
    data = response.json()
    assert "Rate limit" in data["message"]


@patch.object(AIService, "generate_completion", new_callable=AsyncMock)
def test_ai_connection_timeout_handling(mock_generate):
    """Connection or timeout failure should map to 502 Bad Gateway."""
    mock_generate.side_effect = AIServiceTimeoutError("Request timed out.")

    response = client.post(
        "/api/v1/tasks/generate",
        json={"raw_input": "camera 3 screen black after reboot on parking lot vms server fix urgent"}
    )
    assert response.status_code == 502
    data = response.json()
    assert "Error connecting to NVIDIA Nemotron" in data["message"]


# ==============================================================================
# 6. JSON Sanitization & Parsing Tests
# ==============================================================================

def test_extract_json_with_markdown_fences():
    """Verify that AIService.extract_json properly parses ```json code fences."""
    raw = "```json\n{\"test\": 123}\n```"
    parsed = AIService.extract_json(raw)
    assert parsed == {"test": 123}


def test_extract_json_with_surrounding_chatter():
    """Verify extract_json strips conversational artifacts around JSON."""
    raw = "Here is your output:\n{\"status\": \"ok\", \"count\": 5}\nHope this helps!"
    parsed = AIService.extract_json(raw)
    assert parsed == {"status": "ok", "count": 5}


def test_extract_json_invalid_raises():
    """Invalid JSON should raise AIServiceError."""
    with pytest.raises(AIServiceError):
        AIService.extract_json("not a valid json object")


# ==============================================================================
# 7. Prompt Service Unit Tests
# ==============================================================================

def test_prompt_service_structure():
    """Verify prompt formatting and instructions."""
    system_prompt = prompt_service.get_system_prompt()
    assert "Enterprise Systems" in system_prompt
    assert "TASK / DESCRIPTION ONLY" in system_prompt
    assert "CLARIFICATION CRITERIA" in system_prompt

    user_prompt = prompt_service.build_user_prompt("camera 3 broken", num_options=5)
    assert "camera 3 broken" in user_prompt
    assert "5 distinct task options" in user_prompt

    messages = prompt_service.build_messages("camera 3 broken", num_options=4)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


# ==============================================================================
# 8. Fallback Provider Tests (Nemotron 3.5 Bolt E4B)
# ==============================================================================

@pytest.mark.asyncio
async def test_automatic_fallback_on_primary_rate_limit():
    """
    Verify that when primary model (Nemotron 3.5 Lightning) encounters a rate limit (429),
    the system automatically switches to the fallback model (Nemotron 3.5 Bolt E4B).
    """
    settings = Settings(
        NVIDIA_API_KEY="test-key",
        FALLBACK_ENABLED=True,
        FALLBACK_MODEL="nvidia/nemotron-3.5-bolt-e4b"
    )
    ai_service = AIService(settings=settings)

    mock_client = AsyncMock()
    ai_service._primary_client = mock_client
    ai_service._fallback_client = mock_client

    # Primary fails with rate limit, fallback succeeds
    call_counts = {"primary": 0, "fallback": 0}

    async def mock_call(client, model, messages, temp, tokens):
        if model == settings.NVIDIA_MODEL:
            call_counts["primary"] += 1
            raise AIServiceRateLimitError("Rate limit exceeded on primary model")
        elif model == settings.FALLBACK_MODEL:
            call_counts["fallback"] += 1
            return '{"needs_clarification": false, "options": [{"id": 1, "title": "Fallback Task", "description": "Resolved via Bolt E4B"}]}'
        raise ValueError(f"Unexpected model: {model}")

    with patch.object(ai_service, "_call_model", side_effect=mock_call):
        result = await ai_service.generate_completion(messages=[{"role": "user", "content": "test"}])
        assert ai_service.is_fallback_active is True
        assert ai_service.last_model_used == "nvidia/nemotron-3.5-bolt-e4b"
        assert "Fallback Task" in result


def test_task_endpoint_reports_fallback_usage():
    """Verify that TaskGenerationResponse correctly sets is_fallback_used and model."""
    fake_ai_output = json.dumps({
        "needs_clarification": False,
        "options": [{"id": 1, "title": "Bolt Title", "description": "Bolt Description"}]
    })

    with patch.object(AIService, "generate_completion", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = fake_ai_output

        # Simulate ai_service reporting fallback active
        def side_effect(*args, **kwargs):
            return fake_ai_output

        mock_gen.side_effect = side_effect

        with patch("app.services.task_service.AIService") as MockAIServiceClass:
            mock_instance = MockAIServiceClass.return_value
            mock_instance.generate_completion = AsyncMock(return_value=fake_ai_output)
            mock_instance.extract_json = AIService.extract_json
            mock_instance.last_model_used = "nvidia/nemotron-3.5-bolt-e4b"
            mock_instance.is_fallback_active = True

            resp = client.post(
                "/api/v1/tasks/generate",
                json={"raw_input": "camera 3 screen black after reboot on parking lot vms server fix urgent"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["is_fallback_used"] is True
            assert data["model"] == "nvidia/nemotron-3.5-bolt-e4b"
            assert "Served via fallback" in data["message"]


