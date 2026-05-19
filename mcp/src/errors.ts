export interface McpErrorResponse {
  content: Array<{ type: "text"; text: string }>;
  isError: true;
  _meta?: { status_code?: number; upgrade_url?: string };
}

export class BackendError extends Error {
  readonly statusCode: number;
  readonly body: string;
  readonly upgradeUrl?: string;

  constructor(statusCode: number, body: string, upgradeUrl?: string) {
    super(body);
    this.name = "BackendError";
    this.statusCode = statusCode;
    this.body = body;
    this.upgradeUrl = upgradeUrl;
  }

  toMcpResponse(): McpErrorResponse {
    const parts = [this.body];
    if (this.upgradeUrl) {
      parts.push(`Upgrade: ${this.upgradeUrl}`);
    }
    return {
      content: [{ type: "text", text: parts.join("\n\n") }],
      isError: true,
      _meta: {
        status_code: this.statusCode,
        ...(this.upgradeUrl ? { upgrade_url: this.upgradeUrl } : {}),
      },
    };
  }
}
