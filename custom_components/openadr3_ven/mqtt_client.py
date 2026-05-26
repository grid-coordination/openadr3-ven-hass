"""MQTT subscription manager for OpenADR 3 VEN integration."""

from __future__ import annotations

import json
import logging
import ssl
from typing import Any, Callable
from urllib.parse import urlparse

import paho.mqtt.client as mqtt

from openadr3 import Event, coerce_notification

_LOGGER = logging.getLogger(__name__)


def parse_broker_uri(uri: str) -> tuple[str, int, bool]:
    """Parse an MQTT broker URI into (host, port, use_tls)."""
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or "localhost"
    use_tls = scheme in ("mqtts", "ssl")
    default_port = 8883 if use_tls else 1883
    port = parsed.port or default_port
    return host, port, use_tls


def pick_broker_uri(uris: list[str]) -> str | None:
    """Pick the best broker URI, preferring TLS."""
    tls_uris = [u for u in uris if urlparse(u).scheme in ("mqtts", "ssl")]
    if tls_uris:
        return tls_uris[0]
    return uris[0] if uris else None


class MqttSubscriptionManager:
    """Manages MQTT subscriptions for OpenADR 3 event notifications."""

    def __init__(
        self,
        broker_uri: str,
        topics: list[str],
        on_event: Callable[[Event, str], None],
        on_connected: Callable[[], None] | None = None,
        client_id: str = "hass-openadr3-ven",
    ) -> None:
        self._host, self._port, self._use_tls = parse_broker_uri(broker_uri)
        self._on_event = on_event
        self._on_connected = on_connected
        self._topics = list(topics)

        self._client = mqtt.Client(
            client_id=client_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        if self._use_tls:
            self._client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._connected = False

    def start(self) -> None:
        """Connect to broker and start the network loop."""
        _LOGGER.info(
            "Connecting to MQTT broker at %s:%s (TLS=%s)",
            self._host, self._port, self._use_tls,
        )
        self._client.connect(self._host, self._port, keepalive=60)
        self._client.loop_start()

    def stop(self) -> None:
        """Disconnect and stop the network loop."""
        self._client.loop_stop()
        self._client.disconnect()
        _LOGGER.info("Disconnected from MQTT broker")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def update_topics(self, new_topics: list[str]) -> None:
        """Replace the subscribed topic set, applying the diff via (un)subscribe.

        Safe to call from any thread once the client loop is running; paho's
        subscribe/unsubscribe are thread-safe.
        """
        old = set(self._topics)
        new = set(new_topics)
        for topic in old - new:
            self._client.unsubscribe(topic)
            _LOGGER.debug("Unsubscribed from %s", topic)
        for topic in new - old:
            self._client.subscribe(topic)
            _LOGGER.debug("Subscribed to %s", topic)
        self._topics = list(new_topics)

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        rc: mqtt.ReasonCode,
        properties: mqtt.Properties | None = None,
    ) -> None:
        # rc.value == 0 means Success (MQTT v5) / Connection Accepted (v3.1.1).
        # Constructing ReasonCode(CONNACK_ACCEPTED) raises on paho-mqtt 2.x —
        # that constructor wants a name string, not the legacy int constant.
        if rc.value == 0:
            _LOGGER.info(
                "Connected to MQTT broker, subscribing to %d program topic(s)",
                len(self._topics),
            )
            for topic in self._topics:
                client.subscribe(topic)
                _LOGGER.debug("Subscribed to %s", topic)
            self._connected = True
            if self._on_connected is not None:
                try:
                    self._on_connected()
                except Exception:
                    _LOGGER.exception("on_connected callback failed")
        else:
            _LOGGER.error("MQTT connection failed: %s", rc)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.DisconnectFlags,
        rc: mqtt.ReasonCode,
        properties: mqtt.Properties | None = None,
    ) -> None:
        self._connected = False
        # rc.value == 0 means Normal Disconnection; non-zero is unexpected.
        if rc.value != 0:
            _LOGGER.warning("Unexpected MQTT disconnect: %s (will auto-reconnect)", rc)

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        try:
            raw = json.loads(msg.payload)
        except (json.JSONDecodeError, ValueError):
            _LOGGER.warning("Failed to parse MQTT message on %s", msg.topic)
            return

        try:
            notif = coerce_notification(raw)
        except Exception:
            _LOGGER.exception("Failed to parse MQTT notification on %s", msg.topic)
            return

        if notif.object_type != "EVENT" or not isinstance(notif.object, Event):
            _LOGGER.debug(
                "Ignoring non-EVENT notification (object_type=%s) on %s",
                notif.object_type, msg.topic,
            )
            return

        try:
            _LOGGER.debug(
                "MQTT %s for program %s: %s",
                notif.operation, notif.object.program_id, notif.object.event_name,
            )
            self._on_event(notif.object, notif.operation)
        except Exception:
            _LOGGER.exception("Failed to process MQTT event on %s", msg.topic)
