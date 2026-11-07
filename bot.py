import asyncio
import json
import os
import re
import time
import urllib.parse
import aiohttp
from aiohttp import web
import discord
from discord.ext import commands

# =====================================================================
# 1. CONFIGURATION & 24/7 RENDER SETTINGS
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
# Optional: Works out-of-the-box with built-in Free AI even if GEMINI_API_KEY is left blank!
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
START_TIME = time.time()
BOT_STATUS = "Starting..."

# Stores conversation history per user so the AI remembers your full chat!
USER_MEMORY = {}
# Stores each user's current AI mode: "human" (default), "roast", or "genius"
USER_MODE = {}


# =====================================================================
# 2. 24/7 KEEP-ALIVE WEB SERVER & AUTO-PINGER (FOR RENDER)
# =====================================================================
async def handle_ping(req):
    up = int((time.time() - START_TIME) // 60)
    return web.Response(text=f"Yapping AI Bot 24/7 | Uptime: {up}m | Status: {BOT_STATUS}")


async def run_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, "0.0.0.0", port).start()


async def self_ping_loop():
    await asyncio.sleep(20)
    url = os.environ.get("RENDER_EXTERNAL_URL") or f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/"
    async with aiohttp.ClientSession() as s:
        while True:
            try:
                async with s.get(url, timeout=10):
                    pass
            except Exception:
                pass
            await asyncio.sleep(240)


# =====================================================================
# 3. FULL AI BRAIN (MULTI-ENGINE LLM — ANSWERS & DOES EVERYTHING)
# =====================================================================
def build_system_prompt(user_name: str, mode: str) -> str:
    base = (
        f"You are a super smart, funny, human-like AI friend chatting with {user_name} in a Discord server. "
        "You can do EVERYTHING a real AI and human friend can do:\n"
        "- Have natural conversations, ask how they are doing, and react to their exact words.\n"
        "- Do savage, funny 1v1 roast battles whenever they say 'roast me', 'let's do roast', or insult you.\n"
        "- Tell them what to do when they are bored, and play games (Truth or Dare, Trivia, 20 Questions, Riddles, Roleplay).\n"
        "- Answer ANY question accurately (coding, Python, Roblox Luau scripts, math, advice, gaming, general knowledge).\n"
        "STYLE RULES:\n"
        "- Text naturally like a real person on Discord (casual tone, 1 to 4 sentences unless they ask for code or a detailed explanation).\n"
        "- NEVER use generic filler like 'that's so real'. Always reply directly to what they just said!"
    )
    if mode == "roast":
        base += (
            f"\nCURRENT MODE: ROAST BATTLE MODE. Roast {user_name} creatively and hilariously "
            "based on what they just said, and challenge them to fire back!"
        )
    elif mode == "genius":
        base += (
            "\nCURRENT MODE: GENIUS AI HELPER. Give detailed, super helpful, accurate answers, "
            "working code, or step-by-step explanations."
        )
    return base


async def query_free_ai_post(messages_payload: list) -> str:
    """Primary Free Keyless LLM Endpoint (Handles both JSON and plain text responses)."""
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 DiscordAIBot/3.0"}
    payload = {
        "messages": messages_payload,
        "model": "openai",
        "temperature": 0.85,
        "private": True,
    }
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.post("https://text.pollinations.ai/", json=payload, timeout=15) as r:
            if r.status == 200:
                text = (await r.text()).strip()
                if text and not text.startswith("<!DOCTYPE"):
                    if text.startswith("{"):
                        try:
                            d = json.loads(text)
                            return d["choices"][0]["message"]["content"].strip()
                        except Exception:
                            pass
                    return text
    return ""


async def query_free_ai_get(sys_inst: str, history: list, user_name: str) -> str:
    """Backup Free Keyless LLM GET Endpoint (99.9% uptime fallback)."""
    convo_lines = [f"{user_name if m['role'] == 'user' else 'AI'}: {m['text']}" for m in history[-6:]]
    full_prompt = "\n".join(convo_lines) + "\nAI:"
    enc_prompt = urllib.parse.quote(full_prompt)
    enc_sys = urllib.parse.quote(sys_inst)
    url = f"https://text.pollinations.ai/{enc_prompt}?model=openai&system={enc_sys}"
    headers = {"User-Agent": "Mozilla/5.0 DiscordAIBot/3.0"}
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.get(url, timeout=15) as r:
            if r.status == 200:
                text = (await r.text()).strip()
                if text and not text.startswith("<!DOCTYPE"):
                    return text
    return ""


