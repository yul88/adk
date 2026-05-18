# mySaaS

mySaaS is a multi-agent web application integrating Checkmate and Stash into a cohesive experience.

## Features
- **Unified Interface**: Access both Checkmate and Stash from a single web UI.
- **Session Management**: Robust user sessions and auth.
- **Checklist & Link Management**: UI for managing artifacts created by agents.

## Setup
1.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
2.  Configure environment:
    ```bash
    cp .env.example .env
    # Edit .env with your Google Cloud Project and MongoDB URI
    ```
3.  Run the server:
    ```bash
    python3 server.py
    ```
