import { constants } from "node:fs";
import { createHash, randomBytes } from "node:crypto";
import { mkdir, mkdtemp, open, realpath, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, extname, isAbsolute, join, resolve, sep } from "node:path";
import { promisify } from "node:util";
import { execFile, spawn } from "node:child_process";
import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { jsonResult } from "openclaw/plugin-sdk/tool-results";
import { normalizeOpenClawInboundEvent, readTrustedOpenClawMedia, readWebChatStlUpload, trustedMediaFilename, TrustedMediaReadError } from "./inbound-media.js";
import { markDuplicateInboundSession, sendClaimedIntakeReply, sendReplyForCompletedIntake, shouldSuppressDuplicateSessionReply } from "./intake-reply.js";
import { parseBridgeResponse } from "./bridge-response.js";

const execFileAsync = promisify(execFile);
const MAX_FILE_BYTES = 25 * 1024 * 1024;
const TIMEOUT_MS = 10_000;
const SLICE_TIMEOUT_MS = 40_000;
const MAX_OUTPUT_BYTES = 1_048_576;
let intakeCleanupTimer: ReturnType<typeof setInterval> | undefined;
let replyDrainTimer: ReturnType<typeof setInterval> | undefined;
const duplicateInboundSessions = new Map<string, number>();

const configSchema = Type.Object({
  jobsDirectory: Type.String({ description: "Absolute path to the trusted STL jobs directory." }),
  analyzerScript: Type.String({ description: "Absolute path to experiments/stl-analysis/analyze_stl.py." }),
  persistenceScript: Type.String({ description: "Absolute path to experiments/stl-analysis/persistence.py." }),
  productionEstimateScript: Type.String({ description: "Absolute path to experiments/slicing/openclaw_production_estimate.py." }),
  quoteEngine: Type.String({ description: "Absolute path to experiments/quote-engine/quote.py." }),
  databasePath: Type.String({ description: "Absolute path to the project-local SQLite database." }),
  bridgeScript: Type.String({ description: "Absolute path to the trusted Stage 5 channel bridge." }),
  localToolsScript: Type.Optional(Type.String({ description: "Absolute path to the authenticated local onboarding and production adapter." })),
  identityKeyFile: Type.String({ description: "Private deployment identity signing key, mode 0600." }),
  uploadSpool: Type.String({ description: "Private Stage 5 upload spool directory." }),
  pendingIntakeRoot: Type.String({ description: "Private persistent pending-intake spool directory." }),
  openclawMediaRoot: Type.String({ description: "Absolute private root for OpenClaw's staged inbound media files." }),
  openclawCli: Type.Optional(Type.String({ description: "Absolute OpenClaw CLI path used only for the local channel readiness probe." })),
  privateJobsRoot: Type.String({ description: "Private per-job file root." }),
  identityAudience: Type.String({ description: "Audience expected by the local farm application." }),
  pythonExecutable: Type.Optional(Type.String({ description: "Python executable path or PATH-resolved name; defaults to python3." })),
  dockerExecutable: Type.Optional(Type.String({ description: "Docker executable path or PATH-resolved name; defaults to docker." })),
}, { additionalProperties: false });

function errorResult(filename: string, error: string) {
  return { success: false, filename, error };
}

function runBridge(config: Record<string, unknown>, payload: Record<string, unknown>, signal?: AbortSignal): Promise<Record<string, unknown>> {
  return new Promise((resolveResult, reject) => {
    const python = String(config.pythonExecutable || "python3");
    const script = String(config.bridgeScript || "");
    if (!isAbsolute(script) || !isAbsolute(String(config.databasePath || ""))) {
      reject(new Error("The Stage 5 bridge configuration is incomplete."));
      return;
    }
    const child = spawn(python, [script], { stdio: ["pipe", "pipe", "ignore"], windowsHide: true });
    const chunks: Buffer[] = [];
    let total = 0;
    const timeout = setTimeout(() => child.kill("SIGKILL"), 15_000);
    const abort = () => child.kill("SIGTERM");
    signal?.addEventListener("abort", abort, { once: true });
    child.stdout.on("data", (chunk: Buffer) => {
      total += chunk.length;
      if (total > MAX_OUTPUT_BYTES) child.kill("SIGKILL");
      else chunks.push(chunk);
    });
    child.on("error", (error) => { clearTimeout(timeout); reject(error); });
    child.on("close", (code) => {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
      if (code !== 0) {
        try { reject(new Error(String(JSON.parse(Buffer.concat(chunks).toString("utf8")).error || "The farm request was denied."))); }
        catch { reject(new Error("The farm request could not be completed.")); }
        return;
      }
      try { resolveResult(parseBridgeResponse(Buffer.concat(chunks).toString("utf8"))); }
      catch (error) { reject(error); }
    });
    child.stdin.end(JSON.stringify({ ...payload, database: config.databasePath,
      key_file: config.identityKeyFile, spool_root: config.uploadSpool,
      pending_intake_root: config.pendingIntakeRoot,
      private_jobs_root: config.privateJobsRoot, audience: config.identityAudience }));
  });
}

