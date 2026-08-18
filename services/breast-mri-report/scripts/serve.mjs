import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname } from "node:path";
import { createAgentService } from "../src/server/agentService.mjs";
import { createBasicAuth, securityHeaders } from "../src/server/httpSecurity.mjs";
import { resolveStaticFile } from "../src/server/staticFiles.mjs";

const root = process.cwd();

if (!process.env.PORT) {
  console.error("[FATAL] 缺少 PORT 环境变量，请通过启动器（launcher.py 或 start.py）启动服务，不要直接运行 serve.mjs");
  process.exit(1);
}
if (!process.env.HOST) {
  console.error("[FATAL] 缺少 HOST 环境变量，请通过启动器（launcher.py 或 start.py）启动服务，不要直接运行 serve.mjs");
  process.exit(1);
}

const port = Number(process.env.PORT);
const host = process.env.HOST;
const release = process.env.RELEASE_TAG || "development";
const agentService = createAgentService();
const basicAuth = createBasicAuth();
const mime = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".md": "text/markdown; charset=utf-8",
  ".svg": "image/svg+xml; charset=utf-8"
};

const sendJson = (response, status, payload) => {
  response.writeHead(status, {
    ...securityHeaders,
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store"
  });
  response.end(JSON.stringify(payload));
};

const readJson = async (request) => {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 1_000_000) throw new Error("请求体超过1MB限制");
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
};

const server = createServer(async (request, response) => {
  let pathname;
  try {
    pathname = decodeURIComponent(new URL(request.url, `http://${request.headers.host || "localhost"}`).pathname);
  } catch {
    return sendJson(response, 400, { error: "Invalid request URL" });
  }

  if (pathname === "/api/health" && request.method === "GET") {
    return sendJson(response, 200, {
      status: "ok",
      service: "breast-mri-report",
      release
    });
  }

  if (!basicAuth.isAuthorized(request.headers.authorization)) {
    response.writeHead(401, {
      ...securityHeaders,
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store",
      "WWW-Authenticate": "Basic realm=\"Breast MRI Report Test\", charset=\"UTF-8\""
    });
    response.end("Authentication required");
    return;
  }

  if (pathname === "/api/model-status" && request.method === "GET") {
    try {
      return sendJson(response, 200, await agentService.modelStatus());
    } catch (error) {
      return sendJson(response, 200, {
        online: false,
        fallbackAvailable: true,
        model: "local-rule-fallback",
        error: error.message
      });
    }
  }
  if (pathname === "/api/agent/turn" && request.method === "POST") {
    try {
      return sendJson(response, 200, await agentService.runTurn(await readJson(request)));
    } catch (error) {
      return sendJson(response, 502, { error: error.message });
    }
  }
  if (pathname.startsWith("/api/")) return sendJson(response, 404, { error: "API not found" });

  const filePath = resolveStaticFile(root, pathname);
  if (!filePath || !existsSync(filePath) || statSync(filePath).isDirectory()) {
    response.writeHead(404, {
      ...securityHeaders,
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store"
    });
    response.end("Not found");
    return;
  }
  response.writeHead(200, {
    ...securityHeaders,
    "Content-Type": mime[extname(filePath)] || "application/octet-stream",
    "Cache-Control": "no-store"
  });
  createReadStream(filePath).pipe(response);
});

server.listen(port, host, () => {
  console.log(`乳腺 MRI 报告 MVP 已启动：http://${host}:${port}（${release}）`);
});

const shutdown = (signal) => {
  console.log(`收到 ${signal}，正在停止服务……`);
  server.close((error) => {
    if (error) {
      console.error(error);
      process.exit(1);
    }
    process.exit(0);
  });
};

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));