import { z } from "zod";

server.tool(
  "do_thing",
  "",
  { x: z.string(), y: z.number() },
  async (args) => {
    return { content: [{ type: "text", text: "ok" }] };
  }
);

server.registerTool("run", { description: "Run", inputSchema: z.object({ cmd: z.string() }) }, async (args) => {
  return { content: [{ type: "text", text: args.cmd }] };
});
