#!/bin/bash

# Integration tests for debounce batching
# This tests the real service running locally
#
# Usage:
#   ./tests/integration_debounce.sh          # Test: 3 messages in same conversation
#   ./tests/integration_2conversations.sh  # Test: 2 different conversations

set -e

# Load environment variables
source .env

API_KEY=${CONVERSATIONS_WEBHOOK_SECRET}
BASE_URL="http://localhost:8000"

# Function to send a webhook message
send_message() {
    local conv_id=$1
    local msg_text=$2
    local msg_num=$3
    
    local evt_id="evt_${msg_num}_$(date +%s%3N)"
    local msg_id="msg_${msg_num}_$(date +%s%3N)"
    local timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    local int_id="int_$(date +%s)"
    local client_id="client_$(date +%s)"
    
    curl -s -X POST "$BASE_URL/webhook/conversations" \
      -H "Content-Type: application/json" \
      -H "x-api-key: $API_KEY" \
      -d "{
        \"app\": {\"id\": \"app_123\"},
        \"webhook\": {\"id\": \"wh_123\", \"version\": \"v2\"},
        \"events\": [{
          \"id\": \"$evt_id\",
          \"type\": \"conversation:message\",
          \"createdAt\": \"$timestamp\",
          \"payload\": {
            \"conversation\": {
              \"id\": \"$conv_id\",
              \"type\": \"conversation\",
              \"brandId\": \"brand_123\"
            },
            \"message\": {
              \"id\": \"$msg_id\",
              \"received\": \"$timestamp\",
              \"author\": {
                \"userId\": \"user_123\",
                \"displayName\": \"Test User\",
                \"type\": \"user\"
              },
              \"content\": {
                \"type\": \"text\",
                \"text\": \"$msg_text\"
              },
              \"source\": {
                \"type\": \"line\",
                \"integrationId\": \"$int_id\",
                \"client\": {
                  \"integrationId\": \"$int_id\",
                  \"type\": \"line\",
                  \"externalId\": \"external_123\",
                  \"id\": \"$client_id\"
                }
              }
            }
          }
        }]
      }"
}

echo "=========================================="
echo "Integration Test: Debounce Batching"
echo "=========================================="
echo "API: $BASE_URL"
echo "Debounce: 10 seconds"
echo ""

# Test: Single conversation with 3 messages
echo "=== TEST: 3 messages in same conversation ==="
CONV_ID="test_conv_$(date +%s)"

echo "$(date +%H:%M:%S) - Sending message 1"
send_message "$CONV_ID" "Message 1 from integration test" "1"

sleep 3

echo "$(date +%H:%M:%S) - Sending message 2 (3s later)"
send_message "$CONV_ID" "Message 2 from integration test" "2"

sleep 3

echo "$(date +%H:%M:%S) - Sending message 3 (6s later)"
send_message "$CONV_ID" "Message 3 from integration test" "3"

echo ""
echo "$(date +%H:%M:%S) - All 3 messages sent. Waiting 20s for worker to process..."
echo ""

sleep 20

echo "=========================================="
echo "Worker Logs"
echo "=========================================="
docker compose logs worker --tail=30

echo ""
echo "=========================================="
echo "Expected (verify in logs):"
echo "- Worker waits for debounce (remaining_seconds)"
echo "- 3 messages combined into 1 flush: 'Message 1...\\nMessage 2...\\nMessage 3...'"
echo "- Dynamic debounce: flush ~10s after last message (at t0+16s)"
echo "- Note: 404 error is expected (fake conversation ID)"
echo "=========================================="