async def generate_yap_reply(user_id: int, user_name: str, msg_text: str) -> str:
    history = USER_MEMORY.setdefault(user_id, [])
    clean_input = msg_text.strip() if msg_text.strip() else "hey! how are you doing?"

    # Auto-detect mode switches from natural language
    low = clean_input.lower()
    if any(w in low for w in ["let's do roast", "lets do roast", "roast battle", "roast me", "flame me", "cook me"]):
        USER_MODE[user_id] = "roast"
    elif any(w in low for w in ["stop roast", "chill mode", "normal mode", "truce"]):
        USER_MODE[user_id] = "human"

    mode = USER_MODE.get(user_id, "human")
    sys_inst = build_system_prompt(user_name, mode)

    history.append({"role": "user", "text": clean_input})
    if len(history) > 16:
        history[:] = history[-16:]

    # 1. Try Official Gemini API if GEMINI_API_KEY is configured
    if GEMINI_API_KEY:
        convo_str = "\n".join(f"{user_name if m['role'] == 'user' else 'You'}: {m['text']}" for m in history)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": convo_str}]}],
            "systemInstruction": {"parts": [{"text": sys_inst}]},
            "generationConfig": {"temperature": 0.85},
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload, timeout=12) as r:
                    if r.status == 200:
                        data = await r.json()
                        ai_reply = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        if ai_reply:
                            history.append({"role": "bot", "text": ai_reply})
                            return ai_reply
        except Exception:
            pass

    # 2. Primary Free AI (POST with full conversation history)
    messages_payload = [{"role": "system", "content": sys_inst}]
    for m in history[-10:]:
        messages_payload.append({
            "role": "user" if m["role"] == "user" else "assistant",
            "content": m["text"],
        })
    try:
        reply = await query_free_ai_post(messages_payload)
        if reply:
            history.append({"role": "bot", "text": reply})
            return reply
    except Exception:
        pass

    # 3. Secondary Free AI (GET endpoint with conversation context)
    try:
        reply = await query_free_ai_get(sys_inst, history, user_name)
        if reply:
            history.append({"role": "bot", "text": reply})
            return reply
    except Exception:
        pass

    # 4. Emergency offline response if all internet AI endpoints are unreachable
    fallback = (
        f"yo {user_name}, my AI connection lagged for a sec—say that one more time? "
        "we can do a roast battle, play a game, or talk about whatever you want!"
    )
    history.append({"role": "bot", "text": fallback})
    return fallback


# =====================================================================
# 4. BOT CORE & PING / REPLY DETECTOR (FULL AI HUMAN TEXTING)
# =====================================================================
class YappingBot(commands.Bot):
    def __init__(self, msg_intent: bool = True):
        intents = discord.Intents.default()
        intents.message_content = msg_intent
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        await self.tree.sync()

    async def on_ready(self):
        global BOT_STATUS
        BOT_STATUS = f"ONLINE as {self.user}"
        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Game(name="Ping me to chat or roast!"),
        )
        print(f"✅ Full AI Yapping Bot ONLINE as {self.user}")

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        await self.process_commands(message)

        if message.content.startswith("!"):
            return

        is_pinged = self.user in message.mentions
        is_reply_to_bot = (
            message.reference
            and message.reference.resolved
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author.id == self.user.id
        )

        if not (is_pinged or is_reply_to_bot):
            return

        # Strip the @bot mention tag so the AI reads the clean message
        clean_text = re.sub(rf"<@!?{self.user.id}>", "", message.content).strip()

        # Simulate natural human typing before sending plain text
        async with message.channel.typing():
            reply_text = await generate_yap_reply(
                message.author.id,
                message.author.display_name,
                clean_text,
            )

        # Split long AI responses (like code or stories) if over 1950 characters
        for i in range(0, len(reply_text), 1950):
            chunk = reply_text[i:i + 1950]
            await message.reply(chunk, mention_author=(i == 0))


# =====================================================================
# 5. COMMANDS (!yap, !mode, !resetconvo, /yap)
# =====================================================================
def reg_cmds(b: YappingBot):
    @b.command(name="yap", aliases=["ai", "ask"])
    async def cmd_yap(ctx: commands.Context, *, message: str = "hey! how are you doing?"):
        async with ctx.channel.typing():
            reply_text = await generate_yap_reply(ctx.author.id, ctx.author.display_name, message)
        for i in range(0, len(reply_text), 1950):
            await ctx.reply(reply_text[i:i + 1950], mention_author=(i == 0))

    @b.command(name="mode")
    async def cmd_mode(ctx: commands.Context, mode_name: str = "human"):
        m = mode_name.lower().strip()
        if m not in ("human", "roast", "genius"):
            return await ctx.reply(
                "pick a mode: `!mode human`, `!mode roast`, or `!mode genius`!",
                mention_author=True,
            )
        USER_MODE[ctx.author.id] = m
        await ctx.reply(
            f"bet, switched my AI brain to **{m}** mode for you! what's the move?",
            mention_author=True,
        )

    @b.command(name="resetconvo")
    async def cmd_reset(ctx: commands.Context):
        USER_MEMORY.pop(ctx.author.id, None)
        USER_MODE.pop(ctx.author.id, None)
        await ctx.reply(
            "alright bet, wiped my memory for a fresh start! how are you doing today?",
            mention_author=True,
        )

    @b.tree.command(name="yap", description="Talk to the AI about anything (chat, roast battle, questions, code)")
    async def slash_yap(interaction: discord.Interaction, message: str):
        await interaction.response.defer()
        reply_text = await generate_yap_reply(interaction.user.id, interaction.user.display_name, message)
        await interaction.followup.send(f"{interaction.user.mention} {reply_text[:1950]}")


# =====================================================================
# 6. MAIN ENTRYPOINT
# =====================================================================
async def main():
    await run_web_server()
    asyncio.create_task(self_ping_loop())
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN environment variable is missing!")
        while True:
            await asyncio.sleep(3600)

    bot = YappingBot(True)
    reg_cmds(bot)
    try:
        await bot.start(BOT_TOKEN)
    except discord.PrivilegedIntentsRequired:
        await bot.close()
        bot = YappingBot(False)
        reg_cmds(bot)
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
