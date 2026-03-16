#!/bin/bash

# Integration tests for Zendesk webhook using actual curl
# This tests the real service running locally
# 
# Usage:
#   ./tests/integration_debounce.sh          # Test 1: 3 messages in same conversation
#   ./tests/integration_2conversations.sh    # Test 2: 2 different conversations

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
            \"message\": {
              \"id\": \"$msg_id\",
              \"type\": \"text\",
              \"content\": {\"text\": \"$msg_text\"},
              \"author\": {\"type\": \"user\", \"userId\": \"user_123\", \"displayName\": \"Test User\"},
              \"source\": {\"type\": \"line\", \"client\": {\"integrationId\": \"int_123\"}},
              \"received\": \"$timestamp\"
            },
            \"conversation\": {\"id\": \"$conv_id\"}
          }
        }]
      }" > /dev/null
}

echo "=========================================="
echo "Integration Test: Debounce Batching"
echo "=========================================="
echo "API: $BASE_URL"
echo "Debounce: 10 seconds"
echo ""

# Test 1: Single conversation with 3 messages
echo "=== TEST 1: 3 messages in same conversation ==="
CONV_A="test_conv_a_$(date +%s)"

echo "$(date +%H:%M:%S) - Sending message 1 to Conversation A"
send_message "$CONV_A" "Message 1 for Conv A" "1a"

sleep 3

echo "$(date +%H:%M:%S) - Sending message 2 to Conversation A (3s later)"
send_message "$CONV_A" "Message 2 for Conv A" "2a"

sleep 3

echo "$(date +%H:%M:%S) - Sending message 3 to Conversation A (6s later)"
send_message "$CONV_A" "Message 3 for Conv A" "3a"

echo ""
echo "$(date +%H:%M:%S) - All 3 messages sent. Waiting 15s for worker to process..."
echo ""

# Test 2: Start second conversation while first is waiting
sleep 2

echo "$(date +%H:%M:%S) - Starting Conversation B (while A is waiting)"
CONV_B="test_conv_b_$(date +%s)"

echo "$(date +%H:%M:%S) - Sending message 1 to Conversation B"
send_message "$CONV_B" "Message 1 for Conv B" "1b"

sleep 3

echo "$(date +%H:%M:%S) - Sending message 2 to Conversation B (3s later)"
send_message "$CONV_B" "Message 2 for Conv B" "2b"

echo ""
echo "$(date +%H:%M:%S) - Waiting 15s for all processing to complete..."
sleep 15

echo ""
echo "=========================================="
echo "Results - Check Worker Logs"
echo "=========================================="
docker compose logs worker --tail=30

echo ""
echo "=========================================="
echo "Test Complete"
echo "=========================================="
echo ""
echo "Expected behavior:"
echo "- Conv A: 3 messages within 6s → 1 reply with all 3 combined"
echo "- Conv B: 2 messages within 3s → 1 reply with both combined"
echo ""
