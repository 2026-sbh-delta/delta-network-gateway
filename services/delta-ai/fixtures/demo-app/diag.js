const { exec } = require("child_process");
const { promisify } = require("util");

const run = promisify(exec);

async function runDiag(host) {
  const { stdout } = await run(`ping -c 1 ${host}`);
  return stdout;
}

module.exports = { runDiag };
