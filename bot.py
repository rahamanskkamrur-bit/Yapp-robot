import asyncio, os, random, re, time, aiohttp
from aiohttp import web
import discord
from discord.ext import commands

# =====================================================================
# 1. CONFIGURATION & 24/7 RENDER SETTINGS
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()  # Optional: Uses built-in Yapper Engine if empty!
START_TIME = time.time()
BOT_STATUS = "Starting..."

# Stores conversation history per user so the bot remembers previous messages!
# Format: {user_id: [{"role": "user"|"bot", "text": "..."}, ...]}
USER_MEMORY = {}

# =====================================================================
# 2. 24/7 KEEP-ALIVE WEB SERVER & AUTO-PINGER
# =====================================================================
async def handle_ping(req):
    up = int((time.time() - START_TIME) // 60)
    return web.Response(text=f"Yapping Robot Bot 24/7 | Uptime: {up}m | Status: {BOT_STATUS}")

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
# 3. NATURAL HUMAN TEXTING ENGINE (CASUAL & FRIENDLY YAPPING)
# =====================================================================
RANDOM_YAP_TOPICS = [
    "ngl i was just sitting here bored scrolling through the server lol. what have you been up to today?",
    "lowkey today feels like it's flying by so fast. how's your day been going so far?",
    "wait i was literally just about to ask how everyone's doing in here. what are you up to rn?",
    "honestly i'm always down to chat, this channel was getting way too quiet anyway lol. how have you been lately?",
]

def smart_human_yapper(user_name: str, msg_text: str, history: list) -> str:
    t = msg_text.lower().strip()

    # 1. Just pinged the bot or said hi / hello / yo
    if not t or any(w in t for w in ["hi", "hello", "hey", "yo", "sup", "what's up", "whats up", "wassup", "wsg"]):
        greetings = [
            f"yooo {user_name}! what's up? how are you doing today?",
            f"hey {user_name}! good to see you around, how's your day going so far?",
            f"yo {user_name}! i was just chilling in here lol, how have you been doing?",
            f"heyyy {user_name}, what's good! how are things going with you today?",
        ]
        return random.choice(greetings)

    # 2. Player says they are doing good / great / fine / chilling
    if any(w in t for w in ["good", "great", "fine", "awesome", "amazing", "happy", "chill", "chilling", "alright", "not bad", "well", "solid"]):
        good_replies = [
            f"ayy love to hear that! honestly a chill day is the best kind of day. what have you been up to today, playing anything fun or just relaxing?",
            f"niceee glad you're doing good {user_name}! what's the plan for the rest of the day?",
            f"w mood honestly. have you been up to anything cool today or just taking it easy?",
            f"that's awesome to hear! anything exciting happen today or just a nice normal day?",
        ]
        return random.choice(good_replies)

    # 3. Player says they are tired / sleepy / bored
    if any(w in t for w in ["tired", "sleepy", "exhausted", "bored", "boring", "long day", "drained", "dead"]):
        tired_replies = [
            f"nah i feel that 100%, being tired and bored is the worst combo lol. did you barely get any sleep or was today just super long?",
            f"bro same, today has that sleepy vibe fr. you planning on taking a nap soon or powering through it?",
            f"oof sounds like a long day. if you're bored we can just yap in here to pass the time lol, what usually wakes you up?",
        ]
        return random.choice(tired_replies)

    # 4. Player says they are doing bad / sad / stressed / annoyed
    if any(w in t for w in ["bad", "sad", "stressed", "annoyed", "mad", "upset", "terrible", "awful", "rough", "not good", "cooked"]):
        sad_replies = [
            f"damn i'm sorry to hear that {user_name} :/ rough days seriously suck. you wanna talk/vent about what happened, or rather talk about something random to take your mind off it?",
            f"aw man, hope things start looking up for you soon fr. what's been stressing you out today?",
            f"ugh i hate days like that. hang in there though—if you wanna rant about it i'm all ears, what happened?",
        ]
        return random.choice(sad_replies)

    # 5. Player asks how the bot is doing
    if any(w in t for w in ["how are you", "how r u", "hru", "how about you", "wbu", "and you", "how are u", "hbu"]):
        bot_status_replies = [
            f"i'm doing pretty good honestly, thanks for asking! just hanging out in chat talking to people. what are you up to right now?",
            f"honestly can't complain at all! just chilling in the server waiting for someone cool to talk to lol. how's your week been going?",
            f"doing great ngl! always better when chat is active. you got anything fun planned for today?",
        ]
        return random.choice(bot_status_replies)

    # 6. Gaming / Roblox / Server topics
    if any(w in t for w in ["roblox", "game", "playing", "patrol", "ucpd", "grind", "rank", "shift", "coding", "bot"]):
        game_replies = [
            f"ooh nice, how's the grind going on that? have you been playing a lot lately?",
            f"wait that's actually sick lol, what's your favorite part about it so far?",
            f"ayy nice! i swear gaming/working on server stuff makes time fly by so fast. how long have you been at it today?",
        ]
        return random.choice(game_replies)

    # 7. Ongoing casual follow-up yapping
    general_followups = [
        f"wait fr?? no way lol, tell me more about that—what happened next?",
        f"nah that's actually so real {user_name} 😭 what else have you been up to today?",
        f"haha fair enough honestly! wait so what's the #1 thing you're looking forward to this week?",
        f"ohh i see what you mean! lowkey it's always fun yapping with you in here. what else is on your mind?",
        random.choice(RANDOM_YAP_TOPICS),
    ]
    return random.choice(general_followups)

# =====================================================================
# 4. AI + FALLBACK GENERATOR WITH MEMORY
# =====================================================================
async def generate_yap_reply(user_id: int, user_name: str, msg_text: str) -> str:
    history = USER_MEMORY.setdefault(user_id, [])
    history.append({"role": "user", "text": msg_text})
    if len(history) > 12:
        history[:] = history[-12:]

    if GEMINI_API_KEY:
        sys_inst = (
            "You are a chill, talkative, friendly human-like person hanging out in a Discord server. "
            "Do NOT sound like a robot or an AI assistant. Text casually like a real teenager/young adult on Discord "
            "(using natural lowercase or casual punctuation, words like 'ngl', 'lowkey', 'fr', 'lol', 'bro', 'yo' naturally without overdoing it). "
            "Ask how they are doing, care about their day, react naturally to what they tell you, and keep the conversation going "
            "by asking a casual follow-up question. Keep replies to 1-3 short, natural sentences like normal texting."
        )
        convo_str = "\n".join(f"{user_name if m['role'] == 'user' else 'You'}: {m['text']}" for m in history)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"parts": [{"text": convo_str}]}],
            "systemInstruction": {"parts": [{"text": sys_inst}]},
            "generationConfig": {"temperature": 0.9},
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload, timeout=12) as r:
                    if r.status == 200:
                        data = await r.json()
                        ai_reply = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        history.append({"role": "bot", "text": ai_reply})
                        return ai_reply
        except Exception:
            pass

    fallback = smart_human_yapper(user_name, msg_text, history)
    history.append({"role": "bot", "text": fallback})
    return fallback

