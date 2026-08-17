import { timingSafeEqual } from "node:crypto";

export const securityHeaders = Object.freeze({
  "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY"
});

const secureEqual = (left, right) => {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
};

export function createBasicAuth(options = {}) {
  const username = String(options.username ?? process.env.BASIC_AUTH_USER ?? "");
  const password = String(options.password ?? process.env.BASIC_AUTH_PASSWORD ?? "");

  if (Boolean(username) !== Boolean(password)) {
    throw new Error("BASIC_AUTH_USER 和 BASIC_AUTH_PASSWORD 必须同时配置");
  }
  if (username.includes(":")) throw new Error("BASIC_AUTH_USER 不能包含冒号");

  const enabled = Boolean(username && password);
  return {
    enabled,
    isAuthorized(header) {
      if (!enabled) return true;
      if (typeof header !== "string" || !header.startsWith("Basic ")) return false;

      let decoded;
      try {
        decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
      } catch {
        return false;
      }
      const separator = decoded.indexOf(":");
      if (separator < 0) return false;
      return secureEqual(decoded.slice(0, separator), username)
        && secureEqual(decoded.slice(separator + 1), password);
    }
  };
}