function runLocalTool(config: Record<string, unknown>, payload: Record<string, unknown>, signal?: AbortSignal): Promise<Record<string, unknown>> {
  return new Promise((resolveResult, reject) => {
    const python = String(config.pythonExecutable || "python3");
    const script = String(config.localToolsScript || "");
    if (!isAbsolute(script) || !isAbsolute(String(config.databasePath || ""))) {
      reject(new Error("The local farm tools are not configured."));
      return;
    }
    const child = spawn(python, [script], { stdio: ["pipe", "pipe", "ignore"], windowsHide: true });
    const chunks: Buffer[] = [];
    let total = 0;
    const timeout = setTimeout(() => child.kill("SIGKILL"), TIMEOUT_MS);
    const abort = () => child.kill("SIGTERM");
    signal?.addEventListener("abort", abort, { once: true });
    child.stdout.on("data", (chunk: Buffer) => {
      total += chunk.length;
      if (total > MAX_OUTPUT_BYTES) child.kill("SIGKILL");
      else chunks.push(chunk);
    });
    child.on("error", (error) => { clearTimeout(timeout); reject(error); });
    child.on("close", (code) => {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
      const output = Buffer.concat(chunks).toString("utf8");
      try {
        const result = JSON.parse(output) as Record<string, unknown>;
        if (code !== 0) reject(new Error(String(result.error || "The farm operation was denied.")));
        else resolveResult(result);
      } catch {
        reject(new Error("The local farm operation returned invalid output."));
      }
    });
    child.stdin.end(JSON.stringify({ ...payload, database: config.databasePath }));
  });
}

function localStaffTool(ctx: { messageChannel?: string; senderIsOwner?: boolean }) {
  // A local Gateway token-backed WebChat owner is the trusted staff boundary.
  // Public channel sessions never receive these tools in their catalog.
  return ctx.messageChannel === "webchat" && ctx.senderIsOwner === true;
}

