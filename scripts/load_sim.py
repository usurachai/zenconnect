#!/usr/bin/env python3
"""
load_sim.py — Simulate concurrent users chatting with the zenconnect webhook.

Usage:
    python scripts/load_sim.py [OPTIONS]

Options:
    --base-url          Base URL of the service (default: http://localhost:8000)
    --api-key           X-API-KEY header value (default: $CONVERSATIONS_WEBHOOK_SECRET)
    --num-users         Number of concurrent conversations to simulate (default: 5)
    --messages-per-user Number of messages each user sends (default: 3)
    --delay-min         Min seconds between messages per user (default: 0.5)
    --delay-max         Max seconds between messages per user (default: 2.0)
    --timeout           HTTP request timeout in seconds (default: 10)

Exit code 1 if success rate < 100%.
"""

import argparse
import asyncio
import os
import random
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MessageResult:
    message_index: int
    status_code: int | None
    latency_ms: float
    text_sent: str = ""
    response_body: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and self.error is None


@dataclass
class UserResult:
    user_index: int
    conversation_id: str
    results: list[MessageResult] = field(default_factory=list)

    @property
    def sent(self) -> int:
        return len(self.results)

    @property
    def success(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def latencies(self) -> list[float]:
        return [r.latency_ms for r in self.results if r.ok]

    @property
    def errors(self) -> list[str]:
        return [r.error for r in self.results if r.error]


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

def build_payload(conversation_id: str, user_id: str, message_text: str) -> dict[str, object]:
    """Build a valid WebhookPayload matching the pydantic model exactly."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return {
        "app": {"id": "load-test-app"},
        "webhook": {"id": "load-test-webhook", "version": "v2"},
        "events": [
            {
                "id": f"evt_{uuid.uuid4().hex[:24]}",
                "createdAt": now,
                "type": "conversation:message",
                "payload": {
                    "conversation": {
                        "id": conversation_id,
                        "type": "personal",
                        "brandId": "brand_load_test",
                    },
                    "message": {
                        "id": f"msg_{uuid.uuid4().hex[:24]}",
                        "received": now,
                        "author": {
                            "userId": user_id,
                            "displayName": f"Load Test User {user_id[-4:]}",
                            "type": "user",
                        },
                        "content": {"type": "text", "text": message_text},
                        "source": {
                            "type": "line",
                            "integrationId": "int_load_test",
                            "client": {
                                "integrationId": "int_load_test",
                                "type": "line",
                                "externalId": f"ext_{user_id[-8:]}",
                                "id": f"client_{user_id[-8:]}",
                            },
                        },
                    },
                },
            }
        ],
    }


MESSAGES = [
    "สวัสดีครับ ต้องการสอบถามข้อมูล",
    "ช่วยบอกขั้นตอนการสมัครบัตรเครดิตด้วยครับ",
    "ขอบคุณครับ มีคำถามเพิ่มเติมอีกครับ",
    "อยากรู้เรื่องดอกเบี้ยเงินกู้ครับ",
    "สามารถโอนเงินต่างประเทศได้ไหมครับ",
]


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

async def simulate_user(
    client: httpx.AsyncClient,
    user_index: int,
    base_url: str,
    api_key: str,
    messages_per_user: int,
    delay_min: float,
    delay_max: float,
) -> UserResult:
    conversation_id = f"load_test_conv_{uuid.uuid4().hex[:16]}"
    user_id = f"load_test_user_{uuid.uuid4().hex[:8]}"
    result = UserResult(user_index=user_index, conversation_id=conversation_id)

    for i in range(messages_per_user):
        text = MESSAGES[i % len(MESSAGES)]
        payload = build_payload(conversation_id, user_id, text)

        t0 = time.perf_counter()
        status_code = None
        response_body = ""
        error = None
        try:
            response = await client.post(
                f"{base_url}/webhook/conversations",
                json=payload,
                headers={"X-API-KEY": api_key},
            )
            status_code = response.status_code
            response_body = response.text
            if status_code != 200:
                error = f"HTTP {status_code}: {response.text[:100]}"
        except httpx.ConnectError as e:
            error = f"ConnectError: {e}"
        except httpx.TimeoutException as e:
            error = f"Timeout: {e}"
        except Exception as e:
            error = f"Error: {e}"

        latency_ms = (time.perf_counter() - t0) * 1000
        result.results.append(
            MessageResult(
                message_index=i,
                status_code=status_code,
                latency_ms=latency_ms,
                text_sent=text,
                response_body=response_body,
                error=error,
            )
        )

        if i < messages_per_user - 1:
            await asyncio.sleep(random.uniform(delay_min, delay_max))

    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def percentile(data: list[float], p: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]


def print_report(user_results: list[UserResult], wall_time: float) -> bool:
    total_sent = sum(u.sent for u in user_results)
    total_ok = sum(u.success for u in user_results)
    all_latencies = [lat for u in user_results for lat in u.latencies]

    # Per-conversation message trace
    print("\n" + "=" * 70)
    print("CONVERSATION TRACES")
    print("=" * 70)
    for u in user_results:
        print(f"\nUser {u.user_index}  conv: {u.conversation_id}")
        print("-" * 60)
        for r in u.results:
            status = "✓" if r.ok else "✗"
            print(f"  [{status}] sent    : {r.text_sent}")
            print(f"       response: {r.response_body or r.error}  ({r.latency_ms:.1f}ms)")

    # Summary table
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'User':<6} {'Conv ID':<22} {'Sent':>5} {'OK':>5} {'p50ms':>7} {'p95ms':>7}  Errors")
    print("-" * 70)

    for u in user_results:
        p50 = percentile(u.latencies, 50)
        p95 = percentile(u.latencies, 95)
        err_summary = "; ".join(u.errors[:2]) if u.errors else "-"
        print(
            f"{u.user_index:<6} {u.conversation_id[-20:]:<22} "
            f"{u.sent:>5} {u.success:>5} {p50:>7.1f} {p95:>7.1f}  {err_summary}"
        )

    print("=" * 70)
    print(f"Wall time      : {wall_time:.2f}s")
    print(f"Total requests : {total_sent}")
    print(f"Successful     : {total_ok}")
    success_rate = total_ok / total_sent * 100 if total_sent else 0
    print(f"Success rate   : {success_rate:.1f}%")
    if all_latencies:
        print(f"Mean latency   : {statistics.mean(all_latencies):.1f}ms")
        print(f"p50 latency    : {percentile(all_latencies, 50):.1f}ms")
        print(f"p95 latency    : {percentile(all_latencies, 95):.1f}ms")
    print("=" * 70)

    return total_ok == total_sent


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate concurrent users chatting with the zenconnect webhook."
    )
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://localhost:8000"))
    parser.add_argument("--api-key", default=os.getenv("CONVERSATIONS_WEBHOOK_SECRET", ""))
    parser.add_argument("--num-users", type=int, default=int(os.getenv("NUM_USERS", "5")))
    parser.add_argument("--messages-per-user", type=int, default=int(os.getenv("MESSAGES_PER_USER", "3")))
    parser.add_argument("--delay-min", type=float, default=0.5)
    parser.add_argument("--delay-max", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()

    if not args.api_key:
        print("ERROR: --api-key or CONVERSATIONS_WEBHOOK_SECRET env var is required", file=sys.stderr)
        return 1

    print("Starting load simulation:")
    print(f"  Target      : {args.base_url}/webhook/conversations")
    print(f"  Users       : {args.num_users}")
    print(f"  Msgs/user   : {args.messages_per_user}")
    print(f"  Delay range : {args.delay_min}–{args.delay_max}s between messages")
    print()

    t0 = time.perf_counter()

    async with httpx.AsyncClient(timeout=args.timeout) as client:
        tasks = [
            simulate_user(
                client=client,
                user_index=i + 1,
                base_url=args.base_url,
                api_key=args.api_key,
                messages_per_user=args.messages_per_user,
                delay_min=args.delay_min,
                delay_max=args.delay_max,
            )
            for i in range(args.num_users)
        ]
        user_results = await asyncio.gather(*tasks)

    wall_time = time.perf_counter() - t0
    all_ok = print_report(list(user_results), wall_time)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
