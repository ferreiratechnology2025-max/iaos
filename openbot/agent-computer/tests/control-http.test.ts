import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ComputerControlState } from "../../shared/computer-control";

// A real HTTP dispatcher and shell lease, without launching Chromium. Opt in like live-screen tests
// because agent-computer's own Playwright dependency is not installed by the root unit-test lane.
const asked = process.env.OPENBOT_CONTROL_HTTP === "1";
let root = "";
let base = "";
let child: ReturnType<typeof Bun.spawn> | undefined;
let childOutput: Promise<string> | undefined;
let expectedRedactedSecret = "";
const token = "handoff-http-test-token";
async function post(path: string, body: unknown) {
  return fetch(`${base}${path}`, {
    method: "POST",
    headers: {
      "x-openbot-computer-token": token,
      "x-openbot-bot-id": "bot-http",
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
}
async function control(requestId?: string): Promise<ComputerControlState> {
  const response = await fetch(
    `${base}/control${requestId ? `?requestId=${requestId}` : ""}`,
    {
      headers: {
        "x-openbot-computer-token": token,
        "x-openbot-bot-id": "bot-http",
      },
    },
  );
  expect(response.status).toBe(200);
  return response.json();
}
async function until(check: () => Promise<boolean>) {
  const end = Date.now() + 5_000;
  while (!(await check())) {
    if (Date.now() > end)
      throw new Error(
        "Timed out waiting for the HTTP fixture's observable condition.",
      );
    await Bun.sleep(5);
  }
}
beforeAll(async () => {
  if (!asked) return;
  root = await mkdtemp(join(tmpdir(), "control-http-"));
  const probe = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    fetch: () => new Response(),
  });
  const port = probe.port;
  await probe.stop(true);
  base = `http://127.0.0.1:${port}`;
  child = Bun.spawn([process.execPath, "src/index.ts"], {
    cwd: join(import.meta.dir, ".."),
    env: {
      ...process.env,
      COMPUTER_TOKEN: token,
      COMPUTER_BROWSER_BACKEND: "managed",
      COMPUTER_BROWSER_MODE: "headless",
      PORT: String(port),
      PROFILES_DIR: join(root, "profiles"),
      WORKSPACE_DIR: join(root, "workspace"),
      COMPUTER_NAVIGATION_DIAGNOSTICS: asked ? "1" : "0",
    },
    stdout: asked ? "pipe" : "ignore",
    stderr: "inherit",
  });
  if (child.stdout) childOutput = new Response(child.stdout).text();
  await until(async () => {
    if (child?.exitCode !== null)
      throw new Error(`Computer fixture exited with ${child?.exitCode}.`);
    try {
      return (await fetch(`${base}/health`)).ok;
    } catch {
      return false;
    } // Explicit readiness probe, bounded above.
  });
});
afterAll(async () => {
  if (!asked) return;
  child?.kill();
  if (child) await child.exited;
  const output = (await childOutput) ?? "";
  const diagnostics = output
    .split("\n")
    .filter((line) => line.includes('"type":"computer-navigation-failed"'))
    .map((line) => JSON.parse(line));
  expect(diagnostics).toHaveLength(1);
  expect(diagnostics[0]).toMatchObject({
    type: "computer-navigation-failed",
    stage: "navigate_web_page",
    http_status: 502,
  });
  expect(output).not.toContain(token);
  expect(output).not.toContain("Proxy-Authorization");
  if (expectedRedactedSecret) expect(output).not.toContain(expectedRedactedSecret);
  expect(output).not.toContain("proxy-secret");
  await rm(root, { recursive: true, force: true });
});

describe.skipIf(!asked)(
  "request handoff through the actual computer HTTP dispatcher",
  () => {
    test("take cannot overtake admitted work; later mutations are refused, then freshness is mandatory", async () => {
      const running = post("/exec", {
        command: "printf admitted > admitted; sleep 0.3",
        timeoutMs: 3000,
      });
      await until(() => Bun.file(join(root, "workspace", "admitted")).exists());
      const requested = await post("/control/request", {
        reason: "A human step",
        toolCallId: "http-call",
      });
      const waiting: ComputerControlState = await requested.json();
      expect(waiting.transitioning).toBe(true);
      expect(waiting.holder).toBe("bot");
      const requestId = waiting.request!.id;
      const taking = post("/control/take", { requestId });
      const refused = await post("/files/write", {
        path: "too-late",
        contents: "wrong",
      });
      expect(refused.status).toBe(409);
      expect(await refused.json()).toMatchObject({
        humanHasControl: true,
        requestId,
      });
      expect((await control(requestId)).holder).toBe("bot");
      expect((await running).status).toBe(200);
      expect((await (await taking).json()).holder).toBe("human");
      expect(
        (await post("/control/release", { requestId: "wrong" })).status,
      ).toBe(409);
      expect((await post("/control/release", { requestId })).status).toBe(200);
      for (const path of [
        "/navigate",
        "/click",
        "/type",
        "/key",
        "/scroll",
        "/select",
      ]) {
        const response = await post(path, {});
        expect(response.status).toBe(409);
        expect(await response.json()).toMatchObject({
          stale: true,
          snapshotRequired: true,
        });
      }
      expect(
        (await post("/files/write", { path: "after", contents: "allowed" }))
          .status,
      ).toBe(200);
      expect((await control(requestId)).request?.status).toBe("completed");
      const next: ComputerControlState = await (
        await post("/control/request", {
          reason: "Next",
          toolCallId: "next-http-call",
        })
      ).json();
      expect((await post("/control/take", { requestId })).status).toBe(409);
      expect((await control(requestId)).request?.status).toBe("completed");
      expect(
        (await post("/control/cancel", { requestId: next.request!.id })).status,
      ).toBe(200);
    });

    test("failed navigation keeps 502 and emits one safe opt-in diagnostic", async () => {
      const syntheticSecret = "NAV_DIAG_SECRET_DO_NOT_LOG_7f3a91c2";
      expectedRedactedSecret = syntheticSecret;
      const target = Bun.serve({
        hostname: "127.0.0.1",
        port: 0,
        fetch: () => new Response("unreachable"),
      });
      const snapshot = await post("/snapshot", { includeAria: false });
      expect(snapshot.status).toBe(200);
      await target.stop(true);

      const response = await post("/navigate", {
        url:
          `http://127.0.0.1:${target.port}/closed?token=${syntheticSecret}` +
          "&proxy-authorization=Basic%20proxy-secret",
        requestHelp: false,
      });
      expect(response.status).toBe(502);
      const result = await response.json();
      expect(typeof result.error).toBe("string");
      expect(JSON.stringify(result)).toContain(syntheticSecret);
    });
  },
);
