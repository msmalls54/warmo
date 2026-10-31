// Explicit launch only. Defaults to SIM and requires genuine Terse memory.
import { pathToFileURL } from "node:url";
const runtimeDemo = String.raw`C:\Users\Small\hw-test\warmo-desktop-runtime\manufact-v2\demo.mjs`;
try {
  const { main } = await import(pathToFileURL(runtimeDemo).href);
  await main(process.argv.slice(2));
} catch (error) {
  console.error(`WARMO demo stopped: ${error.message}`);
  process.exitCode = 1;
}
