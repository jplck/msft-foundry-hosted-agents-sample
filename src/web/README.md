# Web Frontend: Order Form + Chat Assistant

This folder contains a minimal FastAPI backend and static frontend that
let you interact with an Azure AI agent from a two-panel UI:

- **Left panel** – order form with dropdowns and free-text fields.
- **Right panel** – chat interface that calls a Foundry-hosted agent to
  help fill and validate the form.

## Running locally

1. Install dependencies (from repo root):

   ```bash
   pip install -r requirements.txt
   ```

2. Ensure your environment has a valid Azure AI project endpoint and model deployment:

   ```bash
   export PROJECT_ENDPOINT="<your-project-endpoint>"          # e.g. https://.../api/projects/<project-name>
   export AZURE_AI_MODEL_DEPLOYMENT_NAME="<your-deployment>"  # e.g. o4-mini
   ```

   The backend uses `DefaultAzureCredential`, so you must also be
   authenticated with Azure (e.g. via `az login`) or have appropriate
   environment-based credentials configured.

3. Start the FastAPI app (from repo root):

   ```bash
   uvicorn src.web.app:app --reload
   ```

4. Open the UI in a browser:

   ```text
   http://127.0.0.1:8000/
   ```

From there you can:

- Type into the **chat** and the assistant will see your current form
  state and respond.
- Click **Validate with Assistant** to have the same agent validate the
  form and display issues under the form.
- Submit the form (demo-only; no external side effects) after
  validation passes.
