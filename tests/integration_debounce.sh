#!/bin/bash

# Integration tests for Zendesk webhook using actual curl
# This tests the real service running locally

set -e

# Load environment variables
source .env

API_KEY=${CONVERSATIONS_WEBHOOK_SECRET}
BASE_URL="http://localhost:8000"

echo "=== Integration Test: Debounce Batching ==="
echo "API Key: ${API_KEY:0:10}..."
echo ""

# Test 1: Single conversation with 3 messages
echo "=== Test 1: 3 messages in same conversation ==="
CONV_ID="test_conv_$(date +%s)"

# Message 1 at t=0
echo "Sending message 1 at $(date +%H:%M:%S)"
curl -s -X POST "$BASE_URL/webhook/conversations" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{
    \"app\": {\"id\": \"app_123\"},
    \"webhook\": {\"id\": \"wh_123\", \"version\": \"v2\"},
    \"events\": [{
      \"id\": \"evt_1_$(date +%s)\",
      \"type\": \"conversation:message\",
      \"createdAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
      \"payload\": {
        \"message\": {
          \"id\": \"msg_1_$(date +%s)\",
          \"type\": \"text\",
          \"content\": {\"text\": \"Message 1\"},
          \"author\": {\"type\": \"user\", \"userId\": \"user_123\", \"displayName\": \"Test User\"},
          \"source\": {\"type\": \"line\", \"client\": {\"integrationId\": \"int_123\"}},
          \"received\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"
        },
        \"conversation\": {\"id\": \"$CONV_ID\"}
      }
    }]
  }"

sleep 3

# Message 2 at t=3s
echo "Sending message 2 at $(date +%H:%M:%S)"
curl -s -X POST "$BASE_URL/webhook/conversations" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{
    \"app\": {\"id\": \"app_123\"},
    \"webhook\": {\"id\": \"wh_123\", \"version\": \"v2\"},
    \"events\": [{
      \"id\": \"evt_2_$(date +%s)\",
      \"type\": \"conversation:message\",
      \"createdAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
      \"payload\": {
        \"message\": {
          \"id\": \"msg_2_$(date +%s)\",
          \"type\": \"text\",
          \"content\": {\"text\": \"Message 2\"},
          \"author\": {\"type\": \"user\", \"userId\": \"user_123\", \"displayName\": \"Test User\"},
          \"source\": {\"type\": \"line\", \"client\": {\"integrationId\": \"int_123\"}},
          \"received\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"
        },
        \"conversation\": {\"id\": \"$CONV_ID\"}
      }
    }]
  }"

sleep 3

# Message 3 at t=6s
echo "Sending message 3 at $(date +%H:%M:%S)"
curl -s -X POST "$BASE_URL/webhook/conversations" \
  -H "Content-Type: application/json" \
  -H "x-api-key: $API_KEY" \
  -d "{
    \"app\": {\"id\": \"app_123\"},
    \"webhook\": {\"id\": \"wh_123\", \"version\": \"v2\"},
    \"events\": [{
      \"id\": \"evt_3_$(date +%s)\",
      \"type\": \"conversation:message\",
      \"createdAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
      \"payload\": {
        \"message\": {
          \"id\": \"msg_3_$(date +%s)\",
          \"type\": \"text\",
          \"content\": {\"text\": \"Message 3\"},
          \"author\": {\"type\": \"user\", \"userId\": \"user_123\", \"displayName\": \"Test User\"},
          \"source\": {\"type\": \"line\", \"client\": {\"integrationId\": \"int_123\"}},
          \"received\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"
        },
        \"conversation\": {\"id\": \"$CONV_ID\"}
      }
    }]
  }"

echo ""
echo "All 3 messages sent. Waiting 15 seconds for worker to process..."
sleep 15

echo ""
echo "=== Test 2: Check worker logs ==="
docker compose logs worker --tail=10

echo ""
echo "=== Test Complete ==="
