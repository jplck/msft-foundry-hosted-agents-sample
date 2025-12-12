// Generate a unique session ID for this browser tab
const SESSION_ID = crypto.randomUUID ? crypto.randomUUID() : `session-${Date.now()}-${Math.random().toString(36).slice(2)}`;

function getFormState() {
  const form = document.getElementById("order-form");
  const data = new FormData(form);
  return Object.fromEntries(data.entries());
}

function updateFormFields(updates) {
  // Apply form updates received from the agent
  if (!updates || typeof updates !== 'object') return;
  
  const form = document.getElementById("order-form");
  for (const [field, value] of Object.entries(updates)) {
    const input = form.querySelector(`[name="${field}"]`);
    if (input) {
      input.value = value;
      // Add visual feedback for updated field
      input.style.transition = 'background-color 0.3s';
      input.style.backgroundColor = '#d4edda';
      setTimeout(() => {
        input.style.backgroundColor = '';
      }, 1500);
    }
  }
}

function appendChatMessage(role, text) {
  const log = document.getElementById("chat-log");
  const div = document.createElement("div");
  div.className = `chat-message ${role}`;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function sendChat(message) {
  appendChatMessage("user", message);

  const payload = {
    message,
    form: getFormState(),
    session_id: SESSION_ID,  // Include session ID for conversation memory
  };

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      throw new Error(`Chat error: ${res.status}`);
    }

    const data = await res.json();
    appendChatMessage("assistant", data.reply ?? "(no reply)");
    
    // Apply any form updates from the agent
    if (data.form_updates && Object.keys(data.form_updates).length > 0) {
      updateFormFields(data.form_updates);
    }
  } catch (err) {
    console.error(err);
    appendChatMessage("assistant", "Sorry, there was a problem contacting the assistant.");
  }
}

async function validateWithAssistant() {
  const validationBox = document.getElementById("validation-messages");
  validationBox.textContent = "Validating with assistant...";

  try {
    const res = await fetch("/api/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(getFormState()),
    });

    if (!res.ok) {
      throw new Error(`Validate error: ${res.status}`);
    }

    const data = await res.json();

    if (data.valid) {
      validationBox.style.color = "#22c55e";
      validationBox.textContent = "Form looks good!";
    } else {
      validationBox.style.color = "#f97316";
      validationBox.textContent = (data.issues || []).join("; ") || "Form has issues.";
    }
  } catch (err) {
    console.error(err);
    validationBox.style.color = "#f97316";
    validationBox.textContent = "Validation failed. Please try again.";
  }
}

window.addEventListener("DOMContentLoaded", () => {
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const validateBtn = document.getElementById("validate-btn");
  const orderForm = document.getElementById("order-form");
  const validationBox = document.getElementById("validation-messages");

  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const message = chatInput.value.trim();
    if (!message) return;
    chatInput.value = "";
    sendChat(message);
  });

  validateBtn.addEventListener("click", () => {
    validateWithAssistant();
  });

  orderForm.addEventListener("submit", (e) => {
    e.preventDefault();
    validateWithAssistant().then(() => {
      if (validationBox.textContent === "Form looks good!") {
        alert("Form submitted! (demo only, not actually sending anywhere.)");
      }
    });
  });
});
