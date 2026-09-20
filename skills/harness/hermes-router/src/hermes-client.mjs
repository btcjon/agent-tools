const SESSION_ID_PATTERN = /^[A-Za-z0-9_.:@/-]{1,256}$/;

export function parseAllowedSessions(value = "") {
  return new Set(
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean)
  );
}

export function validateSessionId(sessionId) {
  if (!SESSION_ID_PATTERN.test(sessionId)) {
    throw new Error("Invalid Hermes session ID");
  }
  return sessionId;
}

function boundedText(value, max = 20000) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error("A non-empty message is required");
  }
  if (value.length > max) {
    throw new Error(`Message exceeds ${max} characters`);
  }
  return value.trim();
}

export class HermesClient {
  constructor({ baseUrl, apiKey, allowedSessions, allowCreate = false, timeoutMs = 120000, fetchImpl = fetch }) {
    const parsed = new URL(baseUrl);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      throw new Error("HERMES_BASE_URL must use http or https");
    }
    if (!apiKey || apiKey.length < 16) {
      throw new Error("HERMES_API_KEY must be present and at least 16 characters");
    }
    this.baseUrl = parsed.toString().replace(/\/$/, "");
    this.apiKey = apiKey;
    this.allowedSessions = new Set(allowedSessions || []);
    this.allowCreate = allowCreate;
    this.timeoutMs = timeoutMs;
    this.fetchImpl = fetchImpl;
  }

  assertAllowed(sessionId) {
    validateSessionId(sessionId);
    if (!this.allowedSessions.has(sessionId)) {
      throw new Error("Hermes session is not allowlisted");
    }
  }

  async request(path, { method = "GET", body, authenticated = true, signal } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    const onAbort = () => controller.abort();
    if (signal) {
      if (signal.aborted) controller.abort();
      else signal.addEventListener("abort", onAbort, { once: true });
    }
    try {
      const headers = { Accept: "application/json" };
      if (authenticated) headers.Authorization = `Bearer ${this.apiKey}`;
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const text = await response.text();
      let data = {};
      if (text) {
        try {
          data = JSON.parse(text);
        } catch {
          throw new Error(`Hermes returned non-JSON HTTP ${response.status}`);
        }
      }
      if (!response.ok) {
        const message = data?.error?.message || data?.message || `Hermes HTTP ${response.status}`;
        throw new Error(message);
      }
      return data;
    } catch (error) {
      if (method !== "GET") {
        throw new Error(`Hermes write outcome uncertain: ${error?.name === "AbortError" ? "request timed out" : error.message}. Do not retry automatically; inspect session history and the affected system first.`, { cause: error });
      }
      if (error?.name === "AbortError") throw new Error("Hermes request timed out");
      throw error;
    } finally {
      clearTimeout(timer);
      if (signal) signal.removeEventListener("abort", onAbort);
    }
  }

  async status() {
    const publicHealth = await this.request("/health", { authenticated: false });
    let detailed = null;
    let capabilities = null;
    try {
      detailed = await this.request("/health/detailed");
    } catch {
      // Older Hermes releases may not expose the detailed endpoint.
    }
    try {
      capabilities = await this.request("/v1/capabilities");
    } catch {
      // Compatibility is reported below instead of hiding a missing endpoint.
    }
    const sessionResourcesAvailable = capabilities?.features?.session_resources === true;
    const sessionChatAvailable = capabilities?.features?.session_chat === true;
    return {
      reachable: publicHealth?.status === "ok",
      status: detailed?.status || publicHealth?.status || "unknown",
      version: detailed?.version || publicHealth?.version || null,
      compatible: sessionResourcesAvailable && sessionChatAvailable,
      sessionResourcesAvailable,
      sessionChatAvailable,
      allowedSessionCount: this.allowedSessions.size,
      newSessionCreationEnabled: this.allowCreate,
    };
  }

  async listAllowedSessions() {
    if (this.allowedSessions.size === 0) return [];
    const sessions = await Promise.all(
      [...this.allowedSessions].map(async (sessionId) => {
        const data = await this.request(`/api/sessions/${encodeURIComponent(validateSessionId(sessionId))}`);
        const session = data?.session || data;
        if (!session?.id || session.id !== sessionId) {
          throw new Error(`Hermes returned an unexpected session for ${sessionId}`);
        }
        return session;
      })
    );
    return sessions.map((session) => ({
      id: session.id,
      title: session.title || null,
      source: session.source || null,
      startedAt: session.started_at || null,
      lastActiveAt: session.last_active_at || session.updated_at || null,
      pinned: Boolean(session.pinned),
      archived: Boolean(session.archived),
    }));
  }

  async history(sessionId, limit = 20) {
    this.assertAllowed(sessionId);
    const safeLimit = Math.max(1, Math.min(Number(limit) || 20, 50));
    const data = await this.request(`/api/sessions/${encodeURIComponent(sessionId)}/messages?limit=${safeLimit}&order=latest`);
    return (data?.data || []).map((message) => ({
      role: message.role || "unknown",
      content: typeof message.content === "string" ? message.content : JSON.stringify(message.content ?? ""),
      createdAt: message.created_at || message.timestamp || null,
    }));
  }

  async continueSession(sessionId, message, { signal } = {}) {
    this.assertAllowed(sessionId);
    let data;
    try {
      data = await this.request(`/api/sessions/${encodeURIComponent(sessionId)}/chat`, {
        method: "POST",
        body: { message: boundedText(message) },
        signal,
      });
    } catch (error) {
      throw new Error(`Session ${sessionId}: ${error.message}`, { cause: error });
    }
    return {
      sessionId: data?.session_id || sessionId,
      content: data?.message?.content || "",
      runtime: data?.runtime || {},
    };
  }

  async createAndChat(message, titlePrefix = "ChatGPT bridge") {
    if (!this.allowCreate) {
      throw new Error("New Hermes session creation is disabled");
    }
    message = boundedText(message);
    const suffix = new Date().toISOString().replace(/[:.]/g, "-");
    const title = `${titlePrefix} ${suffix}`.slice(0, 120);
    const created = await this.request("/api/sessions", {
      method: "POST",
      body: { title, source: "api_server" },
    });
    const sessionId = created?.session?.id;
    if (!sessionId) throw new Error("Session creation outcome uncertain: Hermes did not return a new session ID. Inspect Hermes locally before creating another session.");
    validateSessionId(sessionId);
    this.allowedSessions.add(sessionId);
    const result = await this.continueSession(sessionId, message);
    return { ...result, created: true };
  }
}
