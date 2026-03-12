#!/bin/bash

echo "================================================="
echo "   🚀 Production Site Tester (Single Ticket)     "
echo "================================================="

# Load environment variables
if [ -f ../.env ]; then
  set -a
  source ../.env
  set +a
else
  echo "⚠️  No .env file found. Executing with system variables."
fi

# Ask the user for the ticket to test
read -p "Enter the Jira Ticket ID to test against (e.g., PROD-123): " TICKET_ID

if [ -z "$TICKET_ID" ]; then
    echo "❌ No ticket ID provided. Exiting."
    exit 1
fi

echo -e "\nRunning translator script in interactive mode against $TICKET_ID..."
echo -e "You will be prompted before any final revisions are applied.\n"

# Run the python script interactively against the single ticket
PYTHONPATH=.. python3 ../jira_translator.py --ticket_id "$TICKET_ID" --full-ticket

EXIT_CODE=$?

echo -e "\n================================================="
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Test completed successfully."
else
    echo "❌ Test finished with errors. (Exit Code: $EXIT_CODE)"
fi
echo "================================================="
