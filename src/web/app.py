from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from agent_framework import ChatAgent
from agent_framework.azure import AzureAIAgentClient
from azure.identity import DefaultAzureCredential
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.core.exceptions import ResourceExistsError
from dotenv import load_dotenv

# Load environment from repo root .env
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


# --- Global agent setup (reused across requests) ---

AGENT_NAME = "form-helper-agent"

SYSTEM_INSTRUCTIONS = (
    "You are a helpful assistant embedded in a web page. "
    "You help the user fill out and validate an order form with fields: "
    "customer_name, email, product_category, product_name, quantity, notes. "
    "When the user asks for validation or submits the form, carefully "
    "check all fields and respond with clear guidance. "
    "When asked explicitly to validate, respond with short, direct text "
    "explaining any problems and how to fix them. Hint: Product categories are Laptop, Headphone, Monitor, Keyboard"
)

# Global instances
_project_client: AIProjectClient | None = None
_chat_client: AzureAIAgentClient | None = None
_chat_agent: ChatAgent | None = None
_agent_id: str | None = None

# Session-based conversation threads (simple in-memory store)
# In production, use Redis or database keyed by session ID
_conversation_threads: dict[str, Any] = {}


def _get_project_endpoint() -> str:
    endpoint = os.getenv("PROJECT_ENDPOINT") or os.getenv("AZURE_AI_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "PROJECT_ENDPOINT or AZURE_AI_PROJECT_ENDPOINT must be set in your environment"
        )
    return endpoint


def _get_model_deployment() -> str:
    return os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4o")


def get_project_client() -> AIProjectClient:
    """Get or create the AIProjectClient instance."""
    global _project_client
    if _project_client is None:
        _project_client = AIProjectClient(
            endpoint=_get_project_endpoint(),
            credential=DefaultAzureCredential(),
        )
    return _project_client


async def create_agent_if_needed() -> str:
    """Create agent once if it doesn't exist yet."""
    global _agent_id
    
    if _agent_id is not None:
        print(f"Agent already exists with ID: {_agent_id}")
        return _agent_id
    
    project_client = get_project_client()
    model_deployment = _get_model_deployment()

    print("Creating new reusable agent instance...")
    
    try:
        agent = await project_client.agents.create(
            name=AGENT_NAME,
            definition=PromptAgentDefinition(
                instructions=SYSTEM_INSTRUCTIONS,
                model=model_deployment,
            ),
        )
        _agent_id = agent.id
        print(f"✅ Agent '{AGENT_NAME}' created with ID: {_agent_id}")
        return _agent_id
        
    except ResourceExistsError:
        print(f"ℹ️  Agent '{AGENT_NAME}' already exists, reusing.")
        _agent_id = AGENT_NAME
        return _agent_id


def initialize_chat_client() -> None:
    """Initialize the ChatAgent wrapper after agent exists."""
    global _chat_client, _chat_agent, _project_client
    
    print("Initializing Azure AI chat client...")

    # Get the project client (already initialized)
    project_client = get_project_client()

    _chat_client = AzureAIAgentClient(
        project_endpoint=_get_project_endpoint(),
        agent_name=AGENT_NAME,
        model_deployment_name=_get_model_deployment(),
        credential=DefaultAzureCredential(),
    )
    
    print("Chat client initialized")

    _chat_agent = ChatAgent(chat_client=_chat_client)
    print("Agent wrapper created")


