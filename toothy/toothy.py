import datetime
import json
import logging
import sys

import aiohttp
import discord
from discord.ext import commands

from cogs.utils import context

from .database import MongoController

log = logging.getLogger(__name__)

with open("settings/config.json", encoding="utf-8", mode="r") as f:
    data = json.load(f)
    TOKEN = data["TOKEN"]
    DESCRIPTION = data["DESCRIPTION"]
    SELFBOT = data["SELFBOT"]
    OWNER_ID = data["OWNER_ID"]
    DB_SETTINGS = data["DATABASE"]
    CASE_INSENSITIVE = data["CASE_INSENSITIVE_COMMANDS"]
    COLOR = int(data["COLOR"], 16)

if not TOKEN:
    print("Token not set in config.json")
    sys.exit(1)

if SELFBOT and not OWNER_ID:
    print("You must specify owner id in order to use Selfbot mode")
    sys.exit(1)

if SELFBOT:
    print("By using selfbot mode, you put your account at risk of getting "
          "banned. Use at your own risk. You have been warned.\n" * 3)
    to_proceed = input('To proceed type "I understand"\n')
    if to_proceed.lower() != "i understand":
        print("Bot will now exit")
        sys.exit(1)


class Toothy(commands.AutoShardedBot):
    def __init__(self):
        async def prefix_callable(bot, message):
            prefix = await self.database.get_prefixes(message.guild)
            if not prefix:
                prefix = self.global_prefixes
            return prefix + [
                "<@!{.user.id}> ".format(self), self.user.mention + " "
            ]

        super().__init__(
            command_prefix=prefix_callable,
            description=DESCRIPTION,
            pm_help=None if not SELFBOT else False,
            self_bot=SELFBOT,
            owner_id=OWNER_ID,
            case_insensitive=CASE_INSENSITIVE)
        self.database = MongoController(self, DB_SETTINGS)
        self.available = True
        self.global_prefixes = data["PREFIXES"]
        self.uptime = datetime.datetime.utcnow()
        self.color = discord.Color(COLOR)
        self.session = aiohttp.ClientSession(loop=self.loop)
        self.avatar_file = None

    async def on_ready(self):
        self.uptime = datetime.datetime.utcnow()
        #        async with aiohttp.ClientSession() as session:
        #            async with session.get(self.user.avatar_url_as(format="png")) as r:
        #                self.avatar_file = await r.read()
        with open("settings/extensions.json", encoding="utf-8", mode="r") as f:
            extensions = json.load(f)
        for name, state in extensions.items():
            if state:
                try:
                    self.load_extension(name)
                except Exception as e:
                    print("{}: {}".format(e.__class__.__name__, str(e)))
                    print("Failed to load {}".format(name))
                    state = False
        global_cog = self.get_cog("Global")
        if not global_cog:
            print("GLobal cog not loaded, exiting")
            sys.exit(1)
        await self.disable_commands(global_cog)
        with open("settings/extensions.json", encoding="utf-8", mode="w") as f:
            f.write(json.dumps(extensions, indent=4, sort_keys=True))
        print("Toothy ready")
        print("Serving {} guilds".format(len(self.guilds)))

    async def get_disabled_commands(self, global_cog):
        config = await self.database.get_cog_config(global_cog)
        if config:
            return config.get("disabled_commands", [])
        return []

    def disable_command(self, cmd_obj):
        if cmd_obj:
            cmd_obj.hidden = True
            cmd_obj.enabled = False

    async def disable_commands(self, global_cog):
        disabled = await self.get_disabled_commands(global_cog)
        for cmd in disabled:
            obj = self.get_command(cmd)
            self.disable_command(obj)

    async def on_message(self, message):
        user = message.author
        if user.bot:
            return
        if not self.available and user.id != self.owner_id:
            return
        if await self.user_is_ignored(user):
            return
        if isinstance(message.channel, discord.abc.GuildChannel):
            if await self.guild_is_ignored(message.guild):
                return
        await self.process_commands(message)

    async def process_commands(self, message):
        ctx = await self.get_context(message, cls=context.ToothyContext)
        if not ctx.valid:
            return
        await self.invoke(ctx)

    async def on_command_error(self, ctx, exc):
        if isinstance(exc, commands.NoPrivateMessage):
            await ctx.send("This command cannot be used in DMs")
            ctx.command.reset_cooldown(ctx)
        elif isinstance(exc, commands.CommandOnCooldown):
            if await ctx.bot.database.get_flag(ctx.author, "vip"):
                await ctx.reinvoke()
            else:
                await ctx.send(
                    "You cannot use this command again for the next "
                    "{:.2f} seconds"
                    "".format(exc.retry_after))
        elif isinstance(
                exc, (commands.MissingRequiredArgument, commands.BadArgument)):
            await ctx.send_help(ctx.command)
            ctx.command.reset_cooldown(ctx)
        elif isinstance(exc, commands.DisabledCommand):
            await ctx.send("This command is disabled")
        elif isinstance(exc, commands.CommandInvokeError):
            message = ("Something went wrong. If the issue persists, please "
                       "contact the author. ")
            await ctx.send(message)
            log.exception(
                "Exception in command " + ctx.command.qualified_name,
                exc_info=exc.original)
        elif isinstance(exc, commands.MissingPermissions):
            missing = [
                p.replace("guild", "server").replace("_", " ").title()
                for p in exc.missing_perms
            ]
            await ctx.send(
                "You're missing the following permissions to use this "
                "command: `{}`".format(", ".join(missing)))
        elif isinstance(exc, commands.CommandNotFound):
            pass
        elif isinstance(exc, commands.CheckFailure):
            pass

    async def user_is_ignored(self, user):
        doc = await self.database.users.find_one({
            "_id": user.id
        }, {"blacklisted": 1})
        if not doc:
            return False
        return doc.get("blacklisted", False)

    async def guild_is_ignored(self, guild):
        doc = await self.database.guilds.find_one({
            "_id": guild.id
        }, {"blacklisted": 1})
        if not doc:
            return False
        return doc.get("blacklisted", False)

    async def send_cmd_help(self, ctx):  # lol
        return ctx.send_help(ctx.command)

    async def close(self):
        await super().close()
        await self.session.close()

    def save_config(self):
        with open("settings/config.json", encoding="utf-8", mode="r") as f:
            data = json.load(f)
        data["DESCRIPTION"] = DESCRIPTION,
        data["PREFIXES"] = self.global_prefixes
        with open("settings/config.json", encoding="utf-8", mode="w") as f:
            f.write(json.dumps(data, indent=4, sort_keys=True))

    def run(self):
        super().run(TOKEN, bot=not SELFBOT)
