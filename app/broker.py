"""RabbitMQ topology and persistent, confirmed publication via Pika."""
import json
import os
from .common import encode

EXCHANGE = "maltashop.orders"
QUEUE = "notification.order-placed"
ROUTING_KEY = "order.placed.v1"
DEAD_EXCHANGE = "maltashop.dead"
DEAD_QUEUE = "notification.invalid"


def connect():
    import pika
    parameters = pika.URLParameters(os.environ.get("BROKER_URL", "amqp://maltashop:local-demo-broker@broker:5672/%2F"))
    parameters.socket_timeout = 3
    parameters.stack_timeout = 5
    parameters.blocked_connection_timeout = 3
    parameters.connection_attempts = 1
    parameters.heartbeat = 30
    return pika.BlockingConnection(parameters)


def declare(channel):
    channel.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(exchange=DEAD_EXCHANGE, exchange_type="fanout", durable=True)
    channel.queue_declare(queue=DEAD_QUEUE, durable=True)
    channel.queue_bind(queue=DEAD_QUEUE, exchange=DEAD_EXCHANGE)
    channel.queue_declare(queue=QUEUE, durable=True,
                          arguments={"x-dead-letter-exchange": DEAD_EXCHANGE})
    channel.queue_bind(queue=QUEUE, exchange=EXCHANGE, routing_key=ROUTING_KEY)


def publish(event):
    import pika
    connection = connect()
    try:
        channel = connection.channel()
        declare(channel)
        channel.confirm_delivery()
        channel.basic_publish(exchange=EXCHANGE, routing_key=ROUTING_KEY,
            body=encode(event).encode(), mandatory=True,
            properties=pika.BasicProperties(delivery_mode=2, content_type="application/json",
                message_id=event["event_id"], correlation_id=event["correlation_id"],
                type=event["event_type"]))
    finally:
        if connection.is_open:
            connection.close()


def decode_event(body):
    try:
        event = json.loads(body)
        if not isinstance(event, dict):
            raise ValueError("object required")
        for key in ("event_id", "correlation_id", "occurred_at"):
            if not isinstance(event[key], str) or not event[key] or len(event[key]) > 128:
                raise ValueError("invalid envelope")
        if event["event_type"] != "OrderPlaced":
            raise ValueError("unsupported type")
        if type(event["schema_version"]) is not int or event["schema_version"] != 1:
            raise ValueError("unsupported schema version")
        data = event["data"]
        for key in ("order_id", "customer_id", "sku"):
            if not isinstance(data[key], str) or not data[key]:
                raise ValueError("invalid data")
        if type(data["quantity"]) is not int or data["quantity"] <= 0:
            raise ValueError("invalid quantity")
        if type(data["amount_cents"]) is not int or data["amount_cents"] <= 0:
            raise ValueError("invalid amount")
        if data["currency"] != "EUR":
            raise ValueError("invalid currency")
        return event
    except (ValueError, TypeError, KeyError) as cause:
        raise ValueError("INVALID_ORDER_PLACED_V1") from cause
