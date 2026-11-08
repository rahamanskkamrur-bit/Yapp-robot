import asyncio
import json
import os
import random
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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
START_TIME = time.time()
BOT_STATUS = "Starting..."

# Conversation history per user: {user_id: [{"role": "user"|"assistant", "content": str}]}
USER_MEMORY = {}
# Active mode per user: "human" (default), "roast", or "genius"
USER_MODE = {}
# Tracks recently used local replies per user so the bot NEVER repeats itself
USED_LOCAL_REPLIES = {}
# Tracks active trivia/riddle answers per user
ACTIVE_PUZZLE = {}


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
# 3. SYSTEM PROMPT & STRICT AI RESPONSE VALIDATOR
# =====================================================================
def build_system_prompt(user_name: str, mode: str) -> str:
    base = (
        f"You are a witty, smart, human-like AI friend texting with {user_name} on Discord.\n"
        "CRITICAL RULES:\n"
        "1. NEVER send a generic introduction message like 'Hey! How can I help you today?' or 'I am an AI assistant'.\n"
        "2. Directly answer or react to the exact message the user just sent.\n"
        "3. Keep your tone natural, casual, and human-like (1-4 sentences unless writing code or explaining a topic).\n"
        "4. If they ask to do a roast battle ('let's do roast', 'roast me') or insult you, roast them back playfully and creatively.\n"
        "5. If they ask 'what should I do' or say they are bored, suggest specific fun activities, games, or challenges right now.\n"
        "6. Answer any question (coding, Roblox Luau, Python, math, gaming, advice) accurately."
    )
    if mode == "roast":
        base += (
            f"\nCURRENT MODE: 1v1 ROAST BATTLE MODE with {user_name}. "
            "Deliver a funny, clever, savage roast directly targeting what they just said, and dare them to comeback!"
        )
    elif mode == "genius":
        base += (
            "\nCURRENT MODE: GENIUS HELPER MODE. Give clear, accurate, detailed answers and working code."
        )
    return base


def is_valid_ai_response(text: str, user_input: str) -> bool:
    """Filters out API error messages, rate-limit notices, HTML, or unwanted bot intros."""
    if not text or len(text.strip()) < 2:
        return False
    t = text.strip()
    low = t.lower()

    # Reject HTML or raw error payloads
    if low.startswith(("<!doctype", "<html", "{\"error", "error:", "rate limit", "free limit reached")):
        return False

    # Reject provider notices or rate-limit banners
    bad_markers = [
        "free limit reached",
        "1 reply every 30 seconds",
        "pollinations.ai",
        "enter.pollinations.ai",
        "too many requests",
        "402 payment required",
        "502 bad gateway",
        "504 gateway time-out",
        "internal server error",
    ]
    if any(m in low for m in bad_markers):
        return False

    # If the user asked a real question or roast, reject generic canned "how can I help you" intros
    u_low = user_input.lower().strip()
    if u_low not in ("hi", "hey", "hello", "yo", "sup") and len(u_low) > 6:
        intro_phrases = [
            "hello! how can i help you today",
            "hi there! how can i assist you",
            "hey! how are you doing today? what's on your mind",
        ]
        if any(low.startswith(p) for p in intro_phrases):
            return False

    return True


def extract_clean_text(raw_text: str) -> str:
    """Extracts the assistant message cleanly whether the API returned JSON or plain text."""
    text = raw_text.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if "choices" in data and data["choices"]:
                msg = data["choices"][0].get("message") or {}
                content = msg.get("content") or data["choices"][0].get("text") or ""
                if content:
                    return content.strip()
            if "response" in data and isinstance(data["response"], str):
                return data["response"].strip()
        except Exception:
            pass
    return text


