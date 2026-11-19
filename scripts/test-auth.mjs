import { runPython } from "./python-command.mjs";

runPython(["-m", "pytest", "backend/tests", "-q", ...process.argv.slice(2)]);