# =====================================================================
# 5. BOT CORE & PING / REPLY DETECTOR (NORMAL HUMAN TEXTING)
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
            activity=discord.Game(name="hanging out in chat"),
        )
        print(f"✅ Yapping Bot ONLINE as {self.user}")

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        await self.process_commands(message)

        # Trigger whenever someone pings the bot OR replies to the bot's message
        is_pinged = self.user in message.mentions
        is_reply_to_bot = (
            message.reference
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author.id == self.user.id
        )

        if not (is_pinged or is_reply_to_bot):
            return

        # Remove the <@bot_id> tag so we read what the player actually said
        clean_text = re.sub(rf"<@!?{self.user.id}>", "", message.content).strip()

        # Simulate natural human typing speed before sending plain text
        async with message.channel.typing():
            reply_text = await generate_yap_reply(message.author.id, message.author.display_name, clean_text)
            typing_delay = min(3.0, max(1.0, len(reply_text) * 0.025))
            await asyncio.sleep(typing_delay)

        # Replies with a normal text message just like a real person in Discord!
        await message.reply(reply_text, mention_author=True)

def reg_cmds(b: YappingBot):
    @b.command(name="yap")
    async def cmd_yap(ctx: commands.Context, *, message: str = ""):
        async with ctx.channel.typing():
            reply_text = await generate_yap_reply(ctx.author.id, ctx.author.display_name, message)
            await asyncio.sleep(1.2)
        await ctx.reply(reply_text, mention_author=True)

    @b.command(name="resetconvo")
    async def cmd_reset(ctx: commands.Context):
        USER_MEMORY.pop(ctx.author.id, None)
        await ctx.reply("alright bet, fresh start! what's up, how are you doing?", mention_author=True)

    @b.tree.command(name="yap", description="Start chatting with the bot!")
    async def slash_yap(interaction: discord.Interaction, message: str = "yo what's up!"):
        await interaction.response.defer()
        reply_text = await generate_yap_reply(interaction.user.id, interaction.user.display_name, message)
        await interaction.followup.send(f"{interaction.user.mention} {reply_text}")

async def main():
    await run_web_server()
    asyncio.create_task(self_ping_loop())
    if not BOT_TOKEN:
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
