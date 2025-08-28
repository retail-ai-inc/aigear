"""
Test script for AI Gear PubSub client.
Run this to verify all functionalities work with your Google Cloud project.
"""

import json
import time
import sys
from datetime import datetime
from pubsub import PubSubClient

def test_pubsub_client(project_id: str):
    """
    Test all PubSub functionalities.
    
    Args:
        project_id: Your GCP project ID
    """
    print(f"\n🚀 Testing PubSub Client for project: {project_id}\n")
    
    # Initialize client
    client = PubSubClient(project_id)
    
    # Test names with timestamp to avoid conflicts
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    test_topic = f"aigear-test-topic-{timestamp}"
    test_subscription = f"aigear-test-sub-{timestamp}"
    test_push_subscription = f"aigear-test-push-sub-{timestamp}"
    test_schema = f"aigear-test-schema-{timestamp}"
    
    results = {}
    
    # ========== TEST 1: Create Topic ==========
    print("📝 Test 1: Creating topic...")
    try:
        topic_path = client.create_topic(
            topic_name=test_topic,
            labels={"purpose": "aigear-test", "timestamp": timestamp}
        )
        print(f"✅ Topic created: {topic_path}")
        results["create_topic"] = "PASSED"
    except Exception as e:
        print(f"❌ Failed to create topic: {e}")
        results["create_topic"] = f"FAILED: {e}"
    input("✅ Test succeeded. Press Enter to continue to the next test...")
    # ========== TEST 2: List Topics ==========
    print("\n📝 Test 2: Listing topics...")
    try:
        topics = client.list_topics()
        matching_topics = [t for t in topics if test_topic in t]
        if matching_topics:
            print(f"✅ Found test topic in list: {matching_topics[0]}")
            results["list_topics"] = "PASSED"
        else:
            print("❌ Test topic not found in list")
            results["list_topics"] = "FAILED: Topic not in list"
    except Exception as e:
        print(f"❌ Failed to list topics: {e}")
        results["list_topics"] = f"FAILED: {e}"
    input("✅ Test succeeded. Press Enter to continue to the next test...")
    # ========== TEST 3: Publish Simple Message ==========
    print("\n📝 Test 3: Publishing simple message...")
    try:
        message_id = client.publish(
            topic_name=test_topic,
            message_data={
                "test": "Hello from AI Gear!",
                "timestamp": timestamp,
                "model": "test-model-v1"
            },
            attributes={
                "source": "aigear-test",  # Remove str() wrapper - just use string directly
                "version": "1.0"          # Remove str() wrapper - just use string directly
            }
        )
        print(f"✅ Message published with ID: {message_id}")
        results["publish_message"] = "PASSED"
    except Exception as e:
        print(f"❌ Failed to publish message: {e}")
        results["publish_message"] = f"FAILED: {e}"
    input("✅ Test succeeded. Press Enter to continue to the next test...")

    # ========== TEST 4: Create Pull Subscription ==========
    print("\n📝 Test 4: Creating pull subscription...")
    try:
        sub_path = client.create_pull_subscription(
            topic_name=test_topic,
            subscription_name=test_subscription,
            ack_deadline_seconds=30,
            enable_exactly_once_delivery=True,
            labels={"type": "pull", "test": "aigear"}
        )
        print(f"✅ Pull subscription created: {sub_path}")
        results["create_pull_subscription"] = "PASSED"
    except Exception as e:
        print(f"❌ Failed to create pull subscription: {e}")
        results["create_pull_subscription"] = f"FAILED: {e}"
    input("✅ Test succeeded. Press Enter to continue to the next test...")

    # ========== TEST 5: Publish Batch Messages ==========
    print("\n📝 Test 5: Publishing batch messages...")
    try:
        batch_messages = [
            {"id": i, "data": f"Batch message {i}", "timestamp": timestamp}
            for i in range(5)
        ]
        message_ids = client.publish_batch(
            topic_name=test_topic,
            messages=batch_messages
        )
        print(f"✅ Published {len(message_ids)} messages in batch")
        results["publish_batch"] = "PASSED"
    except Exception as e:
        print(f"❌ Failed to publish batch: {e}")
        results["publish_batch"] = f"FAILED: {e}"
    input("✅ Test succeeded. Press Enter to continue to the next test...")

    # ========== TEST 6: Pull Messages ==========
    print("\n📝 Test 6: Pulling messages...")
    # Wait a bit for messages to be available
    time.sleep(2)
    try:
        messages = client.pull_messages(
            subscription_name=test_subscription,
            max_messages=10
        )
        print(f"✅ Pulled {len(messages)} messages")
        if messages:
            print(f"   Sample message: {messages[0]['data']}")
        results["pull_messages"] = "PASSED"
    except Exception as e:
        print(f"❌ Failed to pull messages: {e}")
        results["pull_messages"] = f"FAILED: {e}"
    input("✅ Test succeeded. Press Enter to continue to the next test...")

    # ========== TEST 7: Create Avro Schema ==========
    print("\n📝 Test 7: Creating Avro schema...")
    avro_schema = {
        "type": "record",
        "name": "AIGearModel",
        "fields": [
            {"name": "model_id", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "accuracy", "type": "float"},
            {"name": "timestamp", "type": "long"}
        ]
    }
    try:
        schema_path = client.create_avro_schema(
            schema_name=test_schema,
            avro_definition=json.dumps(avro_schema)
        )
        print(f"✅ Avro schema created: {schema_path}")
        results["create_avro_schema"] = "PASSED"
    except Exception as e:
        print(f"❌ Failed to create Avro schema: {e}")
        results["create_avro_schema"] = f"FAILED: {e}"
    input("✅ Test succeeded. Press Enter to continue to the next test...")

    # ========== TEST 8: Create Topic with Schema ==========
    print("\n📝 Test 8: Creating topic with schema...")
    schema_topic = f"{test_topic}-with-schema"
    try:
        topic_path = client.create_topic(
            topic_name=schema_topic,
            schema_name=test_schema,
            message_encoding="JSON"
        )
        print(f"✅ Topic with schema created: {topic_path}")
        results["create_topic_with_schema"] = "PASSED"
    except Exception as e:
        print(f"❌ Failed to create topic with schema: {e}")
        results["create_topic_with_schema"] = f"FAILED: {e}"
    input("✅ Test succeeded. Press Enter to continue to the next test...")

    # ========== TEST 9: Create Push Subscription ==========
    print("\n📝 Test 9: Creating push subscription with payload unwrapping...")
    try:
        # Using a dummy endpoint - replace with your actual endpoint
        push_path = client.create_push_subscription(
            topic_name=test_topic,
            subscription_name=test_push_subscription,
            push_endpoint="https://example.com/webhook",  # Replace with real endpoint
            enable_payload_unwrapping=True,
            write_metadata=True,
            ack_deadline_seconds=60  # Use seconds instead of any duration string
        )
        print(f"✅ Push subscription created: {push_path}")
        results["create_push_subscription"] = "PASSED"
    except Exception as e:
        error_msg = str(e)
        if "Duration must end with letter" in error_msg:
            print(f"❌ Duration format error in PubSubClient implementation: {e}")
            print("   💡 Check your PubSubClient code - ensure all durations use 's' suffix (e.g., '86400s' not '1d')")
        else:
            print(f"❌ Failed to create push subscription: {e}")
        results["create_push_subscription"] = f"FAILED: {e}"
    input("✅ Test succeeded. Press Enter to continue to the next test...")

    # ========== TEST 10: Create Subscription with Dead Letter ==========
    print("\n📝 Test 10: Creating subscription with dead letter policy...")
    dlq_topic = f"{test_topic}-dlq"
    dlq_subscription = f"{test_subscription}-with-dlq"
    try:
        # First create DLQ topic
        client.create_topic(dlq_topic)
        
        # Create subscription with DLQ
        sub_path = client.create_pull_subscription(
            topic_name=test_topic,
            subscription_name=dlq_subscription,
            dead_letter_policy={
                "dead_letter_topic": dlq_topic,
                "max_delivery_attempts": 5
            }
        )
        print(f"✅ Subscription with DLQ created: {sub_path}")
        results["create_subscription_with_dlq"] = "PASSED"
    except Exception as e:
        print(f"❌ Failed to create subscription with DLQ: {e}")
        results["create_subscription_with_dlq"] = f"FAILED: {e}"
    input("✅ Test succeeded. Press Enter to continue to the next test...")

    # ========== TEST 11: Get Topic Info ==========
    print("\n📝 Test 11: Getting topic info...")
    try:
        topic_info = client.get_topic_info(test_topic)
        print(f"✅ Got topic info: {topic_info['name']}")
        print(f"   Labels: {topic_info['labels']}")
        results["get_topic_info"] = "PASSED"
    except Exception as e:
        print(f"❌ Failed to get topic info: {e}")
        results["get_topic_info"] = f"FAILED: {e}"
    input("✅ Test succeeded. Press Enter to continue to the next test...")

    # ========== TEST 12: Get Subscription Info ==========
    print("\n📝 Test 12: Getting subscription info...")
    try:
        sub_info = client.get_subscription_info(test_subscription)
        print(f"✅ Got subscription info: {sub_info['name']}")
        print(f"   Exactly once delivery: {sub_info['enable_exactly_once_delivery']}")
        results["get_subscription_info"] = "PASSED"
    except Exception as e:
        print(f"❌ Failed to get subscription info: {e}")
        results["get_subscription_info"] = f"FAILED: {e}"
    input("✅ Test succeeded. Press Enter to continue to the next test...")
    
    # ========== CLEANUP ==========
    print("\n🧹 Cleaning up test resources...")
    cleanup_successful = True
    time.sleep(2)
    
    # Delete subscriptions
    for sub in [test_subscription, test_push_subscription, dlq_subscription]:
        try:
            client.delete_subscription(sub)
            print(f"   ✅ Deleted subscription: {sub}")
        except Exception as e:
            if "NOT_FOUND" not in str(e):
                print(f"   ⚠️  Failed to delete subscription {sub}: {e}")
                cleanup_successful = False
    
    # Delete topics
    for topic in [test_topic, schema_topic, dlq_topic]:
        try:
            client.delete_topic(topic)
            print(f"   ✅ Deleted topic: {topic}")
        except Exception as e:
            if "NOT_FOUND" not in str(e):
                print(f"   ⚠️  Failed to delete topic {topic}: {e}")
                cleanup_successful = False
    
    # Delete schema
    try:
        client.delete_schema(test_schema)
        print(f"   ✅ Deleted schema: {test_schema}")
    except Exception as e:
        if "NOT_FOUND" not in str(e):
            print(f"   ⚠️  Failed to delete schema {test_schema}: {e}")
            cleanup_successful = False
    
    results["cleanup"] = "PASSED" if cleanup_successful else "PARTIAL"
    
    # ========== SUMMARY ==========
    print("\n" + "="*50)
    print("📊 TEST SUMMARY")
    print("="*50)
    
    passed = sum(1 for r in results.values() if r == "PASSED")
    failed = len(results) - passed
    
    for test, result in results.items():
        status = "✅" if result == "PASSED" else "❌"
        print(f"{status} {test}: {result}")
    
    print(f"\nTotal: {passed} passed, {failed} failed out of {len(results)} tests")
    
    return results


