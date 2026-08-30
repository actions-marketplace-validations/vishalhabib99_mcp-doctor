import { z } from "zod";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

const server = new McpServer({ name: "weather", version: "1.0.0" });

const ForecastSchema = z.object({
  city: z.string().describe("The city name."),
  days: z.number().describe("How many days out."),
});

const forecastConfig = {
  title: "Get Forecast",
  description: "Get a weather forecast for a city.",
  inputSchema: ForecastSchema,
};

server.registerTool("get_forecast", forecastConfig, async (args) => {
  try {
    const validated = ForecastSchema.parse(args);
    return { content: [{ type: "text", text: `${validated.city} ${validated.days}` }] };
  } catch (e) {
    throw e;
  }
});
