# Personal Assistant Web App

A modern, intelligent personal assistant web application powered by **FastAPI**, **Tailwind CSS**, and **Google ADK (Agent Development Kit)**. Orcherstrates two specialized agents:
- **Stash**: For saving and retrieving knowledge (links, notes).
- **Checkmate**: For managing actionable checklists.

## Features

- **Modern UI**: Built with Tailwind CSS, featuring glassmorphism, dark mode, and responsive design.
- **Secure Authentication**: User signup/login with persistent sessions (MongoDB).
- **Auto-Login**: Seamlessly signs you into the Stash agent using your app credentials.
- **Privacy**: Chat history is strictly isolated and cleared on logout.
- **Dual-Agent Orchestration**: effectively routes requests to Stash or Checkmate based on context.

## Prerequisites

- Python 3.9+
- MongoDB (or compatible service)
- Google Cloud Project with Vertex AI enabled

## Setup

1.  **Clone the repository**
2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
3.  **Configure Environment**:
    Copy `env.example` to `.env` and fill in your values:
    ```bash
    cp env.example .env
    ```
    - `MONGO_URI`: Your MongoDB connection string.
    - `STASH_RESOURCE_ID`: Vertex AI Agent ID for Stash.
    - `CHECKMATE_RESOURCE_ID`: Vertex AI Agent ID for Checkmate.

## Running

Start the server:
```bash
python -m personalAssistant.server
```

Open `http://localhost:8000` in your browser.

## Usage

- **Sign Up/Login**: Create an account to access the assistant.
- **Chat**:
    - "Save this link: https://example.com" (Routed to Stash)
    - "Create a checklist for a road trip" (Routed to Checkmate)
    - "Logoff" or "Sign out" to exit.
