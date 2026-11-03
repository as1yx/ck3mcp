"""Start real stdio server, initialize and list tools; no binding or game input."""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
    params = StdioServerParameters(command=sys.executable,
        args=['-B', '-m', 'ck3mcp.server', '--config', str(ROOT / 'config.example.json')], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            result = await session.initialize()
            print('Initialized:', result.serverInfo.name)
            names = {t.name for t in (await session.list_tools()).tools}
            expected = {'bind_game', 'get_game_state', 'screenshot', 'execute_action', 'verify_action', 'verify_task', 'get_run_status', 'emergency_stop'}
            assert names == expected, names
            print('Tools:', ', '.join(sorted(names)))
            result = await session.call_tool('get_run_status', {})
            assert result.isError, 'Unbound access must fail'
            print('Unbound status access correctly rejected; MCP smoke OK')

asyncio.run(main())
