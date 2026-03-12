# Jira Translator Scraper Bot

This service automatically monitors specified Jira projects and translates comments, descriptions, and custom fields to break down language barriers across global teams. 

It provides high-quality translation by prioritizing local AI models (via Ollama) and gracefully falling back to a lightweight legacy server. It also supports seamless cloud translation via Google Gemini.

---

## ⚡ Quick Start

### 1. Requirements
- Python 3.10+
- An Atlassian account with **Administrator** role access (to edit comments made by others).

### 2. Installation
Clone the repository and install the dependencies:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. General Configuration
Copy the sample environment file and add your credentials:
```bash
cp .env.example .env
```
Open `.env` and fill in:
- `JIRA_DOMAIN=` (e.g. `example.atlassian.net`)
- `JIRA_EMAIL=`
- `JIRA_API_TOKEN=`
- `JIRA_PROJECTS=` (e.g. `PROJECT1,PROJECT2`)

*(Optional)* To translate custom fields, configure `config.json`:
```bash
cp config.example.json config.json
```
Edit `config.json` to map any specific Custom Field IDs you want to translate (e.g. "Status Notes" -> "customfield_12369").

### 4. Running the Script
Run the script to automatically search and translate recently updated tickets:
```bash
./run_translator.sh
```

---

## 🧠 Translation Modes

The script supports three distinct translation execution modes.

### 1. Hybrid Mode (Default & Recommended)
**Best for**: High-quality translations with local fallback reliability.
Attempts to translate via Ollama. If the Ollama server is unreachable, it automatically falls back to the Legacy Local Server.
```bash
# No extra flags needed
python3 jira_translator.py --projects PROJECT1 --no-confirm
```
*Requires `OLLAMA_HOST` in `.env` to point to your Ollama runtime.*

### 2. Cloud (Gemini)
**Best for**: When no local infrastructure or GPU is available.
Uses Google's Gemini API for lightning-fast cloud translations.
```bash
python3 jira_translator.py --projects PROJECT1 --no-confirm --use-gemini
```
*Requires `GEMINI_API_KEY` in `.env`.*

### 3. Legacy Local Only
**Best for**: Server maintenance or constrained environments.
Skips Ollama entirely and strictly falls back to the local `Helsinki-NLP` translation server on port `5000`.
```bash
python3 jira_translator.py --projects PROJECT1 --no-confirm --use-remote-only
```
*Make sure to stand up the docker container first via `cd server && docker-compose up -d`.*

---

## 🛠️ Advanced Setup (Systemd Deployment)

Running this via systemd timers is the recommended way to keep your Jira issues translated 24/7.

1. **Update the execution script:**
   Ensure `run_translator.sh` specifies the exact projects you want to monitor.
2. **Make it Executable:**
   ```bash
   chmod +x run_translator.sh
   ```
3. **Configure Systemd:**
   Create the systemd definition files located in `~/.config/systemd/user/`:
   
   **`jira-translator.service`**
   ```ini
   [Unit]
   Description=Jira Translator Script

   [Service]
   Type=oneshot
   ExecStart=/absolute/path/to/run_translator.sh
   StandardOutput=journal
   StandardError=journal
   SyslogIdentifier=jira-translator
   ```

   **`jira-translator.timer`**
   ```ini
   [Unit]
   Description=Run Jira Translator script periodically

   [Timer]
   OnCalendar=*:0/15
   Persistent=true

   [Install]
   WantedBy=timers.target
   ```
4. **Enable the Service:**
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now jira-translator.timer
   ```

---

## 🔍 Diagnostics & Testing

If you encounter issues, you can run diagnostic tools from the `utils/` directory:

**Test LLM Connection:**
```bash
python3 utils/test_llm.py --host http://localhost:11434 --model qwen2.5:7b
```

**Test End-to-End Environment:**
```bash
./utils/test_setup.sh
```

**Interactive Production Test (Single Ticket):**
```bash
./utils/test_production.sh
```
