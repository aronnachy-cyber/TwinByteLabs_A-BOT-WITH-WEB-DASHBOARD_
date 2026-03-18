import discord

async def log_event(bot, config, title, description, color=0x3498db):
    log_channel_id = config.get("log_channel", "").strip()
    if not log_channel_id:
        return
    try:
        channel = bot.get_channel(int(log_channel_id))
        if channel:
            embed = discord.Embed(title=title, description=description, color=color)
            await channel.send(embed=embed)
    except Exception as e:
        print(f"Log event error: {e}")
