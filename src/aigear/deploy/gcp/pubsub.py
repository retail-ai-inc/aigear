import json
import logging
import weakref
from typing import Dict, Any, Optional, List, Callable, Union
from concurrent.futures import TimeoutError
from google.cloud import pubsub_v1
from google.api_core.retry import Retry
from google.api_core.exceptions import GoogleAPIError
from google.pubsub_v1.types import PubsubMessage, Schema
from google.protobuf.message import Message as ProtoMessage


class PubSubClient:
    """
    Google Cloud Pub/Sub client for AI Gear.
    Handles publishing and subscribing to messages with full feature support.
    """

    def __init__(self, project_id: str):
        """
        Initialize Pub/Sub client.

        Args:
            project_id: GCP project ID from config
        """
        self.project_id = project_id
        self.publisher = pubsub_v1.PublisherClient(
            publisher_options=pubsub_v1.types.PublisherOptions(
                enable_message_ordering=True
            )
        )
        self.subscriber = pubsub_v1.SubscriberClient()
        self.schema_client = pubsub_v1.SchemaServiceClient()

    def close(self):
        """
        Close Pub/Sub connections during cleanup.
        """
        if hasattr(self, "publisher") and hasattr(self.publisher, "transport"):
            try:
                self.publisher.transport.close()
            except Exception:
                pass

        if hasattr(self, "subscriber") and hasattr(self.subscriber, "transport"):
            try:
                self.subscriber.transport.close()
            except Exception:
                pass

        if hasattr(self, "schema_client") and hasattr(self.schema_client, "transport"):
            try:
                self.schema_client.transport.close()
            except Exception:
                pass


    # ==================== TOPIC MANAGEMENT ====================

    def create_topic(
        self, 
        topic_name: str,
        schema_name: Optional[str] = None,
        message_encoding: Optional[str] = None,
        retention_duration: Optional[str] = None,
        labels: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Create a Pub/Sub topic with optional schema.

        Args:
            topic_name: Name of the topic
            schema_name: Optional schema (e.g., "my-proto-schema")
            message_encoding: Encoding type ("JSON" or "BINARY") if schema is used
            retention_duration: How long to retain messages (e.g., "604800s" for 7 days)
            labels: Optional labels for the topic

        Returns:
            Full topic path
        """
        topic_path = self.publisher.topic_path(self.project_id, topic_name)
        
        request = {"name": topic_path}
        
        # Add schema settings if provided
        if schema_name:
            schema_path = self.schema_client.schema_path(self.project_id, schema_name)
            request["schema_settings"] = {
                "schema": schema_path,
                "encoding": message_encoding or "JSON"
            }
        
        # Add retention settings
        if retention_duration:
            request["message_retention_duration"] = retention_duration
        
        # Add labels
        if labels:
            request["labels"] = labels

        try:
            topic = self.publisher.create_topic(request=request)
            logging.info(f"Created topic: {topic.name}")
            return topic.name
        except Exception as e:
            if "already exists" in str(e):
                logging.info(f"Topic already exists: {topic_path}")
                return topic_path
            else:
                logging.error(f"Error creating topic: {e}")
                raise

    def update_topic_schema(
        self,
        topic_name: str,
        schema_name: str,
        message_encoding: str = "JSON"
    ):
        """
        Edit a topic to associate a schema.

        Args:
            topic_name: Topic to update
            schema_name: Schema to associate
            message_encoding: "JSON" or "BINARY"
        """
        topic_path = self.publisher.topic_path(self.project_id, topic_name)
        schema_path = self.schema_client.schema_path(self.project_id, schema_name)
        
        topic = self.publisher.get_topic(request={"topic": topic_path})
        topic.schema_settings.schema = schema_path
        topic.schema_settings.encoding = message_encoding
        
        update_mask = {"paths": ["schema_settings"]}
        
        updated_topic = self.publisher.update_topic(
            request={"topic": topic, "update_mask": update_mask}
        )
        logging.info(f"Updated topic schema: {updated_topic.name}")

    def delete_topic(self, topic_name: str):
        """
        Delete a topic.

        Args:
            topic_name: Topic to delete
        """
        topic_path = self.publisher.topic_path(self.project_id, topic_name)
        
        try:
            self.publisher.delete_topic(request={"topic": topic_path})
            logging.info(f"Deleted topic: {topic_path}")
        except Exception as e:
            logging.error(f"Error deleting topic: {e}")
            raise

    # ==================== SCHEMA MANAGEMENT ====================

    def create_proto_schema(
        self,
        schema_name: str,
        proto_definition: str,
        revision_id: Optional[str] = None
    ) -> str:
        """
        Create a Protocol Buffer schema.

        Args:
            schema_name: Name for the schema
            proto_definition: Proto file content as string
            revision_id: Optional revision ID

        Returns:
            Schema resource name
        """
        parent = f"projects/{self.project_id}"
        schema = {
            "name": f"{parent}/schemas/{schema_name}",
            "type_": "PROTOCOL_BUFFER",
            "definition": proto_definition
        }
        
        if revision_id:
            schema["revision_id"] = revision_id
        
        try:
            created_schema = self.schema_client.create_schema(
                request={
                    "parent": parent,
                    "schema": schema,
                    "schema_id": schema_name
                }
            )
            logging.info(f"Created schema: {created_schema.name}")
            return created_schema.name
        except Exception as e:
            logging.error(f"Error creating schema: {e}")
            raise

    def create_avro_schema(
        self,
        schema_name: str,
        avro_definition: str,
        revision_id: Optional[str] = None
    ) -> str:
        """
        Create an Avro schema.

        Args:
            schema_name: Name for the schema
            avro_definition: Avro schema as JSON string
            revision_id: Optional revision ID

        Returns:
            Schema resource name
        """
        parent = f"projects/{self.project_id}"
        schema = {
            "name": f"{parent}/schemas/{schema_name}",
            "type_": "AVRO",
            "definition": avro_definition
        }
        
        if revision_id:
            schema["revision_id"] = revision_id
        
        try:
            created_schema = self.schema_client.create_schema(
                request={
                    "parent": parent,
                    "schema": schema,
                    "schema_id": schema_name
                }
            )
            logging.info(f"Created schema: {created_schema.name}")
            return created_schema.name
        except Exception as e:
            logging.error(f"Error creating schema: {e}")
            raise

    def delete_schema(self, schema_name: str):
        """
        Delete a schema.

        Args:
            schema_name: Schema to delete
        """
        schema_path = self.schema_client.schema_path(self.project_id, schema_name)
        
        try:
            self.schema_client.delete_schema(request={"name": schema_path})
            logging.info(f"Deleted schema: {schema_path}")
        except Exception as e:
            logging.error(f"Error deleting schema: {e}")
            raise

    def rollback_schema(self, schema_name: str, revision_id: str):
        """
        Rollback to a specific schema revision.

        Args:
            schema_name: Schema to rollback
            revision_id: Revision to rollback to
        """
        schema_path = self.schema_client.schema_path(self.project_id, schema_name)
        
        try:
            schema = self.schema_client.rollback_schema(
                request={
                    "name": schema_path,
                    "revision_id": revision_id
                }
            )
            logging.info(f"Rolled back schema to revision: {revision_id}")
            return schema
        except Exception as e:
            logging.error(f"Error rolling back schema: {e}")
            raise

    # ==================== SUBSCRIPTION MANAGEMENT ====================

    def create_pull_subscription(
        self, 
        topic_name: str, 
        subscription_name: str,
        ack_deadline_seconds: int = 600,
        message_retention_duration: Optional[str] = None,
        enable_exactly_once_delivery: bool = False,
        enable_message_ordering: bool = False,
        retry_policy: Optional[Dict[str, Any]] = None,
        dead_letter_policy: Optional[Dict[str, Any]] = None,
        filter: Optional[str] = None,
        labels: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Create a PULL subscription with full options.

        Args:
            topic_name: Topic to subscribe to
            subscription_name: Name for the subscription
            ack_deadline_seconds: Time to acknowledge
            message_retention_duration: How long to keep messages
            enable_exactly_once_delivery: Enable exactly-once semantics
            enable_message_ordering: Enable message ordering
            retry_policy: Retry configuration
            dead_letter_policy: Dead letter configuration
                Example: {"dead_letter_topic": "topic-name", "max_delivery_attempts": 5}
            filter: Message filter expression
            labels: Optional labels

        Returns:
            Full subscription path
        """
        topic_path = self.publisher.topic_path(self.project_id, topic_name)
        subscription_path = self.subscriber.subscription_path(
            self.project_id, subscription_name
        )

        request = {
            "name": subscription_path,
            "topic": topic_path,
            "ack_deadline_seconds": ack_deadline_seconds,
        }

        # Add optional features
        if enable_exactly_once_delivery:
            request["enable_exactly_once_delivery"] = True
        
        if enable_message_ordering:
            request["enable_message_ordering"] = True
        
        if message_retention_duration:
            request["message_retention_duration"] = message_retention_duration
        
        if retry_policy:
            request["retry_policy"] = retry_policy
        else:
            request["retry_policy"] = {
                "minimum_backoff": "10s",
                "maximum_backoff": "600s"
            }
        
        # Add dead letter policy
        if dead_letter_policy:
            dlq_topic_path = self.publisher.topic_path(
                self.project_id, 
                dead_letter_policy["dead_letter_topic"]
            )
            request["dead_letter_policy"] = {
                "dead_letter_topic": dlq_topic_path,
                "max_delivery_attempts": dead_letter_policy.get("max_delivery_attempts", 5)
            }
        
        if filter:
            request["filter"] = filter
        
        if labels:
            request["labels"] = labels

        try:
            subscription = self.subscriber.create_subscription(request=request)
            logging.info(f"Created pull subscription: {subscription.name}")
            return subscription.name
        except Exception as e:
            if "already exists" in str(e):
                logging.info(f"Subscription already exists: {subscription_path}")
                return subscription_path
            else:
                logging.error(f"Error creating subscription: {e}")
                raise

    def create_push_subscription(
        self,
        topic_name: str,
        subscription_name: str,
        push_endpoint: str,
        service_account_email: Optional[str] = None,
        audience: Optional[str] = None,
        enable_payload_unwrapping: bool = False,
        write_metadata: bool = False,
        ack_deadline_seconds: int = 600,
        message_retention_duration: Optional[str] = "86400s",
        enable_exactly_once_delivery: bool = False,
        retry_policy: Optional[Dict[str, Any]] = None,
        dead_letter_policy: Optional[Dict[str, Any]] = None,
        filter: Optional[str] = None
    ) -> str:
        """
        Create a PUSH subscription with payload unwrapping support.

        Args:
            topic_name: Topic to subscribe to
            subscription_name: Name for the subscription
            push_endpoint: URL where messages will be sent
            service_account_email: Service account for authentication
            audience: JWT audience for authentication
            enable_payload_unwrapping: Enable payload unwrapping
            write_metadata: Write Pub/Sub metadata to HTTP headers
            ack_deadline_seconds: Time to acknowledge
            message_retention_duration: How long to keep messages
            enable_exactly_once_delivery: Enable exactly-once semantics
            retry_policy: Custom retry policy
            dead_letter_policy: Dead letter configuration
            filter: Message filter expression

        Returns:
            Full subscription path
        """
        topic_path = self.publisher.topic_path(self.project_id, topic_name)
        subscription_path = self.subscriber.subscription_path(
            self.project_id, subscription_name
        )

        # Build push config
        push_config = {
            "push_endpoint": push_endpoint
        }

        if service_account_email and audience:
            push_config["oidc_token"] = {
                "service_account_email": service_account_email,
                "audience": audience
            }
        
        # Add payload unwrapping settings
        if enable_payload_unwrapping:
            push_config["no_wrapper"] = {
                "write_metadata": write_metadata
            }

        request = {
            "name": subscription_path,
            "topic": topic_path,
            "push_config": push_config,
            "ack_deadline_seconds": ack_deadline_seconds,
        }

        # Add optional features
        if message_retention_duration:
            request["message_retention_duration"] = message_retention_duration

        if enable_exactly_once_delivery:
            request["enable_exactly_once_delivery"] = True

        if retry_policy:
            request["retry_policy"] = retry_policy
        else:
            request["retry_policy"] = {
                "minimum_backoff": "10s",
                "maximum_backoff": "600s"
            }
        
        # Add dead letter policy
        if dead_letter_policy:
            dlq_topic_path = self.publisher.topic_path(
                self.project_id, 
                dead_letter_policy["dead_letter_topic"]
            )
            request["dead_letter_policy"] = {
                "dead_letter_topic": dlq_topic_path,
                "max_delivery_attempts": dead_letter_policy.get("max_delivery_attempts", 5)
            }
        
        if filter:
            request["filter"] = filter

        try:
            subscription = self.subscriber.create_subscription(request=request)
            logging.info(f"Created push subscription: {subscription.name}")
            return subscription.name
        except Exception as e:
            if "already exists" in str(e):
                logging.info(f"Subscription already exists: {subscription_path}")
                return subscription_path
            else:
                logging.error(f"Error creating subscription: {e}")
                raise

    def delete_subscription(self, subscription_name: str):
        """
        Delete a subscription.

        Args:
            subscription_name: Subscription to delete
        """
        subscription_path = self.subscriber.subscription_path(
            self.project_id, subscription_name
        )
        
        try:
            self.subscriber.delete_subscription(
                request={"subscription": subscription_path}
            )
            logging.info(f"Deleted subscription: {subscription_path}")
        except Exception as e:
            logging.error(f"Error deleting subscription: {e}")
            raise

    def detach_subscription(self, subscription_name: str):
        """
        Detach a subscription from its topic.
        After the detachment, the subscription will stop receiving messages from the topic.

        Args:
            subscription_name: Subscription to detach
        """
        subscription_path = self.subscriber.subscription_path(
            self.project_id, subscription_name
        )
        
        try:
            self.publisher.detach_subscription(
                request={"subscription": subscription_path}
            )
            logging.info(f"Detached subscription: {subscription_path}")
        except Exception as e:
            logging.error(f"Error detaching subscription: {e}")
            raise

    # ==================== PUBLISHING ====================

    def publish(
        self, 
        topic_name: str, 
        message_data: Union[Dict[str, Any], bytes, str], 
        ordering_key: Optional[str] = None,
        attributes: Optional[Dict[str, str]] = None,
        error_handler: Optional[Callable[[Exception], None]] = None
    ) -> str:
        """
        Publish a message with error handling.

        Args:
            topic_name: Topic to publish to
            message_data: Message data (dict, bytes, or string)
            ordering_key: Optional key to maintain message order
            attributes: Optional metadata
            error_handler: Optional error callback function

        Returns:
            Message ID from Pub/Sub
        """
        topic_path = self.publisher.topic_path(self.project_id, topic_name)
        
        # Convert data to bytes
        if isinstance(message_data, dict):
            data = json.dumps(message_data).encode("utf-8")
        elif isinstance(message_data, str):
            data = message_data.encode("utf-8")
        else:
            data = message_data

        kwargs = {}

        if ordering_key:
            kwargs["ordering_key"] = ordering_key
        if attributes:
            # Ensure all attribute values are strings
            kwargs.update({k: str(v) for k, v in attributes.items()})

        try:
            future = self.publisher.publish( topic_path, data, retry=Retry(deadline=10.0), **kwargs )
            
            # Add error callback if provided
            if error_handler:
                def callback(future):
                    try:
                        future.result()
                    except Exception as e:
                        error_handler(e)
                
                future.add_done_callback(callback)
            
            message_id = future.result()
            logging.info(f"Published message {message_id} to {topic_name}")
            return message_id
            
        except Exception as e:
            logging.error(f"Error publishing message: {e}")
            if error_handler:
                error_handler(e)
            raise

    def publish_proto_message(
        self,
        topic_name: str,
        proto_message: ProtoMessage,
        ordering_key: Optional[str] = None,
        attributes: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Publish a Protocol Buffer message.

        Args:
            topic_name: Topic to publish to
            proto_message: Protobuf message instance
            ordering_key: Optional ordering key
            attributes: Optional attributes

        Returns:
            Message ID
        """
        # Serialize protobuf to bytes
        data = proto_message.SerializeToString()
        
        return self.publish(
            topic_name=topic_name,
            message_data=data,
            ordering_key=ordering_key,
            attributes=attributes
        )

    def publish_batch(
        self,
        topic_name: str,
        messages: List[Dict[str, Any]],
        ordering_key: Optional[str] = None,
        error_handler: Optional[Callable[[Exception], None]] = None
    ) -> List[str]:
        """
        Publish multiple messages efficiently with error handling.
        
        Args:
            topic_name: Topic to publish to
            messages: List of message dictionaries
            ordering_key: Optional ordering key for all messages
            error_handler: Optional error callback
        
        Returns:
            List of message IDs
        """
        topic_path = self.publisher.topic_path(self.project_id, topic_name)
        futures = []
        
        batch_settings = pubsub_v1.types.BatchSettings(
            max_messages=100,
            max_bytes=1024 * 1024,
            max_latency=0.1,
        )
        
        batch_publisher = pubsub_v1.PublisherClient(
            batch_settings=batch_settings,
            publisher_options=pubsub_v1.types.PublisherOptions(
                enable_message_ordering=bool(ordering_key)
            )
        )
        
        for message in messages:
            data = json.dumps(message).encode("utf-8")
            kwargs = {"data": data}
            if ordering_key:
                kwargs["ordering_key"] = ordering_key
            
            try:
                future = batch_publisher.publish(topic_path, **kwargs)
                if error_handler:
                    def callback(f):
                        try:
                            f.result()
                        except Exception as e:
                            error_handler(e)
                    future.add_done_callback(callback)
                futures.append(future)
            except Exception as e:
                logging.error(f"Error publishing message in batch: {e}")
                if error_handler:
                    error_handler(e)
        
        message_ids = []
        for future in futures:
            try:
                message_ids.append(future.result())
            except Exception as e:
                logging.error(f"Failed to get message ID: {e}")
                if error_handler:
                    error_handler(e)
        
        logging.info(f"Published {len(message_ids)} messages to {topic_name}")
        return message_ids

    # ==================== SUBSCRIBING ====================

    def pull_messages(
        self, 
        subscription_name: str, 
        max_messages: int = 10,
        return_immediately: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Pull messages from a subscription (synchronous).

        Args:
            subscription_name: Subscription to pull from
            max_messages: Maximum messages to pull
            return_immediately: Return immediately if no messages

        Returns:
            List of message dictionaries
        """
        subscription_path = self.subscriber.subscription_path(
            self.project_id, subscription_name
        )

        request = {
            "subscription": subscription_path,
            "max_messages": max_messages,
            "return_immediately": return_immediately
        }

        response = self.subscriber.pull(
            request=request,
            retry=Retry(deadline=10.0)
        )

        messages = []
        ack_ids = []

        for received_message in response.received_messages:
            try:
                # Try to decode as JSON first
                message_data = json.loads(
                    received_message.message.data.decode("utf-8")
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                # If not JSON, return raw bytes
                message_data = received_message.message.data
            
            message_info = {
                "data": message_data,
                "attributes": dict(received_message.message.attributes),
                "message_id": received_message.message.message_id,
                "publish_time": received_message.message.publish_time,
                "ack_id": received_message.ack_id
            }
            
            messages.append(message_info)
            ack_ids.append(received_message.ack_id)

        if ack_ids:
            self.subscriber.acknowledge(
                request={
                    "subscription": subscription_path,
                    "ack_ids": ack_ids
                }
            )
            logging.info(f"Acknowledged {len(ack_ids)} messages")

        return messages

    def subscribe_with_callback(
        self, 
        subscription_name: str, 
        callback: Callable[[Dict[str, Any]], None], 
        error_handler: Optional[Callable[[Exception], None]] = None,
        timeout: Optional[int] = None,
        with_opentelemetry: bool = False,
        exactly_once_delivery: bool = False
    ):
        """
        Subscribe to messages with callbacks (asynchronous pull).

        Args:
            subscription_name: Subscription to listen to
            callback: Function to process each message
            error_handler: Optional error callback
            timeout: How long to listen (None = forever)
            with_opentelemetry: Enable OpenTelemetry tracing
            exactly_once_delivery: Enable exactly-once processing
        """
        subscription_path = self.subscriber.subscription_path(
            self.project_id, subscription_name
        )

        # Configure flow control
        flow_control = pubsub_v1.types.FlowControl(
            max_messages=100,
            max_bytes=1e9,  # 1GB
            max_lease_duration=3600,  # 1 hour
        )

        # Create subscriber with optional OpenTelemetry
        if with_opentelemetry:
            from opentelemetry import trace
            from opentelemetry.instrumentation.google_cloud_pubsub import PubSubInstrumentor
            
            PubSubInstrumentor().instrument()
            tracer = trace.get_tracer(__name__)
        else:
            tracer = None

        def message_callback(message):
            try:
                # Start span if using OpenTelemetry
                if tracer:
                    with tracer.start_as_current_span(
                        f"process_message_{subscription_name}"
                    ):
                        process_message(message)
                else:
                    process_message(message)
                    
            except Exception as e:
                logging.error(f"Error processing message: {e}")
                if error_handler:
                    error_handler(e)
                message.nack()

        def process_message(message):
            try:
                # Try to decode as JSON
                data = json.loads(message.data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # If not JSON, use raw bytes
                data = message.data
            
            message_info = {
                "data": data,
                "attributes": dict(message.attributes),
                "message_id": message.message_id,
                "publish_time": message.publish_time,
                "delivery_attempt": message.delivery_attempt if exactly_once_delivery else None
            }
            
            # Call user callback
            callback(message_info)
            
            # Acknowledge with optional exactly-once
            if exactly_once_delivery:
                message.ack_with_response()
            else:
                message.ack()
            
            logging.info(f"Processed message: {message.message_id}")

        # Start listening
        streaming_pull_future = self.subscriber.subscribe(
            subscription_path,
            callback=message_callback,
            flow_control=flow_control
        )

        logging.info(f"Listening for messages on {subscription_name}...")

        # Handle errors
        if error_handler:
            streaming_pull_future.add_done_callback(
                lambda future: error_handler(future.exception())
                if future.exception() else None
            )

        try:
            streaming_pull_future.result(timeout=timeout)
        except TimeoutError:
            streaming_pull_future.cancel()
            streaming_pull_future.result()
        logging.info("Subscription streaming pull cancelled due to timeout")

    def pull_proto_messages(
        self,
        subscription_name: str,
        proto_class: type,
        max_messages: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Pull and decode Protocol Buffer messages.

        Args:
            subscription_name: Subscription to pull from
            proto_class: Protobuf message class
            max_messages: Maximum messages to pull

        Returns:
            List of dictionaries with decoded proto messages
        """
        messages = self.pull_messages(subscription_name, max_messages)
        
        decoded_messages = []
        for msg in messages:
            try:
                # Decode protobuf
                proto_message = proto_class()
                proto_message.ParseFromString(msg["data"])
                
                msg["decoded_data"] = proto_message
                decoded_messages.append(msg)
            except Exception as e:
                logging.error(f"Error decoding proto message: {e}")
                msg["decode_error"] = str(e)
                decoded_messages.append(msg)
        
        return decoded_messages

    # ==================== UTILITY METHODS ====================

    def create_subscription(self, topic_name: str, subscription_name: str) -> str:
        """
        Create a basic pull subscription (backward compatibility).
        """
        return self.create_pull_subscription(
            topic_name=topic_name,
            subscription_name=subscription_name,
            ack_deadline_seconds=60,
            enable_exactly_once_delivery=True
        )

    def list_topics(self) -> List[str]:
        """
        List all topics in the project.
        """
        project_path = f"projects/{self.project_id}"
        topics = []
        
        for topic in self.publisher.list_topics(request={"project": project_path}):
            topics.append(topic.name)
        
        return topics

    def list_subscriptions(self) -> List[str]:
        """
        List all subscriptions in the project.
        """
        project_path = f"projects/{self.project_id}"
        subscriptions = []
        
        for sub in self.subscriber.list_subscriptions(request={"project": project_path}):
            subscriptions.append(sub.name)
        
        return subscriptions

    def get_topic_info(self, topic_name: str) -> Dict[str, Any]:
        """
        Get detailed information about a topic.
        """
        topic_path = self.publisher.topic_path(self.project_id, topic_name)
        
        try:
            topic = self.publisher.get_topic(request={"topic": topic_path})
            return {
                "name": topic.name,
                "labels": dict(topic.labels),
                "schema_settings": topic.schema_settings,
                "message_retention_duration": topic.message_retention_duration,
                "kms_key_name": topic.kms_key_name
            }
        except Exception as e:
            logging.error(f"Error getting topic info: {e}")
            raise

    def get_subscription_info(self, subscription_name: str) -> Dict[str, Any]:
        """
        Get detailed information about a subscription.
        """
        subscription_path = self.subscriber.subscription_path(
            self.project_id, subscription_name
        )
        
        try:
            sub = self.subscriber.get_subscription(
                request={"subscription": subscription_path}
            )
            return {
                "name": sub.name,
                "topic": sub.topic,
                "push_config": sub.push_config,
                "ack_deadline_seconds": sub.ack_deadline_seconds,
                "retain_acked_messages": sub.retain_acked_messages,
                "message_retention_duration": sub.message_retention_duration,
                "labels": dict(sub.labels),
                "enable_exactly_once_delivery": sub.enable_exactly_once_delivery,
                "dead_letter_policy": sub.dead_letter_policy,
                "retry_policy": sub.retry_policy,
                "filter": sub.filter
            }
        except Exception as e:
            logging.error(f"Error getting subscription info: {e}")
            raise