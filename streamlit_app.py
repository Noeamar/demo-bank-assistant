"""Entry point for Streamlit Community Cloud: one process, same architecture.

The simulated core bank and the assistant API start as background servers on localhost,
so the UI talks to them over HTTP exactly as it does locally with ./run.sh.
Secrets (MISTRAL_API_KEY, ACCESS_CODE) come from the app's secrets, exposed as environment variables.
"""

import socket
import threading
import time

import streamlit as st
import uvicorn


def _listening(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@st.cache_resource
def start_backend():
    for app_path, port in [("bankassist.core_mock:app", 8181), ("bankassist.api:app", 8180)]:
        if _listening(port):
            continue
        server = uvicorn.Server(uvicorn.Config(app_path, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(120):                      # the API embeds the knowledge base at start-up
            if _listening(port):
                break
            time.sleep(0.5)
    return True


start_backend()

from bankassist.ui import main  # noqa: E402

main()