async function ensureIdentityKey(path: string): Promise<void> {
  if (!isAbsolute(path)) throw new Error("The farm identity key path must be absolute.");
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  try {
    await writeFile(path, randomBytes(32), { flag: "wx", mode: 0o600 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
  }
}

const pluginEntry = defineToolPlugin({
  id: "print-farm-stl",
  name: "Print Farm STL Analysis",
  description: "Analyze STL files from a restricted local jobs directory.",
  configSchema,
  tools: (tool) => [
    tool({
      name: "analyze_stl",
      label: "Analyze STL",
      description: "Analyze one STL file already staged in the configured jobs directory. Pass only its filename, never an arbitrary path.",
      parameters: Type.Object({
        filename: Type.String({ description: "STL filename in the configured jobs directory, for example small-box-20mm.stl." }),
      }, { additionalProperties: false }),
      outputSchema: Type.Object({
        success: Type.Boolean(),
        filename: Type.String(),
        error: Type.Optional(Type.String()),
        analysis: Type.Optional(Type.Object({
          format: Type.String(),
          triangle_count: Type.Integer(),
          connected_components_by_shared_vertices: Type.Integer(),
          dimensions_mm: Type.Object({ x: Type.Number(), y: Type.Number(), z: Type.Number() }),
          bounds_mm: Type.Object({
            min: Type.Array(Type.Number()),
            max: Type.Array(Type.Number()),
          }),
          volume_cm3: Type.Union([Type.Number(), Type.Null()]),
          watertight_heuristic: Type.Boolean(),
          boundary_or_nonmanifold_edges: Type.Integer(),
          fits_example_220x220x250mm: Type.Boolean(),
          orientation: Type.String(),
          units_assumption: Type.String(),
        }, { additionalProperties: false })),
        analysis_id: Type.Optional(Type.String()),
      }, { additionalProperties: false }),
      async execute({ filename }, config, context) {
        context.signal?.throwIfAborted();

        if (!filename || filename !== basename(filename) || filename === "." || filename === "..") {
          return errorResult(String(filename ?? ""), "Pass a filename only; directory paths are not accepted.");
        }
        if (extname(filename).toLowerCase() !== ".stl") {
          return errorResult(filename, "Only .stl files are accepted.");
        }
        if (!isAbsolute(config.jobsDirectory) || !isAbsolute(config.analyzerScript)
          || !isAbsolute(config.persistenceScript) || !isAbsolute(config.databasePath)) {
          return errorResult(filename, "The jobs directory, scripts, and database must use absolute paths.");
        }

        const jobsRoot = resolve(config.jobsDirectory);
        const candidate = resolve(jobsRoot, filename);
        if (!candidate.startsWith(`${jobsRoot}${sep}`)) {
          return errorResult(filename, "The requested file is outside the configured jobs directory.");
        }

        let source;
        let privateDirectory: string | undefined;
        try {
          const metadata = await stat(candidate);
          if (!metadata.isFile()) return errorResult(filename, "The requested path is not a regular file.");
          if (metadata.size <= 0) return errorResult(filename, "The STL file is empty.");
          if (metadata.size > MAX_FILE_BYTES) {
            return errorResult(filename, `The STL exceeds the ${MAX_FILE_BYTES} byte limit.`);
          }

          // O_NOFOLLOW rejects a final-component symlink. Recheck the opened
          // descriptor to narrow races before taking the bounded private copy.
          const handle = await open(candidate, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
          try {
            const opened = await handle.stat();
            if (!opened.isFile() || opened.size <= 0 || opened.size > MAX_FILE_BYTES) {
              return errorResult(filename, "The opened file is not a valid regular STL within the size limit.");
            }
            source = await handle.readFile();
          } finally {
            await handle.close();
          }

          privateDirectory = await mkdtemp(join(tmpdir(), "print-farm-stl-"));
          const privateFile = join(privateDirectory, filename);
          await writeFile(privateFile, source, { flag: "wx", mode: 0o600 });

          const python = config.pythonExecutable || "python3";
          const { stdout } = await execFileAsync(python, [config.analyzerScript, privateFile], {
            timeout: TIMEOUT_MS,
            maxBuffer: MAX_OUTPUT_BYTES,
            windowsHide: true,
            signal: context.signal,
          });
          context.signal?.throwIfAborted();
          const parsed = JSON.parse(stdout);
          const { file: _temporaryPath, ...analysis } = parsed;
          const resultFile = join(privateDirectory, "analysis.json");
          await writeFile(resultFile, JSON.stringify(analysis), { flag: "wx", mode: 0o600 });
          const { stdout: storedStdout } = await execFileAsync(config.pythonExecutable || "python3", [
            config.persistenceScript,
            "store",
            "--database",
            config.databasePath,
            "--filename",
            filename,
            "--result-file",
            resultFile,
          ], {
            timeout: TIMEOUT_MS,
            maxBuffer: MAX_OUTPUT_BYTES,
            windowsHide: true,
            signal: context.signal,
          });
          const stored = JSON.parse(storedStdout);
          return { success: true, filename, analysis_id: stored.analysis_id, analysis };
        } catch (error) {
          const failure = error as NodeJS.ErrnoException & { killed?: boolean; stdout?: string };
          if (failure.code === "ENOENT") return errorResult(filename, "The file or configured analyzer was not found.");
          if (failure.code === "ELOOP") return errorResult(filename, "Symbolic links are not accepted as STL inputs.");
          if (failure.killed || failure.code === "ETIMEDOUT") return errorResult(filename, "STL analysis exceeded the 10 second execution limit.");
          if (failure.stdout) return errorResult(filename, "The analyzer returned invalid or oversized structured output.");
          return errorResult(filename, "The file could not be analyzed as a valid STL.");
        } finally {
          if (privateDirectory) await rm(privateDirectory, { recursive: true, force: true });
        }
      },
    }),
    tool({
      name: "get_latest_stl_analysis",
      label: "Get Latest STL Analysis",
      description: "Retrieve the most recent completed STL analysis from the local application database.",
      parameters: Type.Object({}, { additionalProperties: false }),
      async execute(_params, config, context) {
        context.signal?.throwIfAborted();
        if (!isAbsolute(config.persistenceScript) || !isAbsolute(config.databasePath)) {
          return { found: false, message: "The persistence script and database must use absolute paths." };
        }
        try {
          const { stdout } = await execFileAsync(config.pythonExecutable || "python3", [
            config.persistenceScript,
            "latest",
            "--database",
            config.databasePath,
          ], {
            timeout: TIMEOUT_MS,
            maxBuffer: MAX_OUTPUT_BYTES,
            windowsHide: true,
            signal: context.signal,
          });
          return JSON.parse(stdout);
        } catch {
          return { found: false, message: "The saved STL analysis could not be retrieved." };
        }
      },
    }),
    tool({
      name: "slice_stl",
      label: "Slice STL and Estimate Quote",
      description: "Run the fixed Cura profile for a staged STL. A business quote is returned only when the persisted profile validation, material record, and business configuration are quote-approved. First call analyze_stl and pass its analysis_id.",
      parameters: Type.Object({
        filename: Type.String({ description: "STL basename previously analyzed from the configured jobs directory." }),
        analysis_id: Type.String({ description: "analysis_id returned by analyze_stl for this exact filename." }),
        profile: Type.Union([
          Type.Literal("cura-ultimaker2plus-generic-pla-normal"),
        ], { description: "Approved fixed Cura profile identifier." }),
        quantity: Type.Optional(Type.Integer({ minimum: 1, maximum: 20, description: "Number of sequentially estimated copies; defaults to 1." })),
      }, { additionalProperties: false }),
      async execute({ filename, analysis_id, profile, quantity }, config, context) {
        context.signal?.throwIfAborted();
        if (!isAbsolute(config.jobsDirectory) || !isAbsolute(config.productionEstimateScript)
          || !isAbsolute(config.persistenceScript) || !isAbsolute(config.quoteEngine)
          || !isAbsolute(config.databasePath)) {
          return errorResult(filename, "The jobs directory, scripts, and database must use absolute paths.");
        }
        const jobsRoot = resolve(config.jobsDirectory);
        if (!filename || filename !== basename(filename) || filename === "." || filename === ".."
          || filename.includes("\\") || extname(filename).toLowerCase() !== ".stl") {
          return errorResult(String(filename ?? ""), "Pass an STL filename only; directory paths are not accepted.");
        }
        try {
          const candidate = resolve(jobsRoot, filename);
          if (!candidate.startsWith(`${jobsRoot}${sep}`)) {
            return errorResult(filename, "The requested file is outside the configured jobs directory.");
          }
          const metadata = await stat(candidate);
          if (!metadata.isFile() || metadata.size <= 0 || metadata.size > MAX_FILE_BYTES) {
            return errorResult(filename, "The requested file must be a nonempty regular STL within the 25 MiB limit.");
          }
          context.signal?.throwIfAborted();
          const { stdout } = await execFileAsync(config.pythonExecutable || "python3", [
            config.productionEstimateScript,
            "--filename", filename,
            "--analysis-id", analysis_id,
            "--profile", profile,
            "--quantity", String(quantity ?? 1),
            "--jobs-directory", config.jobsDirectory,
            "--persistence-script", config.persistenceScript,
            "--database-path", config.databasePath,
            "--quote-engine", config.quoteEngine,
            "--docker-executable", config.dockerExecutable || "docker",
            "--python-executable", config.pythonExecutable || "python3",
          ], {
            timeout: SLICE_TIMEOUT_MS,
            maxBuffer: MAX_OUTPUT_BYTES,
            windowsHide: true,
            signal: context.signal,
          });
          context.signal?.throwIfAborted();
          return JSON.parse(stdout);
        } catch (error) {
          const failure = error as NodeJS.ErrnoException & { killed?: boolean; stdout?: string };
          if (failure.code === "ENOENT") return errorResult(filename, "The STL, configured production runner, or executable was not found.");
          if (failure.killed || failure.code === "ETIMEDOUT") return errorResult(filename, "Cura production estimate exceeded the 40 second tool limit.");
          if (failure.stdout) {
            try {
              return JSON.parse(failure.stdout);
            } catch {
              return errorResult(filename, "The production runner returned invalid or oversized structured output.");
            }
          }
          return errorResult(filename, "The Cura slice or quote calculation failed safely.");
        }
      },
    }),
    tool({
      name: "get_latest_production_estimate",
      label: "Get Latest Production Estimate",
      description: "Retrieve the latest completed Cura estimate and quote from the project SQLite database.",
      parameters: Type.Object({}, { additionalProperties: false }),
      async execute(_params, config, context) {
        context.signal?.throwIfAborted();
        if (!isAbsolute(config.persistenceScript) || !isAbsolute(config.databasePath)) {
          return { found: false, message: "The persistence script and database must use absolute paths." };
        }
        try {
          const { stdout } = await execFileAsync(config.pythonExecutable || "python3", [
            config.persistenceScript,
            "latest-estimate",
            "--database",
            config.databasePath,
          ], {
            timeout: TIMEOUT_MS,
            maxBuffer: MAX_OUTPUT_BYTES,
            windowsHide: true,
            signal: context.signal,
          });
          return JSON.parse(stdout);
        } catch {
          return { found: false, message: "The saved production estimate could not be retrieved." };
        }
      },
    }),
    tool({
      name: "farm_get_pending_intake_status",
      label: "Check Pending Print Request",
      description: "Read the current verified sender's own pending request/file correlation state.",
      parameters: Type.Object({}, { additionalProperties: false }),
      factory: ({ config, toolContext }) => {
        if (toolContext.messageChannel !== "whatsapp" || !toolContext.requesterSenderId || !toolContext.nativeChannelId) return null;
        const channel = toolContext.messageChannel;
        const account = toolContext.agentAccountId || "default";
        const sender = toolContext.requesterSenderId;
        const conversation = toolContext.nativeChannelId;
        return {
          name: "farm_get_pending_intake_status",
          label: "Check Pending Print Request",
          description: "Read only the pending intake state for this verified sender and conversation.",
          parameters: Type.Object({}, { additionalProperties: false }),
          async execute(toolCallId: string, _params: Record<string, never>, signal?: AbortSignal) {
            try {
              const result = await runBridge(config as unknown as Record<string, unknown>, {
                operation: "intake_context", channel, account_id: account,
                sender_id: sender, conversation_id: conversation,
              }, signal);
              const configRecord = config as unknown as Record<string, unknown>;
              const replyContext = { channel, account, sender, conversation };
              const replyResults: unknown[] = [];
              for (const recovered of Array.isArray(result.recovered) ? result.recovered : []) {
                replyResults.push(await sendReplyForCompletedIntake(recovered, (intakeId, jobId) =>
                  sendIntakeConfirmation(configRecord, replyContext, intakeId, jobId, toolCallId)));
              }
              if (result.status === "completed") {
                replyResults.push(await sendReplyForCompletedIntake(result, (intakeId, jobId) =>
                  sendIntakeConfirmation(configRecord, replyContext, intakeId, jobId, toolCallId)));
              }
              return jsonResult({ ...result, ...(replyResults.length ? { confirmation_results: replyResults } : {}) });
            } catch {
              return jsonResult({ status: "unavailable", message: "The pending request state could not be checked." });
            }
          },
        };
      },
    }),
    tool({
      name: "farm_send_intake_confirmation",
      label: "Confirm Print Request Once",
      description: "Send one application-generated success confirmation for a completed customer intake. Duplicate calls are suppressed persistently. After using this tool, return NO_REPLY without writing another confirmation.",
      parameters: Type.Object({
        intake_id: Type.String({ minLength: 1, maxLength: 64 }),
        job_id: Type.String({ minLength: 1, maxLength: 64 }),
      }, { additionalProperties: false }),
      factory: ({ config, toolContext }) => {
        if (toolContext.messageChannel !== "whatsapp" || !toolContext.requesterSenderId
          || !toolContext.nativeChannelId
          || toolContext.nativeChannelId.endsWith("@g.us")) return null;
        const channel = toolContext.messageChannel;
        const account = toolContext.agentAccountId || "default";
        const sender = toolContext.requesterSenderId;
        const conversation = toolContext.nativeChannelId;
        return {
          name: "farm_send_intake_confirmation",
          label: "Confirm Print Request Once",
          description: "Send one fixed success confirmation only if this run wins the persistent application reply claim.",
          parameters: Type.Object({
            intake_id: Type.String({ minLength: 1, maxLength: 64 }),
            job_id: Type.String({ minLength: 1, maxLength: 64 }),
          }, { additionalProperties: false }),
          async execute(toolCallId: string, params: { intake_id: string; job_id: string }) {
            return jsonResult(await sendIntakeConfirmation(
              config as unknown as Record<string, unknown>,
              { channel, account, sender, conversation },
              params.intake_id, params.job_id, toolCallId,
            ));
          },
        };
      },
    }),
    tool({
      name: "farm_resolve_pending_intake",
      label: "Choose Request File",
      description: "Resolve an explicitly ambiguous request/file match for the current verified direct sender. Use only the pending choices supplied by the farm intake context.",
      parameters: Type.Object({
        request_id: Type.String({ minLength: 1, maxLength: 64 }),
        attachment_id: Type.String({ minLength: 1, maxLength: 64 }),
      }, { additionalProperties: false }),
      factory: ({ config, toolContext }) => {
        if (toolContext.messageChannel !== "whatsapp" || !toolContext.requesterSenderId || !toolContext.nativeChannelId) return null;
        const channel = toolContext.messageChannel;
        const account = toolContext.agentAccountId || "default";
        const sender = toolContext.requesterSenderId;
        const conversation = toolContext.nativeChannelId;
        return {
          name: "farm_resolve_pending_intake",
          label: "Choose Request File",
          description: "Pair one pending request and STL selected by this verified sender.",
          parameters: Type.Object({
            request_id: Type.String({ minLength: 1, maxLength: 64 }),
            attachment_id: Type.String({ minLength: 1, maxLength: 64 }),
          }, { additionalProperties: false }),
          async execute(_toolCallId: string, params: { request_id: string; attachment_id: string }, signal?: AbortSignal) {
            try {
              const result = await runBridge(config as unknown as Record<string, unknown>, {
                operation: "resolve_intake", channel, account_id: account, sender_id: sender,
                conversation_id: conversation, request_id: params.request_id,
                attachment_id: params.attachment_id, analyzer_script: config.analyzerScript,
              }, signal);
              const replyContext = { channel, account, sender, conversation };
              const configRecord = config as unknown as Record<string, unknown>;
              await sendReplyForCompletedIntake(result, (intakeId, jobId) => sendIntakeConfirmation(
                configRecord, replyContext, intakeId, jobId, `${_toolCallId}:resolved`));
              if (result.additional_job && typeof result.additional_job === "object") {
                await sendReplyForCompletedIntake(result.additional_job as Record<string, unknown>, (intakeId, jobId) =>
                  sendIntakeConfirmation(configRecord, replyContext, intakeId, jobId, `${_toolCallId}:resolved-additional`));
              }
              return jsonResult({ success: true, ...result });
            } catch {
              return jsonResult({ success: false, message: "That selection is no longer available." });
            }
          },
        };
      },
    }),
    tool({
      name: "farm_read_own_job",
      label: "Read My Print Request",
      description: "Read a job only when it belongs to the current verified WhatsApp sender. The sender identity comes from Gateway context.",
      parameters: Type.Object({ job_id: Type.String({ minLength: 1, maxLength: 64 }) }, { additionalProperties: false }),
      factory: ({ config, toolContext }) => {
        if (toolContext.messageChannel !== "whatsapp" || !toolContext.requesterSenderId) return null;
        const account = toolContext.agentAccountId || "default";
        const sender = toolContext.requesterSenderId;
        return {
          name: "farm_read_own_job",
          label: "Read My Print Request",
          description: "Read a job for the verified current sender, with customer ownership enforcement.",
          parameters: Type.Object({ job_id: Type.String({ minLength: 1, maxLength: 64 }) }, { additionalProperties: false }),
          async execute(_toolCallId: string, params: { job_id: string }, signal?: AbortSignal) {
            try {
              const result = await runBridge(config as unknown as Record<string, unknown>, {
                operation: "read_own_job", channel: toolContext.messageChannel,
                account_id: account, sender_id: sender, job_id: params.job_id,
              }, signal);
              return jsonResult({ success: true, ...result });
            } catch {
              return jsonResult({ success: false, message: "That request was not found or is not accessible to this sender." });
            }
          },
        };
      },
    }),
    tool({
      name: "farm_onboarding",
      label: "Configure Print Farm",
      description: "Continue or resume first-run setup. Ask the displayed question in conversation and pass the user's answer; every answer is saved immediately.",
      parameters: Type.Object({ answer: Type.Optional(Type.String({ minLength: 1, maxLength: 500 })) }, { additionalProperties: false }),
      factory: ({ config, toolContext }) => {
        if (!localStaffTool(toolContext)) return null;
        return {
          name: "farm_onboarding",
          label: "Configure Print Farm",
          description: "Continue or resume persistent farm setup using the user's answer to the last question.",
          parameters: Type.Object({ answer: Type.Optional(Type.String({ minLength: 1, maxLength: 500 })) }, { additionalProperties: false }),
          async execute(_id: string, params: { answer?: string }, signal?: AbortSignal) {
            return jsonResult(await runLocalTool(config as unknown as Record<string, unknown>,
              { operation: "farm_onboarding", ...params }, signal));
          },
        };
      },
    }),
    tool({
      name: "get_farm_configuration",
      label: "Get Farm Configuration",
      description: "The authoritative read for the authenticated owner's persisted farm state. Always use this for questions about configured printers or nozzle sizes, primary slicer or material, solo/team mode, onboarding status, and the current user's roles. Do not inspect Gateway config or use shell/search/SQLite to answer these questions.",
      parameters: Type.Object({}, { additionalProperties: false }),
      factory: ({ config, toolContext }) => {
        if (!localStaffTool(toolContext)) return null;
        return {
          name: "get_farm_configuration",
          label: "Get Farm Configuration",
          description: "Read persisted farm onboarding, active printers, primary slicer/material, operating mode, and current owner roles.",
          parameters: Type.Object({}, { additionalProperties: false }),
          async execute(_id: string, _params: Record<string, never>, signal?: AbortSignal) {
            try {
              return jsonResult(await runLocalTool(config as unknown as Record<string, unknown>,
                { operation: "get_farm_configuration" }, signal));
            } catch (error) {
              return jsonResult({ success: false, error: error instanceof Error ? error.message : "The farm configuration could not be read." });
            }
          },
        };
      },
    }),
    ...(["list_ready_jobs", "inspect_production_job", "assign_printer", "start_job", "finish_job"] as const).map((name) =>
      tool({
        name,
        label: name.replaceAll("_", " "),
        description: "Use the authenticated staff production workflow backed by the local farm domain.",
        parameters: name === "list_ready_jobs" ? Type.Object({}, { additionalProperties: false })
          : name === "inspect_production_job" ? Type.Object({ job_id: Type.String({ minLength: 1 }) }, { additionalProperties: false })
          : name === "assign_printer" ? Type.Object({ job_id: Type.String({ minLength: 1 }), printer_id: Type.String({ minLength: 1 }) }, { additionalProperties: false })
          : name === "finish_job" ? Type.Object({ job_id: Type.String({ minLength: 1 }), outcome: Type.Union([Type.Literal("COMPLETED"), Type.Literal("FAILED")]), details: Type.Optional(Type.String({ maxLength: 500 })) }, { additionalProperties: false })
          : Type.Object({ job_id: Type.String({ minLength: 1 }) }, { additionalProperties: false }),
        factory: ({ config, toolContext }) => {
          if (!localStaffTool(toolContext)) return null;
          return {
            name,
            label: name.replaceAll("_", " "),
            description: "Perform one authorized manual production workflow operation. Printer start and completion remain operator-confirmed.",
            parameters: name === "list_ready_jobs" ? Type.Object({}, { additionalProperties: false })
              : name === "inspect_production_job" ? Type.Object({ job_id: Type.String({ minLength: 1 }) }, { additionalProperties: false })
              : name === "assign_printer" ? Type.Object({ job_id: Type.String({ minLength: 1 }), printer_id: Type.String({ minLength: 1 }) }, { additionalProperties: false })
              : name === "finish_job" ? Type.Object({ job_id: Type.String({ minLength: 1 }), outcome: Type.Union([Type.Literal("COMPLETED"), Type.Literal("FAILED")]), details: Type.Optional(Type.String({ maxLength: 500 })) }, { additionalProperties: false })
              : Type.Object({ job_id: Type.String({ minLength: 1 }) }, { additionalProperties: false }),
            async execute(_id: string, params: Record<string, unknown>, signal?: AbortSignal) {
              try {
                return jsonResult(await runLocalTool(config as unknown as Record<string, unknown>,
                  { operation: name, ...params }, signal));
              } catch (error) {
                return jsonResult({ success: false, error: error instanceof Error ? error.message : "The farm operation failed." });
              }
            },
          };
        },
      })),
  ],
});
let pluginApi: Parameters<typeof pluginEntry.register>[0] | undefined;

type IntakeReplyContext = {
  channel: string;
  account: string;
  sender: string;
  conversation: string;
};

async function sendIntakeConfirmation(
  config: Record<string, unknown>,
  context: IntakeReplyContext,
  intakeId: string,
  jobId: string,
  runId: string,
) {
  const api = pluginApi;
  if (!api || context.channel !== "whatsapp" || context.conversation.endsWith("@g.us")) {
    return { status: "delivery_uncertain" as const, final_reply: "NO_REPLY" as const };
  }
  let adapter: Awaited<ReturnType<typeof api.runtime.channel.outbound.loadAdapter>> | undefined;
  try {
    const result = await sendClaimedIntakeReply(
      async () => {
        const claimed = await runBridge(config, {
          operation: "claim_intake_reply", channel: context.channel, account_id: context.account,
          sender_id: context.sender, conversation_id: context.conversation,
          intake_id: intakeId, job_id: jobId, run_id: runId,
        });
        return { send: claimed.send === true,
          job_id: typeof claimed.job_id === "string" ? claimed.job_id : undefined,
          safe_filename: typeof claimed.safe_filename === "string" ? claimed.safe_filename : undefined };
      },
      async (text) => {
        if (!adapter?.sendText) throw new Error("The WhatsApp outbound adapter is unavailable.");
        return await adapter.sendText({ cfg: api.config, to: context.conversation, text, accountId: context.account });
      },
      async (platformMessageId) => {
        const result = await runBridge(config, {
          operation: "mark_intake_reply_sent", account_id: context.account,
          sender_id: context.sender, intake_id: intakeId, run_id: runId,
          platform_message_id: platformMessageId,
        });
        if (result.status !== "sent") throw new Error("The provider receipt could not be committed.");
      },
      async () => {
        adapter = await api.runtime.channel.outbound.loadAdapter("whatsapp");
        if (!adapter?.sendText) throw new Error("The WhatsApp outbound adapter is unavailable.");
      },
      async () => {
        const result = await runBridge(config, {
          operation: "mark_intake_reply_not_sent", account_id: context.account,
          sender_id: context.sender, intake_id: intakeId, run_id: runId,
        });
        if (result.status !== "not_sent") throw new Error("The one-shot claim was not safely released.");
      },
      async () => {
        await runBridge(config, {
          operation: "mark_intake_reply_uncertain", account_id: context.account,
          sender_id: context.sender, intake_id: intakeId, run_id: runId,
        });
      },
    );
    api.logger.info(`Stage 5 intake confirmation result: status=${result.status} intake_ref=${hashReference(intakeId)}.`);
    return result;
  } catch (error) {
    const category = error instanceof Error ? error.name : "UnknownError";
    api.logger.warn(`Stage 5 intake confirmation failed: stage=claim_or_transport failure_category=${category} intake_ref=${hashReference(intakeId)}.`);
    return { status: "delivery_uncertain" as const, final_reply: "NO_REPLY" as const };
  }
}

function hashReference(value: string): string {
  return createHash("sha256").update(value).digest("hex").slice(0, 12);
}

async function readLocalChannelStatus(config: Record<string, unknown>): Promise<Record<string, unknown>> {
  const executable = String(config.openclawCli || "");
  if (!isAbsolute(executable)) throw new Error("The local OpenClaw CLI path is not configured.");
  const { stdout } = await execFileAsync(executable, ["channels", "status", "--probe", "--json"], {
    encoding: "utf8", timeout: 15_000, maxBuffer: 1_048_576,
    env: process.env,
  });
  return JSON.parse(stdout) as Record<string, unknown>;
}

async function whatsappAccountConnected(config: Record<string, unknown>, accountId: string): Promise<boolean> {
  try {
    const status = await readLocalChannelStatus(config);
    const accounts = (status.channelAccounts as Record<string, unknown> | undefined)?.whatsapp;
    if (!Array.isArray(accounts)) return false;
    return accounts.some((account) => account !== null && typeof account === "object"
      && (account as Record<string, unknown>).accountId === accountId
      && (account as Record<string, unknown>).connected === true);
  } catch {
    return false;
  }
}

function schedulePendingReplyDrain(
  api: Parameters<typeof pluginEntry.register>[0],
  config: Record<string, unknown>,
): void {
  if (replyDrainTimer) clearInterval(replyDrainTimer);
  let running = false;
  let attempts = 0;
  replyDrainTimer = setInterval(() => {
    if (running) return;
    running = true;
    attempts += 1;
    void (async () => {
      let stage = "gateway_status";
      try {
        const probe = await readLocalChannelStatus(config);
        const accountMap = probe.channelAccounts as Record<string, unknown> | undefined;
        const accountList = accountMap?.whatsapp;
        const isConnected = Array.isArray(accountList) && accountList.some((account) => account !== null
          && typeof account === "object" && (account as Record<string, unknown>).connected === true);
        if (!isConnected) {
          if (attempts >= 120 && replyDrainTimer) {
            clearInterval(replyDrainTimer);
            replyDrainTimer = undefined;
            api.logger.warn("Stage 5 reply drain stopped because WhatsApp did not become connected.");
          }
          return;
        }
        stage = "pending_reply_lookup";
        const pending = await runBridge(config, { operation: "list_unreplied_completed_intakes",
          account_id: "gateway", sender_id: "gateway" });
        let replyAttempts = 0;
        for (const item of Array.isArray(pending.items) ? pending.items : []) {
          if (typeof item.intake_id !== "string" || typeof item.job_id !== "string"
            || typeof item.channel !== "string" || typeof item.account_id !== "string"
            || typeof item.sender_id !== "string" || typeof item.conversation_id !== "string") continue;
          if (item.channel !== "whatsapp" || !await whatsappAccountConnected(config, item.account_id)) continue;
          stage = "send_confirmation";
          await sendIntakeConfirmation(config, { channel: item.channel,
            account: item.account_id, sender: item.sender_id,
            conversation: item.conversation_id }, item.intake_id, item.job_id,
          `gateway-ready:${item.intake_id}`);
          replyAttempts += 1;
        }
        if (replyAttempts) api.logger.info(`Stage 5 connected-channel reply drain attempted ${replyAttempts} intake(s).`);
        if (replyDrainTimer) clearInterval(replyDrainTimer);
        replyDrainTimer = undefined;
      } catch (error) {
        const category = error instanceof Error ? error.name : "UnknownError";
        api.logger.warn(`Stage 5 connected-channel reply drain failed: stage=${stage} failure_category=${category}.`);
      } finally {
        running = false;
      }
    })();
  }, 3_000);
  replyDrainTimer.unref?.();
}

const registerTools = pluginEntry.register;
pluginEntry.register = (api: Parameters<typeof pluginEntry.register>[0]) => {
  pluginApi = api;
  registerTools(api);
  api.on("message_received", async (event, context) => {
    const channel = context.channelId.trim().toLowerCase();
    if (channel === "webchat" && event.media?.length) {
      const config = api.config.plugins?.entries?.["print-farm-stl"]?.config as Record<string, unknown>;
      try {
        const upload = await readWebChatStlUpload(event, context, String(config.openclawMediaRoot || ""));
        await ensureIdentityKey(String(config.identityKeyFile || ""));
        const result = await runBridge(config, {
          operation: "submit_webchat_stl", channel: upload.channel,
          account_id: upload.account_id, sender_id: upload.sender_id,
          conversation_id: upload.conversation_id, message_id: upload.message_id,
          filename: upload.filename, attachment_b64: upload.bytes.toString("base64"),
          analyzer_script: config.analyzerScript,
        });
        api.logger.info(`WebChat STL intake persisted: status=${String(result.status || "unknown")} replay=${String(result.idempotent_replay === true)}.`);
      } catch (error) {
        const category = error instanceof TrustedMediaReadError
          ? error.category
          : error instanceof Error ? error.name : "UnknownError";
        api.logger.warn(`WebChat STL attachment was not accepted: reason=${category}.`);
      }
      return;
    }
    // WhatsApp is the only currently configured public customer surface.
    // The application correlation contract itself is channel-neutral.
    if (channel !== "whatsapp") return;
    const normalized = normalizeOpenClawInboundEvent(event, context);
    if (!normalized) {
      api.logger.warn("Stage 5 skipped an inbound event lacking trusted sender/conversation/message context.");
      return;
    }
    const media = normalized.media;
    const attachments: Array<{ filename: string; data_b64: string }> = [];
    let ingestAttempted = false;
    const config = api.config.plugins?.entries?.["print-farm-stl"]?.config as Record<string, unknown>;
    try {
      for (const item of media) {
        const data = await readTrustedOpenClawMedia(item, String(config.openclawMediaRoot || ""));
        const filename = trustedMediaFilename(item);
        attachments.push({ filename, data_b64: data.toString("base64") });
      }
      ingestAttempted = true;
      const result = await runBridge(config, {
        operation: "ingest_event", channel: normalized.channel, account_id: normalized.account_id,
        sender_id: normalized.sender_id, conversation_id: normalized.conversation_id,
        message_id: normalized.message_id, timestamp: normalized.timestamp,
        text: normalized.text, attachments,
        is_group: normalized.conversation_id.endsWith("@g.us"),
      });
      if (result.status === "duplicate") markDuplicateInboundSession(context.sessionKey, duplicateInboundSessions);
      if (result.status === "created" || result.status === "completed") {
        await sendReplyForCompletedIntake(result, (intakeId, jobId) => sendIntakeConfirmation(
          config, { channel: normalized.channel, account: normalized.account_id,
            sender: normalized.sender_id, conversation: normalized.conversation_id },
          intakeId, jobId, `${normalized.message_id}:hook`));
      }
      api.logger.info(`Stage 5 persisted inbound event: status=${String(result.status || "unknown")} attachment_count=${attachments.length}.`);
    } catch (error) {
      const category = error instanceof TrustedMediaReadError
        ? error.category
        : error instanceof Error ? error.name : "UnknownError";
      api.logger.warn(`Stage 5 inbound event was not accepted: failure_category=${category} attachment_count=${attachments.length}.`);
      if (ingestAttempted && attachments.length > 0) {
        try {
          const recovered = await runBridge(config, {
            operation: "intake_context", channel: normalized.channel,
            account_id: normalized.account_id, sender_id: normalized.sender_id,
            conversation_id: normalized.conversation_id,
          });
          const candidates = [
            ...(Array.isArray(recovered.recovered) ? recovered.recovered : []),
            ...(recovered.status === "completed" ? [recovered] : []),
          ];
          const sent = new Set<string>();
          for (const candidate of candidates) {
            if (typeof candidate.intake_id !== "string" || sent.has(candidate.intake_id)) continue;
            sent.add(candidate.intake_id);
            await sendReplyForCompletedIntake(candidate, (intakeId, jobId) => sendIntakeConfirmation(
              config, { channel: normalized.channel, account: normalized.account_id,
                sender: normalized.sender_id, conversation: normalized.conversation_id },
              intakeId, jobId, `${normalized.message_id}:recovery`));
          }
        } catch (recoveryError) {
          const recoveryCategory = recoveryError instanceof Error ? recoveryError.name : "UnknownError";
          api.logger.warn(`Stage 5 matched-intake recovery failed: failure_category=${recoveryCategory}.`);
        }
      }
    }
  });
  api.on("before_tool_call", (event, context) => {
    if (event.toolName === "message" && context.requester?.channel === "whatsapp") {
      return { block: true, blockReason: "Customer WhatsApp replies must use the current turn or the idempotent farm intake confirmation." };
    }
  });
  api.on("message_sending", (_event, context) => {
    if (context.channelId.trim().toLowerCase() === "whatsapp"
      && shouldSuppressDuplicateSessionReply(context.sessionKey, duplicateInboundSessions)) {
      return { cancel: true, cancelReason: "duplicate inbound message receipt" };
    }
  });
  api.on("model_call_ended", (event) => {
    if (event.failureKind === "aborted" || event.errorCategory === "abort") {
      api.logger.info(`Stage 5 observed model lifecycle abort: failure_kind=${event.failureKind || "unknown"} error_category=${event.errorCategory || "unknown"} duration_ms=${event.durationMs}.`);
    }
  });
  api.on("gateway_start", async () => {
    try {
      const config = api.config.plugins?.entries?.["print-farm-stl"]?.config as Record<string, unknown>;
      await runBridge(config, {
        operation: "recover_intakes", account_id: "gateway", sender_id: "gateway",
        analyzer_script: config.analyzerScript,
      });
      schedulePendingReplyDrain(api, config);
      if (!intakeCleanupTimer) {
        intakeCleanupTimer = setInterval(() => {
          void runBridge(config, { operation: "cleanup_intakes", account_id: "gateway", sender_id: "gateway" })
            .catch(() => api.logger.warn("Stage 5 scheduled pending-intake cleanup failed."));
        }, 5 * 60 * 1000);
        intakeCleanupTimer.unref?.();
      }
      api.logger.info("Stage 5 startup reconciliation completed for persisted pending intakes.");
    } catch (error) {
      const name = error instanceof Error ? error.name : "UnknownError";
      api.logger.warn(`Stage 5 startup reconciliation failed: error_type=${name}.`);
    }
  });
  api.on("gateway_stop", () => {
    if (intakeCleanupTimer) clearInterval(intakeCleanupTimer);
    intakeCleanupTimer = undefined;
    if (replyDrainTimer) clearInterval(replyDrainTimer);
    replyDrainTimer = undefined;
  });
};

export default pluginEntry;
