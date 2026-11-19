import { runPython } from "./python-command.mjs";
import { backendPort } from "./dev-config.mjs";

runPython([
  "-m",
  "uvicorn",
  "backend.main:app",
  "--host",
  "127.0.0.1",
  "--port",
  String(backendPort()),
  "--reload",
  "--timeout-graceful-shutdown",
  "5",
]);
