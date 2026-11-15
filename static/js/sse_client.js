/**
 * Real-Time Server-Sent Events (SSE) Client
 * Uses native browser EventSource for auto-reconnecting, low-overhead HTTP streaming.
 */
class TelemetrySSE {
  constructor(endpoint = "/api/events/stream") {
    this.endpoint = endpoint;
    this.source = null;
    this.listeners = new Map();
    this.isConnected = false;
  }

  connect() {
    this.disconnect();

    const token = localStorage.getItem("auth_token") || "";
    const url = `${this.endpoint}?token=${encodeURIComponent(token)}`;

    try {
      this.source = new EventSource(url);

      this.source.onopen = () => {
        this.isConnected = true;
        this.trigger("connection_change", { connected: true });
      };

      this.source.onerror = (err) => {
        this.isConnected = false;
        this.trigger("connection_change", { connected: false });
      };

      // General message listener
      this.source.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          this.trigger("message", payload);
        } catch (e) {
          console.warn("SSE JSON parse error:", event.data);
        }
      };

      // Specific named event listeners
      const eventNames = [
        "connected",
        "record_ingested",
        "record_processing",
        "record_completed",
        "record_failed",
        "record_retry",
        "record_updated",
        "record_deleted",
        "records_batch_deleted",
        "records_priority_changed",
        "records_requeued",
        "records_purged",
        "rules_updated",
        "system_event",
        "ports_changed"
      ];

      eventNames.forEach((evName) => {
        this.source.addEventListener(evName, (e) => {
          try {
            const data = JSON.parse(e.data);
            this.trigger(evName, data);
          } catch (err) {
            this.trigger(evName, e.data);
          }
        });
      });

    } catch (err) {
      console.error("Failed to initialize EventSource:", err);
      this.trigger("connection_change", { connected: false });
    }
  }

  disconnect() {
    if (this.source) {
      this.source.close();
      this.source = null;
      this.isConnected = false;
      this.trigger("connection_change", { connected: false });
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
        console.error(`Error in SSE listener for '${event}':`, err);
      }
    });
  }
}

window.telemetrySSE = new TelemetrySSE();

