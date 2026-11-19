import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import path from "node:path";
import test from "node:test";
import { pythonExecutable, runPython } from "./python-command.mjs";

test("finds both virtualenv names across supported platforms", () => {
  for (const platform of ["darwin", "linux", "win32"]) {
    const root = path.resolve("project with spaces");
    const suffix =
      platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"];
    const modern = path.join(root, ".venv", ...suffix);
    const legacy = path.join(root, "venv", ...suffix);
    assert.equal(
      pythonExecutable({ platform, root, exists: () => true }),
      modern,
    );
    assert.equal(
      pythonExecutable({ platform, root, exists: (p) => p === legacy }),
      legacy,
    );
    assert.equal(
      pythonExecutable({ platform, root, exists: () => false }),
      platform === "win32" ? "python" : "python3",
    );
  }
});

test("runs Python without a shell, from the project root, preserving arguments", () => {
  const child = new EventEmitter();
  let call;
  const args = ["-m", "pytest", "backend/tests", "-q", "-k", "two words"];
  assert.equal(
    runPython(args, {
      spawnProcess: (...values) => {
        call = values;
        return child;
      },
    }),
    child,
  );
  assert.deepEqual(call[1], args);
  assert.equal(call[2].shell, false);
  assert.equal(call[2].stdio, "inherit");
  assert.equal(path.isAbsolute(call[2].cwd), true);
});

test("reports missing Python clearly and propagates failing exit codes", () => {
  const previous = process.exitCode;
  const previousError = console.error;
  const messages = [];
  console.error = (message) => messages.push(message);
  try {
    const child = new EventEmitter();
    runPython([], { spawnProcess: () => child });
    child.emit("error", new Error("ENOENT"));
    assert.equal(process.exitCode, 1);
    assert.match(messages[0], /Python could not start/);
    assert.doesNotMatch(messages[0], /ENOENT/);
    child.emit("exit", 2);
    assert.equal(process.exitCode, 2);
  } finally {
    console.error = previousError;
    process.exitCode = previous;
  }
});
