from mcp.server.fastmcp import FastMCP
from tools.weather import register_weather_tools
from tools.route import register_route_tools
import os
import logging
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from mcp.server.sse import SseServerTransport

# SSE 配置
sse = SseServerTransport("/messages/")
async def handle_sse(request):
    """处理 /sse 路由的 SSE 连接并接入 MCP Server"""
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await mcp.run(streams[0], streams[1], mcp.create_initialization_options())
# 定义 Starlette 路由
starlette_app = Starlette(
    debug=True,
    routes=[
        Route("/sse", endpoint=handle_sse),  # SSE 路由
        Mount("/messages/", app=sse.handle_post_message),
    ]
)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("MCP_Server")

mcp = FastMCP("AggregateMCP")

register_weather_tools(mcp)
register_route_tools(mcp)

if __name__ == "__main__":
    mcp.run(transport="sse")