# =====================================================================
# 4. MULTI-PROVIDER AI REQUEST ENGINE (GEMINI / GROQ / LLM7 / POLLINATIONS)
# =====================================================================
async def call_gemini_api(sys_inst: str, history: list, user_name: str) -> str:
    if not GEMINI_API_KEY:
        return ""
    convo = "\n".join(
        f"{user_name if m['role'] == 'user' else 'You'}: {m['content']}" for m in history[-12:]
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": f"{convo}\nYou:"}]}],
        "systemInstruction": {"parts": [{"text": sys_inst}]},
        "generationConfig": {"temperature": 0.85},
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, timeout=12) as r:
                if r.status == 200:
                    d = await r.json()
                    return d["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        pass
    return ""


async def call_groq_api(messages_payload: list) -> str:
    if not GROQ_API_KEY:
        return ""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages_payload,
        "temperature": 0.85,
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.post("https://api.groq.com/openai/v1/chat/completions", json=payload, timeout=12) as r:
                if r.status == 200:
                    d = await r.json()
                    return d["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return ""


async def call_llm7_free_api(messages_payload: list) -> str:
    """Free Keyless OpenAI-compatible LLM Gateway (No 30s IP lock)."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer unused",
        "User-Agent": "Mozilla/5.0 DiscordCompanionBot/4.0",
    }
    for model_name in ("gpt-4o-mini", "default"):
        payload = {
            "model": model_name,
            "messages": messages_payload,
            "temperature": 0.85,
        }
        try:
            async with aiohttp.ClientSession(headers=headers) as s:
                async with s.post("https://api.llm7.io/v1/chat/completions", json=payload, timeout=12) as r:
                    if r.status == 200:
                        raw = await r.text()
                        cleaned = extract_clean_text(raw)
                        if cleaned:
                            return cleaned
        except Exception:
            pass
    return ""


async def call_pollinations_openai(messages_payload: list) -> str:
    """Pollinations OpenAI-compatible POST endpoint (/openai)."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 DiscordCompanionBot/4.0",
    }
    payload = {
        "model": "openai",
        "messages": messages_payload,
        "temperature": 0.85,
        "seed": random.randint(1, 999999),
        "private": True,
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.post("https://text.pollinations.ai/openai", json=payload, timeout=14) as r:
                if r.status == 200:
                    raw = await r.text()
                    return extract_clean_text(raw)
    except Exception:
        pass
    return ""


# =====================================================================
# 5. BUILT-IN SMART BRAIN (INSTANT ROASTS, GAMES, ADVICE & CONVERSATION)
# =====================================================================
ROAST_PACK = [
    "bro {name}, you asked for a roast like your Wi-Fi router doesn't already struggle to carry your gameplay 💀 fire back, let's see what you got!",
    "{name} really said 'let's do roast' with the confidence of an NPC walking into a wall 😂 hit me with your best shot!",
    "if brains were dynamite, {name}, you wouldn't have enough to blow your nose 🔥 your turn, try to top that!",
    "you're like the terms and conditions, {name}—nobody pays attention to what you're saying, they just scroll past 😭 roast me back if you can!",
    "bro your comebacks take longer to load than a 2009 Roblox server on dial-up 💀 whatchu got {name}?",
    "i'd roast you harder {name}, but my code says I'm not allowed to bully beginners 😂 drop your best roast right now!",
    "{name} walked into the chat acting like the main character when you're really just background props in tutorial level 1 💀 your turn!",
    "even my error logs make more sense than that weak energy {name} 🔥 come on, hit me with a real roast!",
    "bro if laziness was an Olympic sport, {name} would still come in fourth just to avoid walking up to the podium 😂 fire back!",
    "i ran a scan on your aura {name} and the results came back '404 skill not found' 💀 let's hear your comeback!",
]

WHAT_TO_DO_PACK = [
    "here's the move right now {name}:\n1️⃣ challenge me to a **1v1 roast battle** (just reply with your best insult)\n2️⃣ say **`truth`** or **`dare`** and let's play\n3️⃣ ask me for a **riddle** or **trivia** question\n4️⃣ or go hop into Roblox / work on a new bot feature! which one we doing?",
    "if you're bored {name}, let's run a quick game right here—type **`riddle`** for a brain teaser, **`dare`** for a chat dare, or try to roast me in one sentence and see if you survive 🔥",
    "honestly {name}, you have 3 solid options right now: build a crazy new command for the server, start an event in-game, or try to beat me in **Rock Paper Scissors** (just reply `rock`, `paper`, or `scissors`)!",
]

TRUTH_QUESTIONS = [
    "alright **TRUTH** time {name}: what's the most embarrassing thing you've ever done in a game or Discord server?",
    "**TRUTH** for {name}: if you had to ban one person in this server as a joke for 5 minutes, who would it be and why? 👀",
    "**TRUTH** {name}: what is the longest you've ever stayed awake gaming non-stop?",
    "**TRUTH** {name}: what's a game everyone loves that you secretly think is super overrated?",
]

DARE_CHALLENGES = [
    "alright **DARE** time {name}: i dare you to ping a random friend in this channel and compliment them with zero context 😂",
    "**DARE** for {name}: change your server nickname to `'Professional Yapper'` for the next 10 minutes!",
    "**DARE** {name}: type your next 3 messages in ALL CAPS like you're announcing a presidential emergency!",
    "**DARE** {name}: send the 5th emoji in your recently used emojis with no explanation 💀",
]

RIDDLES = [
    ("I speak without a mouth and hear without ears. I have no body, but I come alive with wind. What am I?", "echo"),
    ("The more of this there is, the less you see. What is it?", "darkness"),
    ("I have keys, but no locks. I have space, but no room. You can enter, but can't go outside. What am I?", "keyboard"),
    ("What has to be broken before you can use it?", "egg"),
]


def pick_unique(user_id: int, options: list, name: str) -> str:
    used = USED_LOCAL_REPLIES.setdefault(user_id, set())
    avail = [opt for opt in options if opt not in used]
    if not avail:
        used.clear()
        avail = list(options)
    choice = random.choice(avail)
    used.add(choice)
    return choice.format(name=name)


def smart_local_brain(user_id: int, user_name: str, msg_text: str, mode: str) -> str:
    """Instant, context-aware conversational engine that NEVER repeats intros or lags."""
    clean = msg_text.strip()
    low = clean.lower()

    # 1. Check if user is answering an active riddle
    if user_id in ACTIVE_PUZZLE:
        ans = ACTIVE_PUZZLE[user_id]
        if ans in low:
            ACTIVE_PUZZLE.pop(user_id, None)
            return f"yooo {user_name} you actually got it right!! 🎉 the answer was **{ans}**! want another one? just say `riddle`!"
        elif any(w in low for w in ["give up", "idk", "what is it", "answer"]):
            ACTIVE_PUZZLE.pop(user_id, None)
            return f"haha the answer was **{ans}**! say `riddle` if you want another shot or `roast me` to switch it up!"

    # 2. Roast Battle Mode or roast triggers
    if mode == "roast" or any(w in low for w in ["roast", "flame", "cook me", "insult", "trash", "noob", "bot", "weak", "ugly", "dumb", "stupid"]):
        USER_MODE[user_id] = "roast"
        return pick_unique(user_id, ROAST_PACK, user_name)

    # 3. "What should I do?" / Bored / Help me decide
    if any(w in low for w in ["what do i do", "what should i do", "im bored", "i'm bored", "bored", "tell me what to do", "what to do", "nothing to do"]):
        return pick_unique(user_id, WHAT_TO_DO_PACK, user_name)

    # 4. Truth or Dare
    if "truth or dare" in low:
        return f"let's go {user_name}! pick one right now—reply with **`truth`** or **`dare`**! 👀"
    if re.search(r"\btruth\b", low):
        return pick_unique(user_id, TRUTH_QUESTIONS, user_name)
    if re.search(r"\bdare\b", low):
        return pick_unique(user_id, DARE_CHALLENGES, user_name)

    # 5. Riddles
    if "riddle" in low or "quiz" in low or "trivia" in low:
        q, a = random.choice(RIDDLES)
        ACTIVE_PUZZLE[user_id] = a
        return f"🧠 **Riddle time for {user_name}:**\n> *{q}*\nReply with your guess (or say `give up`)!"

    # 6. Rock Paper Scissors
    rps_match = re.search(r"\b(rock|paper|scissors)\b", low)
    if rps_match:
        user_pick = rps_match.group(1)
        bot_pick = random.choice(["rock", "paper", "scissors"])
        if user_pick == bot_pick:
            res = "it's a tie! 🤝 run it back!"
        elif (user_pick == "rock" and bot_pick == "scissors") or (user_pick == "paper" and bot_pick == "rock") or (user_pick == "scissors" and bot_pick == "paper"):
            res = f"you won {user_name}! 🏆 lucky shot though!"
        else:
            res = f"I win {user_name}! 😎 better luck next round!"
        return f"you picked **{user_pick}**, I picked **{bot_pick}** — {res}"

    # 7. Math expressions (e.g. "what is 25 * 40")
    math_m = re.search(r"([-+]?\d+(?:\.\d+)?\s*[\+\-\*\/]\s*[-+]?\d+(?:\.\d+)?(?:\s*[\+\-\*\/]\s*[-+]?\d+(?:\.\d+)?)*)", low)
    if math_m:
        expr = math_m.group(1)
        try:
            val = eval(expr, {"__builtins__": None}, {})
            return f"easy math {user_name}: `{expr.strip()} = {val}` 🧠"
        except Exception:
            pass

    # 8. Mood / How are you doing check-ins
    if any(w in low for w in ["how are you", "how r u", "hru", "how you doing", "what's up", "whats up", "wsp", "sup"]):
        replies = [
            "doing awesome {name}! just vibing in the server and ready to yap. how's your day going so far?",
            "feeling 10/10 today {name}! what have you been up to? working on bots, gaming, or just chilling?",
            "all good on my end {name}! how are you holding up today—everything going smooth?",
        ]
        return pick_unique(user_id, replies, user_name)

    if any(w in low for w in ["im good", "i'm good", "doing good", "doing great", "im great", "fine", "awesome", "pretty good", "chilling"]):
        replies = [
            "love to hear that {name}! 🙌 what are you working on or playing today?",
            "ayy that's a W {name}! we can run a roast battle (`let's do roast`), play `truth or dare`, or talk about whatever you're up to!",
            "glad you're doing good {name}! anything hyped happening today or just a chill day?",
        ]
        return pick_unique(user_id, replies, user_name)

    if any(w in low for w in ["tired", "sleepy", "exhausted", "bad", "sad", "stressed", "annoyed", "rough"]):
        replies = [
            "aw man, sorry it's been rough {name} 💙 take a breather! want me to distract you with a roast battle, a riddle, or just vent about it?",
            "i feel that {name}, long days hit hard. grab some water and chill for a bit—what's been stressing you out?",
            "hang in there {name}! we can keep it super chill or play a quick game to take your mind off things. what sounds good?",
        ]
        return pick_unique(user_id, replies, user_name)

    # 9. Coding / Roblox / Bot Dev questions
    if any(w in low for w in ["python", "luau", "roblox", "script", "code", "bot", "discord.py"]):
        replies = [
            "oh nice, we talking dev work {name}? tell me what feature you're trying to script in Python or Roblox Luau and I'll help you build it!",
            "i love coding talk {name}! drop the exact command or script idea you're working on and let's cook it up.",
        ]
        return pick_unique(user_id, replies, user_name)

    # 10. Dynamic contextual reply that directly references their message
    contextual_replies = [
        f"Wait {user_name}, when you say \"{clean[:60]}\", what's the backstory on that? 👀",
        f"ngl {user_name}, \"{clean[:60]}\" got me curious—what made you think of that? also if you wanna switch it up we can do a roast battle or truth or dare!",
        f"haha fair point {user_name}! so what's the next move—are we roasting each other, playing a game, or cooking up a new project?",
        f"i hear you {user_name}! tell me more about that, or hit me with a question / roast and let's see what you got!",
    ]
    return pick_unique(user_id, contextual_replies, user_name)


# =====================================================================
# 6. MASTER RESPONSE GENERATOR
# =====================================================================
async def generate_yap_reply(user_id: int, user_name: str, msg_text: str) -> str:
    history = USER_MEMORY.setdefault(user_id, [])
    clean_input = msg_text.strip()

    # Check if user only pinged the bot with zero text
    if not clean_input:
        if not history:
            greeting = f"yooo {user_name}! how are you doing today? we can chat, do a 1v1 roast battle (`let's do roast`), or play a game!"
        else:
            greeting = f"yo {user_name}! what's on your mind? type a message with your ping so I can reply to it!"
        history.append({"role": "assistant", "content": greeting})
        return greeting

    low = clean_input.lower()
    if any(w in low for w in ["let's do roast", "lets do roast", "roast battle", "roast me", "flame me", "cook me"]):
        USER_MODE[user_id] = "roast"
        return pick_unique(user_id, ROAST_PACK, user_name)

    # 3. "What should I do?" / Bored / Help me decide
    if any(w in low for w in ["what do i do", "what should i do", "im bored", "i'm bored", "bored", "tell me what to do", "what to do", "nothing to do"]):
        return pick_unique(user_id, WHAT_TO_DO_PACK, user_name)

    # 4. Truth or Dare
    if "truth or dare" in low:
        return f"let's go {user_name}! pick one right now—reply with **`truth`** or **`dare`**! 👀"
    if re.search(r"\btruth\b", low):
        return pick_unique(user_id, TRUTH_QUESTIONS, user_name)
    if re.search(r"\bdare\b", low):
        return pick_unique(user_id, DARE_CHALLENGES, user_name)

    # 5. Riddles
    if "riddle" in low or "quiz" in low or "trivia" in low:
        q, a = random.choice(RIDDLES)
        ACTIVE_PUZZLE[user_id] = a
        return f"🧠 **Riddle time for {user_name}:**\n> *{q}*\nReply with your guess (or say `give up`)!"

    # 6. Rock Paper Scissors
    rps_match = re.search(r"\b(rock|paper|scissors)\b", low)
    if rps_match:
        user_pick = rps_match.group(1)
        bot_pick = random.choice(["rock", "paper", "scissors"])
        if user_pick == bot_pick:
            res = "it's a tie! 🤝 run it back!"
        elif (user_pick == "rock" and bot_pick == "scissors") or (user_pick == "paper" and bot_pick == "rock") or (user_pick == "scissors" and bot_pick == "paper"):
            res = f"you won {user_name}! 🏆 lucky shot though!"
        else:
            res = f"I win {user_name}! 😎 better luck next round!"
        return f"you picked **{user_pick}**, I picked **{bot_pick}** — {res}"

    # 7. Math expressions (e.g. "what is 25 * 40")
    math_m = re.search(r"([-+]?\d+(?:\.\d+)?\s*[\+\-\*\/]\s*[-+]?\d+(?:\.\d+)?(?:\s*[\+\-\*\/]\s*[-+]?\d+(?:\.\d+)?)*)", low)
    if math_m:
        expr = math_m.group(1)
        try:
            val = eval(expr, {"__builtins__": None}, {})
            return f"easy math {user_name}: `{expr.strip()} = {val}` 🧠"
        except Exception:
            pass

    # 8. Mood / How are you doing check-ins
    if any(w in low for w in ["how are you", "how r u", "hru", "how you doing", "what's up", "whats up", "wsp", "sup"]):
        replies = [
            "doing awesome {name}! just vibing in the server and ready to yap. how's your day going so far?",
            "feeling 10/10 today {name}! what have you been up to? working on bots, gaming, or just chilling?",
            "all good on my end {name}! how are you holding up today—everything going smooth?",
        ]
        return pick_unique(user_id, replies, user_name)

    if any(w in low for w in ["im good", "i'm good", "doing good", "doing great", "im great", "fine", "awesome", "pretty good", "chilling"]):
        replies = [
            "love to hear that {name}! 🙌 what are you working on or playing today?",
            "ayy that's a W {name}! we can run a roast battle (`let's do roast`), play `truth or dare`, or talk about whatever you're up to!",
            "glad you're doing good {name}! anything hyped happening today or just a chill day?",
        ]
        return pick_unique(user_id, replies, user_name)

    if any(w in low for w in ["tired", "sleepy", "exhausted", "bad", "sad", "stressed", "annoyed", "rough"]):
        replies = [
            "aw man, sorry it's been rough {name} 💙 take a breather! want me to distract you with a roast battle, a riddle, or just vent about it?",
            "i feel that {name}, long days hit hard. grab some water and chill for a bit—what's been stressing you out?",
            "hang in there {name}! we can keep it super chill or play a quick game to take your mind off things. what sounds good?",
        ]
        return pick_unique(user_id, replies, user_name)

    # 9. Coding / Roblox / Bot Dev questions
    if any(w in low for w in ["python", "luau", "roblox", "script", "code", "bot", "discord.py"]):
        replies = [
            "oh nice, we talking dev work {name}? tell me what feature you're trying to script in Python or Roblox Luau and I'll help you build it!",
            "i love coding talk {name}! drop the exact command or script idea you're working on and let's cook it up.",
        ]
        return pick_unique(user_id, replies, user_name)

    # 10. Dynamic contextual reply that directly references their message
    contextual_replies = [
        f"Wait {user_name}, when you say \"{clean[:60]}\", what's the backstory on that? 👀",
        f"ngl {user_name}, \"{clean[:60]}\" got me curious—what made you think of that? also if you wanna switch it up we can do a roast battle or truth or dare!",
        f"haha fair point {user_name}! so what's the next move—are we roasting each other, playing a game, or cooking up a new project?",
        f"i hear you {user_name}! tell me more about that, or hit me with a question / roast and let's see what you got!",
    ]
    return pick_unique(user_id, contextual_replies, user_name)


# =====================================================================
# 6. MASTER RESPONSE GENERATOR
# =====================================================================
async def generate_yap_reply(user_id: int, user_name: str, msg_text: str) -> str:
    history = USER_MEMORY.setdefault(user_id, [])
    clean_input = msg_text.strip()

    # Check if user only pinged the bot with zero text
    if not clean_input:
        if not history:
            greeting = f"yooo {user_name}! how are you doing today? we can chat, do a 1v1 roast battle (`let's do roast`), or play a game!"
        else:
            greeting = f"yo {user_name}! what's on your mind? type a message with your ping so I can reply to it!"
        history.append({"role": "assistant", "content": greeting})
        return greeting

    low = clean_input.lower()
    if any(w in low for w in ["let's do roast", "lets do roast", "roast battle", "roast me", "flame me", "cook me"]):
        USER_MODE[user_id] = "roast"
    elif any(w in low for w in ["stop roast", "chill mode", "normal mode", "truce"]):
        USER_MODE[user_id] = "human"
    elif any(w in low for w in ["genius mode", "helper mode"]):
        USER_MODE[user_id] = "genius"

    mode = USER_MODE.get(user_id, "human")

    # If the user is answering an active riddle, let the local puzzle checker grade it immediately
    if user_id in ACTIVE_PUZZLE:
        puzzle_reply = smart_local_brain(user_id, user_name, clean_input, mode)
        history.append({"role": "user", "content": clean_input})
        history.append({"role": "assistant", "content": puzzle_reply})
        return puzzle_reply

    sys_inst = build_system_prompt(user_name, mode)
    history.append({"role": "user", "content": clean_input})
    if len(history) > 14:
        history[:] = history[-14:]

    messages_payload = [{"role": "system", "content": sys_inst}] + history[-10:]

    # 1. Try Official Gemini API (if configured)
    reply = await call_gemini_api(sys_inst, history, user_name)
    if is_valid_ai_response(reply, clean_input):
        history.append({"role": "assistant", "content": reply})
        return reply

    # 2. Try Official Groq API (if configured)
    reply = await call_groq_api(messages_payload)
    if is_valid_ai_response(reply, clean_input):
        history.append({"role": "assistant", "content": reply})
        return reply

    # 3. Try Free Keyless LLM7 OpenAI Gateway
    reply = await call_llm7_free_api(messages_payload)
    if is_valid_ai_response(reply, clean_input):
        history.append({"role": "assistant", "content": reply})
        return reply

    # 4. Try Free Keyless Pollinations OpenAI POST Endpoint (/openai)
    reply = await call_pollinations_openai(messages_payload)
    if is_valid_ai_response(reply, clean_input):
        history.append({"role": "assistant", "content": reply})
        return reply

    # 5. Instant Built-In Smart Brain (Zero lag, handles roasts, games, advice & chat!)
    local_reply = smart_local_brain(user_id, user_name, clean_input, mode)
    history.append({"role": "assistant", "content": local_reply})
    return local_reply


# =====================================================================
# 7. BOT CORE & PING / REPLY DETECTOR
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
        print(f"✅ Finest Yapping AI Bot ONLINE as {self.user}")

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

        raw_content = message.content or ""
        # Detect if Discord stripped the message text because Message Content Intent is OFF on a non-ping reply
        if not raw_content.strip() and is_reply_to_bot and not self.intents.message_content:
            await message.reply(
                f"yo {message.author.display_name}! Discord hid your reply text because **Message Content Intent** is turned off in the Discord Developer Portal—either turn on **Message Content Intent** under the **Bot** tab, or include {self.user.mention} in your message so I can read what you typed!",
                mention_author=True,
            )
            return

        # Strip the @bot mention tag so the AI sees only what the player typed
        clean_text = re.sub(rf"<@!?{self.user.id}>", "", raw_content).strip()

        async with message.channel.typing():
            reply_text = await generate_yap_reply(
                message.author.id,
                message.author.display_name,
                clean_text,
            )

        for i in range(0, len(reply_text), 1950):
            chunk = reply_text[i:i + 1950]
            await message.reply(chunk, mention_author=(i == 0))


# =====================================================================
# 8. COMMANDS (!yap, !roast, !mode, !resetconvo, /yap)
# =====================================================================
def reg_cmds(b: YappingBot):
    @b.command(name="yap", aliases=["ai", "ask"])
    async def cmd_yap(ctx: commands.Context, *, message: str = ""):
        async with ctx.channel.typing():
            reply_text = await generate_yap_reply(ctx.author.id, ctx.author.display_name, message)
        for i in range(0, len(reply_text), 1950):
            await ctx.reply(reply_text[i:i + 1950], mention_author=(i == 0))

    @b.command(name="roast")
    async def cmd_roast(ctx: commands.Context, member: discord.Member = None):
        target = member or ctx.author
        USER_MODE[ctx.author.id] = "roast"
        async with ctx.channel.typing():
            reply_text = await generate_yap_reply(
                ctx.author.id,
                target.display_name,
                f"roast {target.display_name} right now!",
            )
        await ctx.reply(reply_text[:1950], mention_author=True)

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
        ACTIVE_PUZZLE.pop(ctx.author.id, None)
        await ctx.reply(
            "alright bet, wiped our chat memory for a fresh start! what do you wanna talk about?",
            mention_author=True,
        )

    @b.tree.command(name="yap", description="Talk to the AI about anything (chat, roast battle, games, code)")
    async def slash_yap(interaction: discord.Interaction, message: str):
        await interaction.response.defer()
        reply_text = await generate_yap_reply(interaction.user.id, interaction.user.display_name, message)
        await interaction.followup.send(f"{interaction.user.mention} {reply_text[:1950]}")


# =====================================================================
# 9. MAIN ENTRYPOINT
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
        print("⚠️ Message Content Intent is OFF in Developer Portal! Starting in fallback mode...")
        await bot.close()
        bot = YappingBot(False)
        reg_cmds(bot)
        await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