async def call_agent(prompt: str, session_id: str | None = None) -> str:
    """Send a prompt to the agent and return the text reply.
    
    If session_id is provided, the conversation thread is persisted
    so the agent remembers previous messages in that session.
    """
    global _chat_agent, _conversation_threads

    if _chat_agent is None:
        raise RuntimeError("Chat agent not initialized")

    # Use existing thread for this session, or create a new one
    if session_id and session_id in _conversation_threads:
        thread = _conversation_threads[session_id]
    else:
        thread = _chat_agent.get_new_thread()
        if session_id:
            _conversation_threads[session_id] = thread

    # Collect streamed chunks into a single response
    chunks: list[str] = []
    async for chunk in _chat_agent.run_stream(prompt, thread=thread):
        if chunk.text:
            chunks.append(chunk.text)

    return "".join(chunks)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle: initialize client and ensure agent exists."""
    # First ensure the agent exists in Foundry
    await create_agent_if_needed()
    
    # Then initialize the ChatAgent wrapper
    initialize_chat_client()
    
    yield

    # Cleanup on shutdown
    if _chat_client is not None and hasattr(_chat_client, "close"):
        try:
            await _chat_client.close()
        except Exception:
            pass
    if _project_client is not None:
        try:
            await _project_client.close()
        except Exception:
            pass


app = FastAPI(title="Form + Chat Frontend Backend", lifespan=lifespan)

# Allow browser clients from anywhere for now; tighten in real deployments
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files from the same folder
_static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/")
async def index() -> FileResponse:
    """Serve the simple static frontend."""
    return FileResponse(str(_static_dir / "index.html"))


@app.post("/api/chat")
async def chat(payload: dict[str, Any]) -> dict[str, Any]:
    """Chat endpoint used by the right-hand panel.

    Expects JSON body: {"message": str, "form": {...optional...}, "session_id": str}
    """

    message = payload.get("message")
    form_state = payload.get("form") or {}
    session_id = payload.get("session_id") or "default"

    if not isinstance(message, str) or not message.strip():
        raise HTTPException(status_code=400, detail="'message' must be a non-empty string")

    # Combine user message with current form state so the agent can
    # use it to suggest values or corrections.
    combined_prompt = (
        f"User message: {message}\n\n"
        f"Current form state as JSON: {json.dumps(form_state, ensure_ascii=False)}\n\n"
        "IMPORTANT INSTRUCTIONS:\n"
        "1. Remember all previous messages in this conversation.\n"
        "2. If the user wants to change a form field value, respond with a JSON block "
        "containing the updates in this exact format: ```json\n{\"form_updates\": {\"field_name\": \"new_value\"}}\n```\n"
        "3. After the JSON block, add your conversational response.\n"
        "4. Valid form fields are: customer_name, email, product_category, product_name, quantity, notes.\n"
        "5. product_category must be one of: laptop, headphone, monitor, keyboard (lowercase).\n"
        "6. product_name is a free text field for the specific product name/model.\n"
        "7. If the user is just asking questions (not updating), respond normally without JSON."
    )

    reply = await call_agent(combined_prompt, session_id=session_id)
    
    # Parse any form updates from the agent's response
    form_updates = {}
    json_match = re.search(r'```json\s*({.*?})\s*```', reply, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group(1))
            if "form_updates" in parsed:
                form_updates = parsed["form_updates"]
                # Remove the JSON block from the displayed reply
                reply = re.sub(r'```json\s*{.*?}\s*```\s*', '', reply, flags=re.DOTALL).strip()
        except json.JSONDecodeError:
            pass
    
    return {"reply": reply, "form_updates": form_updates}


@app.post("/api/validate")
async def validate_form(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the full form using the same agent.

    Expects JSON body with all form fields.

    Returns a structured object: {"valid": bool, "issues": [...], "raw": str}
    """

    form_state = payload or {}

    validation_prompt = (
        "You are validating an order form. "
        "The form fields are provided as JSON. "
        "Respond ONLY with JSON of the shape: "
        '{"valid": bool, "issues": ["..."]}. '
        "If valid is true, issues may be empty. "
        "Be strict about email format and that quantity is a positive integer.\n\n"
        f"Form JSON: {json.dumps(form_state, ensure_ascii=False)}"
    )

    raw = await call_agent(validation_prompt)
    try:
        parsed = json.loads(raw)
        valid = bool(parsed.get("valid"))
        issues = parsed.get("issues") or []
        if not isinstance(issues, list):
            issues = [str(issues)]
    except Exception:
        # Fall back to treating the response as unstructured text
        valid = False
        issues = ["Agent returned non-JSON response", raw]

    return {"valid": valid, "issues": issues, "raw": raw}
