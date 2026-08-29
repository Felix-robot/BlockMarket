// Directly runnable Node.js reference for the TypeScript starter Bot.
import readline from "node:readline";

function decide(observation) {
  const toCents = (value) => Math.round(Number(value) * 100);
  const fromCents = (value) => (value / 100).toFixed(2);
  const reference = toCents(observation.reference_price);
  const [lower, upper] = observation.limits.price_range.map(toCents);
  const clamp = (value) => Math.max(lower, Math.min(value, upper));
  let bid = { price: fromCents(clamp(reference - 45)), quantity: "7" };
  let ask = { price: fromCents(clamp(reference + 45)), quantity: "7" };
  if (observation.market_signal === "UP") ask = null;
  else bid = null;
  return { decision_seq: observation.block_seq, bid, ask };
}

let initialized = false;
const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  const message = JSON.parse(line);
  if (message.type === "init") {
    if (message.protocol !== "blockmarket-jsonl-v1") throw new Error("unsupported_protocol");
    initialized = true;
  } else if (message.type === "decision") {
    if (!initialized) throw new Error("missing_init");
    process.stdout.write(`${JSON.stringify(decide(message.observation))}\n`);
  } else if (message.type === "end") {
    break;
  }
}
