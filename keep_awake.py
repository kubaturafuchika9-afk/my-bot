import asyncio
import aiohttp

async def ping():
    url = "https://твой-сервис.onrender.com"
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url) as resp:
                    print("ping", resp.status)
            except:
                pass
            await asyncio.sleep(300)  # 5 минут

asyncio.run(ping())
