import { access, readFile } from "fs/promises";
import { execFile } from "child_process";
import { dirname, extname, join } from "path";
import { fileURLToPath } from "url";
import { promisify } from "util";

import {
	createReadToolDefinition,
	detectSupportedImageMimeTypeFromFile,
	type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";

const execFileAsync = promisify(execFile);

const DOCUMENT_EXTENSIONS = new Set([".docx", ".pdf", ".pptx", ".xlsx"]);
const EXTENSION_DIR = dirname(fileURLToPath(import.meta.url));
const RECIPE_ROOT = join(EXTENSION_DIR, "..");
const PARSER_PATH = join(RECIPE_ROOT, "tools/parse_document.py");
const PYTHON_CANDIDATES = [
	process.env.PYTHON,
	process.env.VIRTUAL_ENV ? join(process.env.VIRTUAL_ENV, "bin/python") : undefined,
	join(RECIPE_ROOT, "python/.venv/bin/python"),
	"python3",
	"python",
].filter((candidate): candidate is string => Boolean(candidate));

function documentFormat(path: string): string | undefined {
	const suffix = extname(path).toLowerCase();
	return DOCUMENT_EXTENSIONS.has(suffix) ? suffix.slice(1) : undefined;
}

async function parseDocument(path: string, format: string): Promise<Buffer> {
	let lastError: unknown;

	for (const python of PYTHON_CANDIDATES) {
		try {
			const { stdout } = await execFileAsync(python, [PARSER_PATH, format, path], {
				encoding: "utf8",
				maxBuffer: 50 * 1024 * 1024,
				timeout: 120_000,
			});
			return Buffer.from(stdout, "utf8");
		} catch (error: any) {
			lastError = error;
			if (error?.code === "ENOENT") {
				continue;
			}

			const stderr = typeof error?.stderr === "string" ? error.stderr.trim() : "";
			const message = stderr.split("\n").filter(Boolean).at(-1) || error?.message || String(error);
			return Buffer.from(`Error: failed to parse ${path} (${format}): ${message}`, "utf8");
		}
	}

	const message = lastError instanceof Error ? lastError.message : String(lastError);
	return Buffer.from(`Error: failed to parse ${path} (${format}): ${message}`, "utf8");
}

export default function documentReadExtension(pi: ExtensionAPI) {
	const readFileTool = createReadToolDefinition(process.cwd(), {
		operations: {
			access,
			async detectImageMimeType(path) {
				return documentFormat(path) ? undefined : detectSupportedImageMimeTypeFromFile(path);
			},
			async readFile(path) {
				const format = documentFormat(path);
				return format ? parseDocument(path, format) : readFile(path);
			},
		},
	});

	readFileTool.name = "read_file";
	readFileTool.label = "read_file";
	readFileTool.description =
		"Read the contents of a workspace file. Supports plain text, images, and document formats (.docx, .xlsx, .pptx, .pdf). For large text output, use offset/limit to continue.";
	readFileTool.promptSnippet = "Read file contents, including Office/PDF documents";
	readFileTool.promptGuidelines = [
		"Use read_file to examine files instead of cat or sed, especially for .docx, .xlsx, .pptx, and .pdf files.",
	];

	pi.registerTool(readFileTool);
}
