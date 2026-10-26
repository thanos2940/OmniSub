"""
Event Broadcaster
=================
Thread-safe and asyncio-friendly WebSocket and polling event broadcast system
for OmbiSub. Broadcasts real-time events (agent submissions, queue updates,
disk sync status, etc.) to all connected browser clients.
"""

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional
from fastapi import WebSocket

logger = logging.getLogger("omnisub.events")

class EventBroadcaster:
    """Manages WebSocket clients, event buffering, and real-time broadcasts."""

    def __init__(self, max_history: int = 100):
        self.active_connections: List[WebSocket] = []
        self._history: List[Dict[str, Any]] = []
        self._max_history = max_history
        self._lock = threading.Lock()
        self._counter = 0
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register the application's primary asyncio event loop."""
        self._main_loop = loop

    @property
    def latest_id(self) -> int:
        """Return the highest event ID assigned so far."""
        with self._lock:
            return self._counter

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        with self._lock:
            self.active_connections.append(websocket)
        logger.debug(f"WebSocket client connected. Active connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a disconnected WebSocket client."""
        with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.debug(f"WebSocket client disconnected. Active connections: {len(self.active_connections)}")

    async def broadcast_async(self, message: Dict[str, Any]) -> None:
        """Broadcast a message to all active WebSocket connections asynchronously."""
        with self._lock:
            connections = list(self.active_connections)

        dead_connections = []
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.debug(f"Error sending to WebSocket client: {e}")
                dead_connections.append(ws)

        if dead_connections:
            with self._lock:
                for d in dead_connections:
                    if d in self.active_connections:
                        self.active_connections.remove(d)

    def broadcast_event(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Broadcast an event to all WebSocket clients and buffer in history.
        Safe to call from async functions, background threads, or synchronous callers.
        """
        with self._lock:
            self._counter += 1
            event_id = self._counter
            payload = {
                "id": event_id,
                "type": event_type,
                "timestamp": time.time(),
                "data": data or {},
            }
            # Flatten top-level keys for easy access if needed
            if data:
                for k, v in data.items():
                    if k not in payload:
                        payload[k] = v

            self._history.append(payload)
            if len(self._history) > self._max_history:
                self._history.pop(0)

        # Broadcast via asyncio event loop
        scheduled = False
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self.broadcast_async(payload))
                scheduled = True
        except RuntimeError:
            pass

        # If called from a thread without an event loop, schedule onto main loop
        if not scheduled and self._main_loop is not None:
            try:
                if self._main_loop.is_running():
                    asyncio.run_coroutine_threadsafe(self.broadcast_async(payload), self._main_loop)
            except Exception as e:
                logger.debug(f"Failed to schedule broadcast on main event loop: {e}")

        return payload

    def get_events(self, since_id: int = 0, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent events newer than since_id for polling clients."""
        with self._lock:
            filtered = [e for e in self._history if e["id"] > since_id]
            return filtered[-limit:]


broadcaster = EventBroadcaster()


def broadcast_event(event_type: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Helper to broadcast an event globally."""
    return broadcaster.broadcast_event(event_type, data)


# ------------------------------------------------------------------ coalesced "the queue changed" event

_QUEUE_EVENT_GAP = 0.5   # seconds: queue changes come in bursts (a sync enqueues fifty rows); one event per burst
_queue_lock = threading.Lock()
_queue_last = 0.0
_queue_timer: Optional[threading.Timer] = None
_queue_reason = ""


def queue_changed(reason: str, **data: Any) -> None:
    """Broadcast ``queue_updated`` for a change to the translation queue, at most once per
    ``_QUEUE_EVENT_GAP``; a change inside the gap is sent when the gap ends (never dropped)."""
    global _queue_last, _queue_timer, _queue_reason

    def fire() -> None:
        global _queue_last, _queue_timer
        with _queue_lock:
            _queue_last = time.time()
            _queue_timer = None
            why = _queue_reason
        try:
            broadcast_event("queue_updated", {"reason": why, **data})
        except Exception as exc:  # events are best effort
            logger.debug(f"queue_updated not sent: {exc}")

    with _queue_lock:
        _queue_reason = reason
        wait = _QUEUE_EVENT_GAP - (time.time() - _queue_last)
        if wait <= 0:
            pending = False
        else:
            if _queue_timer is None:
                _queue_timer = threading.Timer(wait, fire)
                _queue_timer.daemon = True
                _queue_timer.start()
            pending = True
    if not pending:
        fire()
