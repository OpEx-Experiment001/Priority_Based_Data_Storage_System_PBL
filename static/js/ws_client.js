/**
 * Real-Time WebSocket Client with Heartbeat and Auto-Reconnect.
 * Dispatches real-time telemetry, queue, and worker events to registered listeners.
 */
class TelemetryWebSocket {
  constructor(endpoint = "/ws") {
    this.endpoint = endpoint;
    this.socket = null;
    this.listeners = new Map();
    this.isConnected = false;
    this.reconnectInterval = 2500;
    this.heartbeatTimer = null;
  }

  connect() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const url = `${protocol}//${host}${this.endpoint}`;

    try {
      this.socket = new WebSocket(url);

      this.socket.onopen = () => {
        this.isConnected = true;
        this.trigger("connection_change", { connected: true });
        this.startHeartbeat();
      };

      this.socket.onclose = () => {
        this.isConnected = false;
        this.stopHeartbeat();
        this.trigger("connection_change", { connected: false });
        setTimeout(() => this.connect(), this.reconnectInterval);
      };

      this.socket.onerror = (err) => {
        this.trigger("error", err);
      };

      this.socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          this.trigger(payload.event || "message", payload.data || payload);
        } catch (e) {
          console.warn("Failed to parse WebSocket JSON payload:", event.data);
        }
      };
    } catch (err) {
      setTimeout(() => this.connect(), this.reconnectInterval);
    }
  }

  startHeartbeat() {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.isConnected && this.socket.readyState === WebSocket.OPEN) {
        this.send({ action: "PING" });
      }
    }, 15000);
  }

  stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  send(data) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(data));
    }
  }

  on(event, callback) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, []);
    }
    this.listeners.get(event).push(callback);
  }

  trigger(event, data) {
    const callbacks = this.listeners.get(event) || [];
    callbacks.forEach((cb) => {
      try {
        cb(data);
      } catch (err) {
        console.error(`Error in WebSocket listener for '${event}':`, err);
      }
    });
  }
}

window.telemetryWS = new TelemetryWebSocket();

