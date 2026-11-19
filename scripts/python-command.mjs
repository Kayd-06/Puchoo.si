import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = fileURLToPath(new URL("../", import.meta.url));

export function pythonExecutable({
  platform = process.platform,
  root = projectRoot,
  exists = existsSync,
} = {}) {
  const suffix =
    platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"];
  for (const directory of [".venv", "venv"]) {
    const candidate = path.join(root, directory, ...suffix);
    if (exists(candidate)) return candidate;
  }
  return platform === "win32" ? "python" : "python3";
}

export function runPython(args, { spawnProcess = spawn } = {}) {
  const child = spawnProcess(pythonExecutable(), args, {
    cwd: projectRoot,
    stdio: "inherit",
    shell: false,
  });
  child.on("error", () => {
    console.error(
      "Python could not start. Install Python, create .venv, and install requirements.txt before running this command.",
    );
    process.exitCode = 1;
  });
  child.on("exit", (code) => {
    process.exitCode = code ?? 1;
  });
  return child;
}