def test_async_subscription(client: PubSubClient, topic_name: str, subscription_name: str):
    """
    Test asynchronous subscription with callback.
    """
    print("\n📝 Bonus Test: Async subscription with callback...")
    # Create subscription if it doesn't exist
    try:
        client.create_pull_subscription(
            topic_name=topic_name,
            subscription_name=subscription_name
        )
    except:
        pass  # Subscription might already exist
    
    # Publish test messages
    for i in range(3):
        client.publish(
            topic_name=topic_name,
            message_data={
                "test": f"Async message {i}",
                "timestamp": datetime.now().isoformat()
            }
        )
    
    received_messages = []
    
    def message_handler(message):
        print(f"   📨 Received: {message['data']}")
        received_messages.append(message)
    
    def error_handler(error):
        print(f"   ❌ Error: {error}")
    
    # Subscribe for 5 seconds
    print("   Listening for 5 seconds...")
    client.subscribe_with_callback(
        subscription_name=subscription_name,
        callback=message_handler,
        error_handler=error_handler,
        timeout=5
    )
    
    print(f"   ✅ Received {len(received_messages)} messages asynchronously")


if __name__ == "__main__":
    # Get project ID from command line or environment
    
    if len(sys.argv) > 1:
        project_id = sys.argv[1]
    else:
        import os
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project_id:
            print("❌ Please provide project ID as argument or set GOOGLE_CLOUD_PROJECT environment variable")
            print("Usage: python test_pubsub.py YOUR_PROJECT_ID")
            sys.exit(1)
    
    # Run main tests
    results = test_pubsub_client(project_id)
    
    # Run async test if main tests passed
    if results.get("create_topic") == "PASSED":
        # Create new timestamp and topic name for async test
        async_timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        async_test_topic = f"aigear-test-topic-{async_timestamp}"
        async_test_subscription = f"{async_test_topic}-async-sub"
        
        # Create client and topic for async test
        async_client = PubSubClient(project_id)
        try:
            async_client.create_topic(async_test_topic)
            test_async_subscription(
                async_client,
                async_test_topic,
                async_test_subscription
            )
            
            # Cleanup async test resources
            try:
                async_client.delete_subscription(async_test_subscription)
                async_client.delete_topic(async_test_topic)
                print("✅ Cleaned up async test resources")
            except Exception as e:
                print(f"⚠️  Failed to cleanup async test resources: {e}")
        except Exception as e:
            print(f"❌ Failed to run async test: {e}")