#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")"

# Load environment variables if the .env file exists
if [ -f .env ]; then
  set -a
  source .env
  set +a
else
  echo "⚠️  No .env file found. Proceeding with system environment variables."
fi

# Set defaults if not provided in .env
JIRA_DOMAIN=${JIRA_DOMAIN:-"example.atlassian.net"}
OLLAMA_HOST=${OLLAMA_HOST:-"http://localhost:11434"}
OLLAMA_MODEL=${OLLAMA_MODEL:-"qwen2.5:7b"}

echo "======================================"
echo "    🧪 Translation Service Tester     "
echo "======================================"

# --- 1. Test Jira Reachability & Auth ---
echo -e "\n[1] Testing Jira API ($JIRA_DOMAIN)..."

if [ -z "$JIRA_EMAIL" ] || [ -z "$JIRA_API_TOKEN" ]; then
    echo "❌ JIRA_EMAIL or JIRA_API_TOKEN is not set. Skipping Jira authentication test."
else
    # We test the basic /myself endpoint
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -u "${JIRA_EMAIL}:${JIRA_API_TOKEN}" -H "Accept: application/json" "https://${JIRA_DOMAIN}/rest/api/3/myself")
    
    if [ "$HTTP_STATUS" -eq 200 ]; then
        echo "✅ Jira Authentication Successful!"
    elif [ "$HTTP_STATUS" -eq 401 ] || [ "$HTTP_STATUS" -eq 403 ]; then
        echo "❌ Jira Authentication Failed! (HTTP $HTTP_STATUS) - Please check your JIRA_EMAIL and JIRA_API_TOKEN."
    else
        echo "❌ Jira Connection Failed! (HTTP $HTTP_STATUS) - Could not reach https://${JIRA_DOMAIN}."
    fi
fi

# --- 2. Test LLM Reachability ---
echo -e "\n[2] Testing LLM Provider..."

if [ -n "$GEMINI_API_KEY" ]; then
    echo "ℹ️  GEMINI_API_KEY is set. Testing Gemini connection..."
    # Quick test to Gemini's generateContent endpoint
    GEMINI_RES=$(curl -s -o /dev/null -w "%{http_code}" -H "Content-Type: application/json" \
         -d '{"contents":[{"parts":[{"text":"Say hello"}]}]}' \
         "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key=${GEMINI_API_KEY}")
         
    if [ "$GEMINI_RES" -eq 200 ]; then
        echo "✅ Gemini API Connection Successful!"
    else
        echo "❌ Gemini API Connection Failed! (HTTP $GEMINI_RES)"
    fi
else
    echo "ℹ️  No GEMINI_API_KEY found. Testing local Ollama connection..."
    echo "    Host: $OLLAMA_HOST"
    echo "    Model: $OLLAMA_MODEL"
    python3 utils/test_llm.py --host "$OLLAMA_HOST" --model "$OLLAMA_MODEL"
fi

echo -e "\n======================================"
echo "          Tests Completed             "
echo "======================================"
