import os
import io
import certifi
import random
import time
import asyncio
import discord
from pymongo import MongoClient
from discord import app_commands
from discord.ext import commands
from keep_alive import keep_alive

# ==========================================
# 1. НАСТРОЙКИ И БАЗА ДАННЫХ
# ==========================================
MONGO_URI = os.getenv('MONGO_URI')
cluster = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = cluster.aincrad_data

users_coll = db.users
custom_roles_coll = db.custom_roles
auction_coll = db.auction_roles
titles_coll = db.user_titles
guilds_coll = db.guilds
guild_reqs_coll = db.guilds_collection if hasattr(db, 'guild_requests') else db.guild_requests

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True 

bot = commands.Bot(command_prefix="!", intents=intents, chunk_guilds_at_startup=False, max_messages=10)

MAINTENANCE_MODE = False

# Константы каналов
WELCOME_CHANNEL_ID = 1529471897647976648
MEDIA_CHANNELS = [1534785295696789634, 1534785127572443246, 1534550233315414169]
VIDEO_CHANNEL_ID = 1529472211730043012
MEDIA_LOG_CHANNEL_ID = 1534789085582065794
STREAM_CHANNEL_ID = 1534785474739179530
AUTO_MOD_LOG_CHANNEL_ID = 1529472394102706336
PUBLIC_IDEA_CHANNEL_ID = 1532592402223730739  
ADMIN_IDEA_CHANNEL_ID = 1532719050319466610   
DOCS_CHANNEL_ID = 1533682208487903483

voice_start_times, voice_accumulated = {}, {}
user_last_message_time, log_cooldowns = {}, {}

ROLES_MAPPING = {
    100: "Beater (LVL 100)", 80: "Вершитель Судеб (LVL 80)", 65: "Грандмастер (LVL 65)",
    50: "Герой Айнкрада (LVL 50)", 40: "Мастер клинка (LVL 40)", 30: "Закаленный Огнем (LVL 30)",
    20: "Передовой Воин (LVL 20)", 15: "Опытный Мечник (LVL 15)", 10: "Разведчик Рубежа (LVL 10)",
    5: "Путешественник (LVL 5)", 2: "Начало Легенды (LVL 2)"
}

# ==========================================
# 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (DRY)
# ==========================================
def get_user(user_id: int) -> dict:
    user = users_coll.find_one({"_id": user_id})
    if not user:
        user = {
            "_id": user_id, "coins": 100, "xp": 0, "level": 1, 
            "last_daily": 0.0, "last_work": 0.0, "last_crime": 0.0, "last_rob": 0.0, 
            "streak": 0, "guild_id": None, "special_title": "Отсутствует", 
            "voice_time": 0.0, "partner_id": None, "marry_time": 0.0
        }
        users_coll.insert_one(user)
    return user

def update_coins(user_id: int, amount: int):
    users_coll.update_one({"_id": user_id}, {"$inc": {"coins": amount}})

def is_admin_or_mod(member: discord.Member) -> bool:
    if member.guild_permissions.administrator: return True
    roles = [r.name.lower() for r in member.roles]
    return any(r in roles for r in ["модератор", "moderator", "администратор", "administrator", "саппорт", "support", "founder", "co-founder", "content maker", "sigmo brazzers"])

def check_maintenance():
    async def predicate(interaction: discord.Interaction) -> bool:
        if MAINTENANCE_MODE and not is_admin_or_mod(interaction.user):
            await interaction.response.send_message("🛠️ **[ SYSTEM ALERT: КАРДИНАЛ АКТИВЕН ]**\nНа сервере проводятся технические работы.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

async def check_level_roles(member: discord.Member, current_level: int):
    highest_role_name = next((name for lvl, name in ROLES_MAPPING.items() if current_level >= lvl), None)
    if not highest_role_name: return

    highest_role = discord.utils.get(member.guild.roles, name=highest_role_name)
    roles_to_remove = [discord.utils.get(member.guild.roles, name=n) for n in ROLES_MAPPING.values() if n != highest_role_name]
    roles_to_remove = [r for r in roles_to_remove if r and r in member.roles]
    
    if roles_to_remove:
        try: await member.remove_roles(*roles_to_remove)
        except: pass

    if highest_role and highest_role not in member.roles:
        try:
            await member.add_roles(highest_role)
            await member.send(f"🎉 Поздравляем! Вы прорвались на **{current_level} этаж** Айнкрада и получили элитный статус **{highest_role_name}**!")
        except: pass

async def add_xp(interaction_or_member, user_id: int, amount: int):
    user = get_user(user_id)
    xp, level = user['xp'] + amount, user['level']
    leveled_up = False
    
    get_next_xp = lambda l: int(35 * (l ** 1.85) + 80 * l + 40)
    
    while xp >= get_next_xp(level):
        xp -= get_next_xp(level)
        level += 1
        leveled_up = True

    users_coll.update_one({"_id": user_id}, {"$set": {"xp": xp, "level": level}})

    if leveled_up:
        member = interaction_or_member if isinstance(interaction_or_member, discord.Member) else getattr(interaction_or_member, 'user', getattr(interaction_or_member, 'author', None))
        channel = getattr(interaction_or_member, 'channel', None)
        if member:
            await check_level_roles(member, level)
            if channel:
                embed = discord.Embed(title="⚡ СИСТЕМНОЕ УВЕДОМЛЕНИЕ: ПОВЫШЕНИЕ ЭТАЖА", description=f"Поздравляем! Игрок успешно прорвался на **{level} этаж** башни Айнкрад!", color=0x00BFFF)
                embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
                try: await channel.send(content=f"Внимание, Система: {member.mention} устанавливает новые рекорды!", embed=embed, delete_after=10.0)
                except: pass

# ==========================================
# 3. ИВЕНТЫ (АВТОМОД, ВОЙС, ПРИВЕТСТВИЕ)
# ==========================================
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Бот {bot.user} запущен и полностью готов к работе в Айнкраде!")

def is_afk(vs): return vs.self_mute or vs.mute or vs.self_deaf or vs.deaf or vs.afk

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot: return
    uid = member.id
    was_afk, is_afk_now = before.channel is None or is_afk(before), after.channel is None or is_afk(after)

    if was_afk and not is_afk_now:
        voice_start_times[uid] = time.time()
    elif not was_afk and is_afk_now and uid in voice_start_times:
        voice_accumulated[uid] = voice_accumulated.get(uid, 0.0) + (time.time() - voice_start_times.pop(uid))

    if before.channel and not after.channel:
        if uid in voice_start_times:
            voice_accumulated[uid] = voice_accumulated.get(uid, 0.0) + (time.time() - voice_start_times.pop(uid))
        
        total = voice_accumulated.pop(uid, 0.0)
        mins = int(total // 60)
        if mins >= 1:
            users_coll.update_one({"_id": uid}, {"$inc": {"coins": mins, "voice_time": int(total)}}, upsert=True)
            await add_xp(member, uid, mins)
            try:
                embed = discord.Embed(description="```ansi\n\u001b[36m─────────────── ┌ 🎙️ ВОЙС-АКТИВНОСТЬ ┐ ───────────────\u001b[0m\n```\nСеанс связи завершен!", color=0x00BFFF)
                embed.add_field(name="🪙 Колы", value=f"```fix\n+{mins:,}\n```", inline=True)
                embed.add_field(name="⚡ Опыт", value=f"```yaml\n+{mins:,} XP\n```", inline=True)
                embed.add_field(name="⏱️ Время", value=f"```yaml\n{mins} мин.\n```", inline=True)
                embed.set_footer(text="Cardinal Anti-AFK System • Айнкрад")
                await member.send(embed=embed)
            except: pass

@bot.event
async def on_member_join(member):
    role = discord.utils.get(member.guild.roles, name="unverify")
    if role: 
        try: await member.add_roles(role)
        except: pass
    welcome = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if welcome:
        embed = discord.Embed(color=0x2B2D31).set_author(name=f"Member #{member.guild.member_count}", icon_url=member.display_avatar.url)
        embed.set_image(url="https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExZWFmN3l4dDZleDhmdDJ0Y3MxcDlhMzB5cWs4dHgxM29na2Q2ZmQ0diZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/12wr8S2n5fL8lO/giphy.gif")
        try: await welcome.send(content=f"Welcome {member.mention} to **Aincrad**!", embed=embed)
        except: pass

class MediaModerationView(discord.ui.View):
    def __init__(self, author_id, channel_id, content_text, files_data):
        super().__init__(timeout=None)
        self.author_id, self.channel_id, self.content_text, self.files_data = author_id, channel_id, content_text, files_data

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
        channel = interaction.guild.get_channel(self.channel_id)
        webhook = discord.utils.get(await channel.webhooks(), name="Yui Media") or await channel.create_webhook(name="Yui Media")
        files = [discord.File(io.BytesIO(f["bytes"]), filename=f["filename"]) for f in self.files_data]
        content = f"**Отправил:** <@{self.author_id}>\n\n{self.content_text}" if self.content_text else f"**Отправил:** <@{self.author_id}>"
        
        msg = await webhook.send(content=content, files=files, username="Yui", avatar_url=bot.user.display_avatar.url, wait=True)
        try: await msg.add_reaction("❤️")
        except: pass

        embed = interaction.message.embeds[0]
        embed.color, embed.title = 0x2ECC71, "✅ КОНТЕНТ ОДОБРЕН И ОПУБЛИКОВАН"
        embed.add_field(name="Одобрил", value=interaction.user.mention, inline=False)
        for c in self.children: c.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        await add_xp(interaction, self.author_id, random.randint(2, 5))

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
        embed = interaction.message.embeds[0]
        embed.color, embed.title = 0xE74C3C, "❌ КОНТЕНТ ОТКЛОНЕН"
        embed.add_field(name="Отклонил", value=interaction.user.mention, inline=False)
        for c in self.children: c.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild: return
    if MAINTENANCE_MODE and not is_admin_or_mod(message.author): return

    is_priv = is_admin_or_mod(message.author)
    uid, content_lower, atts = message.author.id, message.content.lower().strip(), len(message.attachments)
    violation, curr_time = None, time.time()

    if not is_priv:
        is_media = any(d in content_lower for d in ["tenor.com", "giphy.com", "imgur.com", "discordapp.com", "discord.com", "pinimg.com", "klipy.com"]) or ".gif" in content_lower
        has_stream = any(p in content_lower for p in ["twitch.tv", "youtube.com/live", "kick.com", "trovo.live", "vkplay.live", "youtu.be", "tiktok.com"])
        has_link = any(p in content_lower for p in ["http://", "https://", "www."]) and not is_media and not has_stream
        
        is_fast = (curr_time - user_last_message_time.get(uid, 0)) < 1.5
        user_last_message_time[uid] = curr_time
        letters = [c for c in message.content if c.isalpha()]
        is_caps = len(letters) > 8 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7

        if any(i in content_lower for i in ["discord.gg/", "discord.com/invite"]): violation = "Попытка публикации стороннего инвайта"
        elif has_link: violation = "Публикация неразрешенной ссылки"
        elif "@everyone" in message.content or "@here" in message.content: violation = "Массовый пинг"
        elif atts >= 3 and message.channel.id not in MEDIA_CHANNELS: violation = "Массовый спам картинками"
        elif is_fast: violation = "Слишком быстрый флуд"
        elif is_caps: violation = "Caps Lock Spam"
        elif message.channel.id in MEDIA_CHANNELS and atts == 0 and not is_media: violation = "Только медиаконтент!"
        elif message.channel.id == VIDEO_CHANNEL_ID and not has_stream and atts == 0 and not is_media: violation = "Только видеоролики!"
        elif message.channel.id == STREAM_CHANNEL_ID and not has_stream: violation = "Только стримы!"

        if violation:
            try: await message.delete()
            except: pass
            if curr_time - log_cooldowns.get(uid, 0) > 15.0:
                log_cooldowns[uid] = curr_time
                log_ch = message.guild.get_channel(AUTO_MOD_LOG_CHANNEL_ID)
                if log_ch:
                    embed = discord.Embed(title="⚠️ КАРДИНАЛ: НОВЫЙ ОТЧЕТ АВТОМОДА", color=0xE74C3C)
                    embed.add_field(name="Пользователь", value=message.author.mention)
                    embed.add_field(name="Канал", value=message.channel.mention)
                    embed.add_field(name="Причина", value=violation, inline=False)
                    try: await log_ch.send(embed=embed)
                    except: pass
            return

    if message.channel.id in MEDIA_CHANNELS or message.channel.id == VIDEO_CHANNEL_ID:
        if message.attachments or message.embeds or "http" in content_lower or ".gif" in content_lower:
            try:
                mod_ch = bot.get_channel(MEDIA_LOG_CHANNEL_ID)
                files_data = [{"bytes": await a.read(), "filename": a.filename} for a in message.attachments]
                embed = discord.Embed(title="🔍 ПРЕМОДЕРАЦИЯ КОНТЕНТА", description=f"**Автор:** {message.author.mention}\n**Канал:** {message.channel.mention}", color=0xF1C40F)
                if message.content: embed.add_field(name="Текст", value=message.content, inline=False)
                if message.attachments: embed.set_image(url=message.attachments[0].url)
                elif message.embeds and message.embeds[0].image: embed.set_image(url=message.embeds[0].image.url)
                
                await mod_ch.send(embed=embed, view=MediaModerationView(message.author.id, message.channel.id, message.content, files_data))
                await message.delete()
            except: pass
            return

    await add_xp(message, message.author.id, random.randint(2, 5))
    await bot.process_commands(message)

# ==========================================
# 4. ЭКОНОМИКА, ПРОФИЛЬ, БРАКИ
# ==========================================
@bot.tree.command(name="balance", description="Посмотреть текущий баланс Колов")
@check_maintenance()
async def balance(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    u = get_user(target.id)
    embed = discord.Embed(title="🌐 БАНКОВСКИЙ СЧЕТ АЙНКРАДА", color=0x00BFFF)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="💳 Владелец", value=f"{target.mention}", inline=False)
    embed.add_field(name="💰 Баланс", value=f"```fix\n{u['coins']:,} Колов\n```", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="profile", description="Посмотреть подробный игровой профиль")
@check_maintenance()
async def profile(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    u = get_user(target.id)
    nxt = int(35 * (u['level'] ** 1.85) + 80 * u['level'] + 40)
    bar = "🟩" * int((u['xp'] / nxt) * 10 if nxt > 0 else 0) + "⬛" * (10 - int((u['xp'] / nxt) * 10 if nxt > 0 else 0))
    vh, vm = int(u['voice_time'] // 3600), int((u['voice_time'] % 3600) // 60)

    embed = discord.Embed(title=f"🛡️ ИГРОВОЙ ПРОФИЛЬ: {target.display_name}", color=0x00BFFF)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="⚔️ Этаж башни", value=f"```yaml\n{u['level']}\n```", inline=True)
    embed.add_field(name="🪙 Капитал", value=f"```fix\n{u['coins']:,} Колов\n```", inline=True)
    embed.add_field(name="🔥 Стрик", value=f"```yaml\n{u['streak']} дн.\n```", inline=True)
    embed.add_field(name="🎙️ Часы в Voice", value=f"```yaml\n{vh} ч. {vm} м.\n```", inline=True)
    embed.add_field(name="🏰 Гильдия", value=f"```yaml\n{u['guild_id'] or 'Нет'}\n```", inline=True)
    embed.add_field(name="✨ Активный титул", value=f"```fix\n{u['special_title']}\n```", inline=False)
    embed.add_field(name="📊 Прогресс опыта (XP)", value=f"{u['xp']} / {nxt} XP\n{bar}", inline=False)
    await interaction.response.send_message(embed=embed)

class MarryAcceptView(discord.ui.View):
    def __init__(self, proposer, target):
        super().__init__(timeout=300)
        self.proposer, self.target = proposer, target

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, emoji="💍")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id: return await interaction.response.send_message("❌ Не для вас!", ephemeral=True)
        if get_user(self.proposer.id)['coins'] < 3000: return await interaction.response.send_message("❌ У инициатора нет средств!", ephemeral=True)
        
        for c in self.children: c.disabled = True
        role = discord.utils.get(interaction.guild.roles, name="💞")
        if role:
            try: 
                await self.proposer.add_roles(role)
                await self.target.add_roles(role)
            except: pass
        
        update_coins(self.proposer.id, -3000)
        curr = time.time()
        users_coll.update_one({"_id": self.proposer.id}, {"$set": {"partner_id": self.target.id, "marry_time": curr}})
        users_coll.update_one({"_id": self.target.id}, {"$set": {"partner_id": self.proposer.id, "marry_time": curr}})
        
        embed = discord.Embed(title="💖 УСПЕШНО ПОЖЕНИЛИСЬ!", description=f"{self.target.mention} и {self.proposer.mention} теперь состоят в законном браке!", color=0xFF69B4)
        embed.set_image(url="https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExcmwwc2U1cDg3ZHUzcjZ6ZG9ieGhlZ2llcGhsNzgzeTE3Y3k0bHFxYyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/nyGFcsP0kAobm/giphy.gif")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Отказать", style=discord.ButtonStyle.red, emoji="💔")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id: return await interaction.response.send_message("❌ Не для вас!", ephemeral=True)
        for c in self.children: c.disabled = True
        embed = discord.Embed(title="💔 ОТКАЗ", description=f"{self.target.mention} отверг(ла) предложение.", color=0x2B2D31)
        embed.set_image(url="https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExeXo0aXNtbDVpOHY0NmN5d3NjcnBvdmJrZ2hnYm13dHV3ZnllZ2E3YiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/oS56qcrdYDBw4/giphy.gif")
        await interaction.response.edit_message(embed=embed, view=self)

@bot.tree.command(name="marry", description="Сделать предложение руки и сердца (3000 Колов)")
@check_maintenance()
async def marry(interaction: discord.Interaction, member: discord.Member):
    if member.id == interaction.user.id or member.bot: return await interaction.response.send_message("❌ Ошибка цели!", ephemeral=True)
    if get_user(interaction.user.id)['coins'] < 3000: return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)
    if get_user(interaction.user.id).get('partner_id') or get_user(member.id).get('partner_id'):
        return await interaction.response.send_message("❌ Один из вас уже в браке!", ephemeral=True)
        
    embed = discord.Embed(title="💍 ПРЕДЛОЖЕНИЕ", description=f"{member.mention}, игрок {interaction.user.mention} предлагает вам вступить в брак!\nВы согласны?", color=0x2B2D31)
    await interaction.response.send_message(content=member.mention, embed=embed, view=MarryAcceptView(interaction.user, member))

@bot.tree.command(name="divorce", description="Расторгнуть брак (1000 Колов)")
@check_maintenance()
async def divorce(interaction: discord.Interaction):
    u1 = get_user(interaction.user.id)
    if not u1.get("partner_id"): return await interaction.response.send_message("❌ Вы не в браке!", ephemeral=True)
    if u1['coins'] < 1000: return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)
        
    partner_id = u1["partner_id"]
    users_coll.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -1000}, "$set": {"partner_id": None, "marry_time": 0.0}})
    users_coll.update_one({"_id": partner_id}, {"$set": {"partner_id": None, "marry_time": 0.0}})
    
    role = discord.utils.get(interaction.guild.roles, name="💞")
    if role:
        try:
            await interaction.user.remove_roles(role)
            pm = interaction.guild.get_member(partner_id)
            if pm: await pm.remove_roles(role)
        except: pass
        
    await interaction.response.send_message(embed=discord.Embed(title="💔 РАЗВОД", description=f"Вы расторгли брак с <@{partner_id}>.", color=0x2B2D31))

@bot.tree.command(name="love_profile", description="Профиль вашей пары")
@check_maintenance()
async def love_profile(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    u = get_user(target.id)
    if not u.get("partner_id"): return await interaction.response.send_message("❌ Игрок не в браке.", ephemeral=True)
    days = int((time.time() - u.get("marry_time", time.time())) // 86400)
    
    embed = discord.Embed(color=0x2B2D31).set_author(name=f"Любовный профиль | {target.display_name}", icon_url=target.display_avatar.url)
    embed.description = f"💞 **Партнеры:** <@{target.id}> и <@{u['partner_id']}>\n⏳ **Вместе:** `{days} дн.`"
    embed.set_image(url="https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExcmwwc2U1cDg3ZHUzcjZ6ZG9ieGhlZ2llcGhsNzgzeTE3Y3k0bHFxYyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/nyGFcsP0kAobm/giphy.gif")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Глобальный топ игроков")
@app_commands.choices(category=[app_commands.Choice(name="Этажи", value="level"), app_commands.Choice(name="Колы", value="coins"), app_commands.Choice(name="Войс", value="voice"), app_commands.Choice(name="Гильдии", value="guilds")])
@check_maintenance()
async def leaderboard(interaction: discord.Interaction, category: str = "level"):
    if category == "level":
        top = list(users_coll.find().sort([("level", -1), ("xp", -1)]).limit(10))
        title, desc = "🏆 ТОП-10 (ЭТАЖИ)", "\n".join([f"`#{i}` <@{u['_id']}> — **{u.get('level', 1)} этаж**" for i, u in enumerate(top, 1)])
    elif category == "coins":
        top = list(users_coll.find().sort("coins", -1).limit(10))
        title, desc = "💰 ТОП-10 (КОЛЫ)", "\n".join([f"`#{i}` <@{u['_id']}> — **{u.get('coins', 0):,} Колов**" for i, u in enumerate(top, 1)])
    elif category == "guilds":
        top = list(guilds_coll.find().sort("bank", -1).limit(10))
        title, desc = "🏰 ТОП-10 ГИЛЬДИЙ", "\n".join([f"`#{i}` **{g['guild_name']}** — **{g.get('bank', 0):,} Колов**" for i, g in enumerate(top, 1)])
    else:
        top = list(users_coll.find().sort("voice_time", -1).limit(10))
        title, desc = "🎙️ ТОП-10 (ВОЙС)", "\n".join([f"`#{i}` <@{u['_id']}> — **{int(u.get('voice_time',0)//3600)}ч {int((u.get('voice_time',0)%3600)//60)}м**" for i, u in enumerate(top, 1)])

    await interaction.response.send_message(embed=discord.Embed(title=title, description=desc or "Пусто.", color=0x00BFFF))

@bot.tree.command(name="pay", description="Перевод Колов (Комиссия 10%)")
@check_maintenance()
async def pay(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0 or member.id == interaction.user.id: return await interaction.response.send_message("❌ Некорректная сумма/цель!", ephemeral=True)
    if get_user(interaction.user.id)['coins'] < amount: return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)

    get_user(member.id)
    fee = int(amount * 0.10)
    update_coins(interaction.user.id, -amount)
    update_coins(member.id, amount - fee)

    embed = discord.Embed(title="💸 ПЕРЕВОД УСПЕШЕН", description=f"Списано: **{amount:,}**\nЗачислено ({member.mention}): **{amount - fee:,}**\n*(Комиссия: {fee:,})*", color=0x2B2D31)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="daily", description="Ежедневная награда")
@check_maintenance()
async def daily(interaction: discord.Interaction):
    u, curr = get_user(interaction.user.id), time.time()
    if curr - u['last_daily'] < 86400:
        left = int(86400 - (curr - u['last_daily']))
        return await interaction.response.send_message(f"⏳ Ждите {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)
    
    streak = 0 if u['last_daily'] > 0 and curr - u['last_daily'] > 172800 else u['streak'] + 1
    m = 1.0 + (streak * 0.15)
    rc, rx = int((40 + u['level']*8) * m), int((15 + u['level']*3) * m)

    users_coll.update_one({"_id": u['_id']}, {"$inc": {"coins": rc}, "$set": {"streak": streak, "last_daily": curr}})
    await add_xp(interaction, u['_id'], rx)

    embed = discord.Embed(title="🎁 ЕЖЕДНЕВНАЯ НАГРАДА", color=0x00FF00)
    embed.add_field(name="Стрик", value=f"```yaml\n{streak} дн.\n```")
    embed.add_field(name="Колы", value=f"```fix\n+{rc}\n```")
    embed.add_field(name="Опыт", value=f"```yaml\n+{rx} XP\n```")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="work", description="Работа в Айнкраде")
@check_maintenance()
async def work(interaction: discord.Interaction):
    u, curr = get_user(interaction.user.id), time.time()
    if curr - u['last_work'] < 7200:
        left = int(7200 - (curr - u['last_work']))
        return await interaction.response.send_message(f"⏳ Усталость. Ждите {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)

    earned = random.randint(40, 120) + (u['level'] * 2)
    users_coll.update_one({"_id": u['_id']}, {"$inc": {"coins": earned}, "$set": {"last_work": curr}})
    await add_xp(interaction, u['_id'], random.randint(10, 20))

    embed = discord.Embed(title="🛠️ ОТЧЕТ РАБОТЫ", color=0x3498DB)
    embed.add_field(name="Награда", value=f"```fix\n+{earned} Колов\n```")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="crime", description="Рискованная авантюра")
@check_maintenance()
async def crime(interaction: discord.Interaction):
    u, curr = get_user(interaction.user.id), time.time()
    if curr - u['last_crime'] < 14400:
        left = int(14400 - (curr - u['last_crime']))
        return await interaction.response.send_message(f"⏳ Ждите {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)

    embed = discord.Embed()
    if random.choice([True, False]):
        r = random.randint(50, 130) + (u['level'] * 2)
        users_coll.update_one({"_id": u['_id']}, {"$inc": {"coins": r}, "$set": {"last_crime": curr}})
        await add_xp(interaction, u['_id'], 15)
        embed.title, embed.color, embed.description = "🥷 УСПЕХ", 0x2ECC71, f"Получено: +{r} Колов"
    else:
        f = random.randint(30, 70)
        users_coll.update_one({"_id": u['_id']}, {"$set": {"coins": max(0, u['coins'] - f), "last_crime": curr}})
        embed.title, embed.color, embed.description = "❌ ПОЙМАН", 0xE74C3C, f"Штраф: -{f} Колов"
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rob", description="Карманная кража")
@check_maintenance()
async def rob(interaction: discord.Interaction, member: discord.Member):
    if member.id == interaction.user.id or member.bot: return await interaction.response.send_message("❌ Невозможно!", ephemeral=True)
    if any(r in [r.name.lower() for r in member.roles] for r in ["неприкасаемый", "модератор"]): return await interaction.response.send_message("🛡️ Защищен иммунитетом!", ephemeral=True)

    att, tgt, curr = get_user(interaction.user.id), get_user(member.id), time.time()
    if curr - att['last_rob'] < 10800:
        left = int(10800 - (curr - att['last_rob']))
        return await interaction.response.send_message(f"⏳ Ждите {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)
    if tgt['coins'] < 500: return await interaction.response.send_message("❌ У жертвы мало денег.", ephemeral=True)
    if att['coins'] > 50000 and att['coins'] > tgt['coins'] * 5: return await interaction.response.send_message("❌ Запрещено грабить бедняков!", ephemeral=True)

    pot = max(20, random.randint(int(tgt['coins'] * 0.05), int(tgt['coins'] * 0.10)))
    if att['coins'] < pot: return await interaction.response.send_message(f"❌ Нужен залог в {pot:,} Колов!", ephemeral=True)

    users_coll.update_one({"_id": att['_id']}, {"$set": {"last_rob": curr}})
    await interaction.response.send_message(embed=discord.Embed(title="🕵️ ОГРАБЛЕНИЕ", description="Вы подкрадываетесь...", color=0x2C3E50))
    await asyncio.sleep(3.0)

    res = discord.Embed(title="🕵️ РЕЗУЛЬТАТ")
    res.set_image(url="https://media1.tenor.com/images/HsNUWd_R6RYAAAAC/sword-art-online-sao.gif")
    if random.choice([True, False]):
        update_coins(att['_id'], pot)
        update_coins(tgt['_id'], -pot)
        await add_xp(interaction, att['_id'], 20)
        res.color, res.description = 0x2ECC71, f"🎉 Успех! Украдено: **+{pot:,}**"
    else:
        update_coins(att['_id'], -pot)
        res.color, res.description = 0xE74C3C, f"🚨 Заметили! Штраф: **-{pot:,}**"
    await interaction.edit_original_response(embed=res)

# ==========================================
# 5. АЗАРТНЫЕ ИГРЫ И ДУЭЛЬ (DRY)
# ==========================================
async def check_gambling(interaction, amount):
    if amount < 50:
        await interaction.response.send_message("❌ Мин. ставка: 50 Колов!", ephemeral=True)
        return False
    if get_user(interaction.user.id)['coins'] < amount:
        await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)
        return False
    update_coins(interaction.user.id, -amount)
    return True

class DuelAcceptView(discord.ui.View):
    def __init__(self, c, t, a):
        super().__init__(timeout=300)
        self.c, self.t, self.a = c, t, a
    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, emoji="⚔️")
    async def acc(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.t.id: return await i.response.send_message("❌ Не для вас!", ephemeral=True)
        for child in self.children: child.disabled = True
        await i.response.edit_message(view=self)
        if get_user(self.c.id)['coins'] < self.a or get_user(self.t.id)['coins'] < self.a:
            return await i.followup.send("❌ Недостаточно средств у одного из бойцов!", ephemeral=True)
        
        embed = discord.Embed(title="⚔️ АРЕНА", description=f"Ставка: **{self.a:,} Колов**.", color=0xE67E22)
        embed.set_image(url="https://media1.tenor.com/images/HsNUWd_R6RYAAAAC/sword-art-online-sao.gif")
        msg = await i.followup.send(embed=embed)
        await asyncio.sleep(3.0)
        
        w = random.choice([self.c, self.t])
        l = self.t if w == self.c else self.c
        update_coins(w.id, self.a)
        update_coins(l.id, -self.a)
        await add_xp(i, w.id, 25)
        
        res = discord.Embed(title="⚔️ ИТОГ", description=f"🏆 **Победитель:** {w.mention} забрал **{self.a:,} Колов**!", color=0x3498DB)
        await msg.edit(embed=res, attachments=[])
    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="🏃")
    async def dec(self, i: discord.Interaction, b: discord.ui.Button):
        if i.user.id != self.t.id: return await i.response.send_message("❌ Не для вас!", ephemeral=True)
        for child in self.children: child.disabled = True
        await i.response.edit_message(embed=discord.Embed(title="⚔️ ОТМЕНА", description=f"{self.t.mention} отклонил вызов.", color=0x2B2D31), view=self)

@bot.tree.command(name="duel", description="Вызвать на дуэль")
@check_maintenance()
async def duel(interaction: discord.Interaction, target: discord.Member, amount: int):
    if target.id == interaction.user.id or target.bot: return await interaction.response.send_message("❌ Ошибка цели!", ephemeral=True)
    if amount < 50: return await interaction.response.send_message("❌ Мин ставка 50!", ephemeral=True)
    if get_user(interaction.user.id)['coins'] < amount or get_user(target.id)['coins'] < amount:
        return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)
    
    embed = discord.Embed(title="⚔️ ВЫЗОВ", description=f"{interaction.user.mention} вызывает {target.mention}!\nСтавка: `{amount:,}` Колов.", color=0xE67E22)
    await interaction.response.send_message(content=target.mention, embed=embed, view=DuelAcceptView(interaction.user, target, amount))

@bot.tree.command(name="dice", description="Игральные кости (Мин: 50)")
@check_maintenance()
async def dice(interaction: discord.Interaction, amount: int):
    if not await check_gambling(interaction, amount): return
    embed = discord.Embed(title="🎲 КОСТИ", description="Бросаем...", color=0x9B59B6)
    embed.set_image(url="https://media2.giphy.com/media/l4hLA4ALdmXqSRBKQ/giphy.gif")
    await interaction.response.send_message(embed=embed)
    await asyncio.sleep(3.0)

    p, b = random.randint(1, 6), random.randint(1, 6)
    res = discord.Embed(title="🎲 ИТОГ", color=0x2ECC71 if p > b else 0xE74C3C if p < b else 0xF1C40F)
    res.add_field(name="Вы", value=p)
    res.add_field(name="Бот", value=b)
    
    if p > b:
        update_coins(interaction.user.id, amount * 2)
        res.add_field(name="Выигрыш", value=f"+{amount}", inline=False)
    elif p < b: res.add_field(name="Проигрыш", value=f"-{amount}", inline=False)
    else:
        update_coins(interaction.user.id, amount)
        res.description = "🤝 Ничья."
    await interaction.edit_original_response(embed=res, attachments=[])
    await add_xp(interaction, interaction.user.id, random.randint(5, 10))

@bot.tree.command(name="coinflip", description="Монетка (Мин: 50)")
@app_commands.choices(choice=[app_commands.Choice(name="Орел", value="орел"), app_commands.Choice(name="Решка", value="решка")])
@check_maintenance()
async def coinflip(interaction: discord.Interaction, choice: str, amount: int):
    if not await check_gambling(interaction, amount): return
    embed = discord.Embed(title="🪙 ОРЕЛ И РЕШКА", description="Бросаем...", color=0xF1C40F)
    embed.set_image(url="https://media1.tenor.com/m/9PALsSO_XpsAAAAd/misaka-mikoto.gif")
    await interaction.response.send_message(embed=embed)
    await asyncio.sleep(3.0)

    res = random.choice(["орел", "решка"])
    emb = discord.Embed(title="🪙 РЕЗУЛЬТАТ", color=0x2ECC71 if choice == res else 0xE74C3C)
    emb.add_field(name="Выпало", value=res.upper(), inline=False)
    if choice == res:
        update_coins(interaction.user.id, amount * 2)
        emb.add_field(name="Выигрыш", value=f"+{amount}", inline=False)
    else: emb.add_field(name="Проигрыш", value=f"-{amount}", inline=False)
    
    await interaction.edit_original_response(embed=emb, attachments=[])
    await add_xp(interaction, interaction.user.id, random.randint(5, 15))

@bot.tree.command(name="roulette", description="Русская рулетка (Мин: 50)")
@check_maintenance()
async def roulette(interaction: discord.Interaction, amount: int):
    if not await check_gambling(interaction, amount): return
    embed = discord.Embed(title="🎯 РУССКАЯ РУЛЕТКА", description="Вращаем...", color=0xE74C3C)
    embed.set_image(url="https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExdWQwMjM0MGVwcGZkYTN2aHF2aTN2MjZ2MzhzMjMwbzE3bzgzMGZ6ZSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/3o7TKSHA3wTep5L848/giphy.gif")
    await interaction.response.send_message(embed=embed)
    await asyncio.sleep(3.0)

    shot = random.choice([True, False, False, False, False, False])
    emb = discord.Embed(title="🎯 ИТОГ", color=0xE74C3C if shot else 0x2ECC71)
    if not shot:
        update_coins(interaction.user.id, amount * 2)
        emb.add_field(name="Барабан", value="Пусто (Щелк)", inline=False)
        emb.add_field(name="Выигрыш", value=f"+{amount}", inline=False)
    else:
        emb.add_field(name="Барабан", value="Выстрел (БАХ)", inline=False)
        emb.add_field(name="Проигрыш", value=f"-{amount}", inline=False)
        
    await interaction.edit_original_response(embed=emb, attachments=[])
    await add_xp(interaction, interaction.user.id, random.randint(10, 20))

# ==========================================
# 6. МАГАЗИН, РОЛИ, ТИТУЛЫ
# ==========================================
class CustomRoleModal(discord.ui.Modal, title="Кастомная роль"):
    role_name = discord.ui.TextInput(label="Название", max_length=50)
    role_color = discord.ui.TextInput(label="HEX Цвет", max_length=6, min_length=6)
    def __init__(self, p): super().__init__(); self.p = p
    async def on_submit(self, i: discord.Interaction):
        rn = self.role_name.value.strip()
        if custom_roles_coll.find_one({"role_name": {"$regex": f"^{rn}$", "$options": "i"}}) or discord.utils.get(i.guild.roles, name=rn):
            return await i.response.send_message("❌ Роль существует!", ephemeral=True)
        try: c = int(self.role_color.value.strip(), 16)
        except: return await i.response.send_message("❌ Неверный HEX!", ephemeral=True)
        
        update_coins(i.user.id, -self.p)
        try:
            r = await i.guild.create_role(name=rn, color=discord.Color(c))
            await i.user.add_roles(r)
            custom_roles_coll.insert_one({"role_id": r.id, "user_id": i.user.id, "role_name": rn})
            await i.response.send_message(f"✅ Роль **{rn}** создана!", ephemeral=True)
        except:
            update_coins(i.user.id, self.p)
            await i.response.send_message("❌ Ошибка создания.", ephemeral=True)

class CustomTitleModal(discord.ui.Modal, title="Кастомный титул"):
    title_text = discord.ui.TextInput(label="Текст", max_length=30)
    def __init__(self, p): super().__init__(); self.p = p
    async def on_submit(self, i: discord.Interaction):
        tt = self.title_text.value.strip()
        users_coll.update_one({"_id": i.user.id}, {"$inc": {"coins": -self.p}, "$set": {"special_title": tt}})
        titles_coll.insert_one({"user_id": i.user.id, "title_name": tt})
        await i.response.send_message(f"👑 Титул **{tt}** куплен!", ephemeral=True)

class ShopButtonsView(discord.ui.View):
    def __init__(self): super().__init__(timeout=300)
    
    @discord.ui.button(label="Неприкасаемый (15,000)", style=discord.ButtonStyle.blurple, emoji="🛡️")
    async def buy_un(self, i: discord.Interaction, b: discord.ui.Button):
        r = discord.utils.get(i.guild.roles, name="Неприкасаемый")
        if not r: return await i.response.send_message("❌ Роли нет на сервере.", ephemeral=True)
        if r in i.user.roles: return await i.response.send_message("❌ Уже есть!", ephemeral=True)
        if get_user(i.user.id)['coins'] < 15000: return await i.response.send_message("❌ Нет средств!", ephemeral=True)
        update_coins(i.user.id, -15000)
        await i.user.add_roles(r)
        await i.response.send_message("🎉 Статус куплен!", ephemeral=True)
        
    @discord.ui.button(label="Кастомная роль (10,000)", style=discord.ButtonStyle.green, emoji="✨")
    async def buy_cr(self, i: discord.Interaction, b: discord.ui.Button):
        if custom_roles_coll.count_documents({"user_id": i.user.id}) >= 2: return await i.response.send_message("❌ Лимит ролей!", ephemeral=True)
        if get_user(i.user.id)['coins'] < 10000: return await i.response.send_message("❌ Нет средств!", ephemeral=True)
        await i.response.send_modal(CustomRoleModal(10000))
        
    @discord.ui.button(label="Кастомный титул (5,000)", style=discord.ButtonStyle.grey, emoji="👑")
    async def buy_ct(self, i: discord.Interaction, b: discord.ui.Button):
        if get_user(i.user.id)['coins'] < 5000: return await i.response.send_message("❌ Нет средств!", ephemeral=True)
        await i.response.send_modal(CustomTitleModal(5000))

@bot.tree.command(name="shop", description="Магазин предметов")
@check_maintenance()
async def shop(interaction: discord.Interaction):
    desc = (
        "Добро пожаловать в торговый интерфейс системы. Выберите нужную привилегию для покупки с помощью кнопок ниже.\n\n"
        "🛡️ **Элитный статус «Неприкасаемый»**\n"
        "```yaml\nСтоимость: 15,000 Колов\n```\n"
        "Обеспечивает абсолютный и бессрочный иммунитет от любых попыток карманных краж и грабежей другими игроками.\n\n"
        "✨ **Персональная Кастомная Роль**\n"
        "```yaml\nСтоимость: 10,000 Колов\n```\n"
        "Позволяет зарегистрировать собственное уникальное имя роли и персональный цвет в формате HEX с выдачей в ваш профиль.\n\n"
        "👑 **Уникальный Кастомный Титул**\n"
        "```yaml\nСтоимость: 5,000 Колов\n```\n"
        "Устанавливает индивидуальный престижный текстовый статус, который отображается в вашем персональном `/profile`."
    )
    embed = discord.Embed(title="🛒 ЦЕНТРАЛЬНЫЙ ИГРОВОЙ МАГАЗИН АЙНКРАДА", description=desc, color=0x2B2D31)
    embed.set_image(url="https://media1.giphy.com/media/v1.Y2lkPTc5MGI3NjExMmZrb210em05Ync0M2p6bnE2anJwZGM2NDk2MG9ieDluN3JzbTk2ZCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/DU3DhzJli9dsc/giphy.gif")
    embed.set_footer(text="Aincrad Economy System • Используйте кнопки интерфейса для взаимодействия")
    await interaction.response.send_message(embed=embed, view=ShopButtonsView())

class EditRoleModal(discord.ui.Modal, title="Редактирование"):
    rn = discord.ui.TextInput(label="Новое имя", max_length=50)
    rc = discord.ui.TextInput(label="HEX", max_length=6, min_length=6)
    def __init__(self, r, p): super().__init__(); self.r, self.p = r, p
    async def on_submit(self, i: discord.Interaction):
        if custom_roles_coll.find_one({"role_name": self.rn.value, "role_id": {"$ne": self.r.id}}): return await i.response.send_message("❌ Имя занято!", ephemeral=True)
        try: c = int(self.rc.value, 16)
        except: return await i.response.send_message("❌ Неверный HEX!", ephemeral=True)
        update_coins(i.user.id, -self.p)
        await self.r.edit(name=self.rn.value, color=discord.Color(c))
        custom_roles_coll.update_one({"role_id": self.r.id}, {"$set": {"role_name": self.rn.value}})
        auction_coll.update_one({"role_id": self.r.id}, {"$set": {"role_name": self.rn.value}})
        await i.response.send_message(f"✅ Обновлено на **{self.rn.value}**!", ephemeral=True)

class SelectRoleView(discord.ui.View):
    def __init__(self, r_list, cb):
        super().__init__(timeout=300)
        sel = discord.ui.Select(options=[discord.SelectOption(label=r.name, value=str(r.id)) for r in r_list])
        sel.callback = cb
        self.add_item(sel)

@bot.tree.command(name="editrole", description="Изменить роль (3000)")
@check_maintenance()
async def editrole(interaction: discord.Interaction):
    if get_user(interaction.user.id)['coins'] < 3000: return await interaction.response.send_message("❌ Нет средств!", ephemeral=True)
    roles = [interaction.guild.get_role(r["role_id"]) for r in list(custom_roles_coll.find({"user_id": interaction.user.id})) if interaction.guild.get_role(r["role_id"])]
    if not roles: return await interaction.response.send_message("❌ Нет ролей!", ephemeral=True)
    async def cb(i): await i.response.send_modal(EditRoleModal(i.guild.get_role(int(i.data['values'][0])), 3000))
    await interaction.response.send_message(embed=discord.Embed(title="🛠️ РЕДАКТИРОВАНИЕ", color=0x3498DB), view=SelectRoleView(roles, cb), ephemeral=True)

@bot.tree.command(name="deleterole", description="Удалить роль (5000)")
@check_maintenance()
async def cb(i):
        rid = int(i.data['values'][0])
        r = i.guild.get_role(rid)
        update_coins(i.user.id, -5000)
        custom_roles_coll.delete_one({"role_id": rid})
        auction_coll.delete_one({"role_id": rid})
        if r: 
            try: 
                await r.delete()
            except: 
                pass
        await i.response.send_message("🗑️ Роль удалена!", ephemeral=True)

@bot.tree.command(name="settitle", description="Выбрать титул")
@check_maintenance()
async def settitle(interaction: discord.Interaction):
    t_list = [r["title_name"] for r in list(titles_coll.find({"user_id": interaction.user.id}))]
    if not t_list: return await interaction.response.send_message("❌ Нет титулов!", ephemeral=True)
    sel = discord.ui.Select(options=[discord.SelectOption(label=t, value=t) for t in t_list])
    async def cb(i):
        users_coll.update_one({"_id": i.user.id}, {"$set": {"special_title": i.data['values'][0]}})
        await i.response.send_message(f"✅ Титул изменен на: **{i.data['values'][0]}**!", ephemeral=True)
    sel.callback = cb
    v = discord.ui.View(timeout=300); v.add_item(sel)
    await interaction.response.send_message(embed=discord.Embed(title="👑 ВЫБОР ТИТУЛА", color=0xFFD700), view=v, ephemeral=True)

# ==========================================
# 7. ГИЛЬДИИ (ПОЛНЫЙ ФУНКЦИОНАЛ)
# ==========================================
class GuildModal(discord.ui.Modal):
    inp = discord.ui.TextInput(label="Ввод", max_length=30)
    def __init__(self, mode, gname=""): super().__init__(title="Гильдия"); self.mode, self.gn = mode, gname
    async def on_submit(self, i):
        val = self.inp.value.strip()
        if self.mode == "create":
            if get_user(i.user.id)['coins'] < 25000: return await i.response.send_message("❌ Нужно 25k!", ephemeral=True)
            if guilds_coll.find_one({"guild_name": {"$regex": f"^{val}$", "$options": "i"}}): return await i.response.send_message("❌ Имя занято!", ephemeral=True)
            users_coll.update_one({"_id": i.user.id}, {"$inc": {"coins": -25000}, "$set": {"guild_id": val}})
            guilds_coll.insert_one({"guild_name": val, "leader_id": i.user.id, "co_leaders": [], "bank": 0, "is_private": False, "entry_fee": 0})
            await i.response.send_message(f"🏰 Создана: **{val}**!", ephemeral=True)
        elif self.mode == "deposit":
            try: v = int(val)
            except: return await i.response.send_message("❌ Ошибка!", ephemeral=True)
            if v <= 0 or get_user(i.user.id)['coins'] < v: return await i.response.send_message("❌ Ошибка суммы!", ephemeral=True)
            update_coins(i.user.id, -v)
            guilds_coll.update_one({"guild_name": self.gn}, {"$inc": {"bank": v}})
            await i.response.send_message(f"✅ Внесено {v}!", ephemeral=True)
        elif self.mode == "fee":
            try: v = int(val)
            except: return await i.response.send_message("❌ Ошибка!", ephemeral=True)
            guilds_coll.update_one({"guild_name": self.gn}, {"$set": {"entry_fee": max(0, v)}})
            await i.response.send_message(f"✅ Цена: {max(0, v)}.", ephemeral=True)

class GuildMainView(discord.ui.View):
    def __init__(self, uid):
        super().__init__(timeout=300)
        self.gn = get_user(uid).get('guild_id')
        
    @discord.ui.button(label="Создать (25k)", style=discord.ButtonStyle.green, emoji="🏰", row=0)
    async def cr(self, i, b): await i.response.send_modal(GuildModal("create"))
    
    @discord.ui.button(label="Вступить", style=discord.ButtonStyle.blurple, emoji="🤝", row=0)
    async def join_g(self, i, b):
        if self.gn: return await i.response.send_message("❌ Вы уже состоите в гильдии!", ephemeral=True)
        await i.response.send_message("🛠️ Меню заявок открыто. (Функционал в процессе восстановления)", ephemeral=True)
        
    @discord.ui.button(label="Информация", style=discord.ButtonStyle.blurple, emoji="🛡️", row=0)
    async def inf(self, i, b):
        if not self.gn: return await i.response.send_message("❌ Нет гильдии!", ephemeral=True)
        g = guilds_coll.find_one({"guild_name": self.gn})
        m = list(users_coll.find({"guild_id": self.gn}))
        emb = discord.Embed(title=f"🛡️ {self.gn}", color=0x9B59B6)
        emb.add_field(name="👑 Лидер", value=f"<@{g['leader_id']}>", inline=False)
        emb.add_field(name="💰 Казна", value=f"{g.get('bank', 0):,}", inline=True)
        emb.add_field(name="🎟️ Вход", value=f"{g.get('entry_fee', 0):,}", inline=True)
        emb.add_field(name=f"👥 Участники ({len(m)})", value=", ".join([f"<@{x['_id']}>" for x in m]), inline=False)
        await i.response.send_message(embed=emb, ephemeral=True)
        
    @discord.ui.button(label="Пополнить", style=discord.ButtonStyle.grey, emoji="💰", row=1)
    async def dep(self, i, b):
        if not self.gn: return await i.response.send_message("❌ Нет гильдии!", ephemeral=True)
        await i.response.send_modal(GuildModal("deposit", self.gn))
        
    @discord.ui.button(label="Панель лидера", style=discord.ButtonStyle.grey, emoji="⚙️", row=1)
    async def leader_panel(self, i, b):
        if not self.gn: return await i.response.send_message("❌ У вас нет гильдии!", ephemeral=True)
        g = guilds_coll.find_one({"guild_name": self.gn})
        if not g or g.get("leader_id") != i.user.id: return await i.response.send_message("❌ Вы не являетесь лидером!", ephemeral=True)
        await i.response.send_message("🛠️ Панель лидера открыта. (Настройки временно переносятся)", ephemeral=True)
        
    @discord.ui.button(label="Покинуть", style=discord.ButtonStyle.red, emoji="🚪", row=1)
    async def lv(self, i, b):
        if not self.gn: return await i.response.send_message("❌ Нет гильдии!", ephemeral=True)
        g = guilds_coll.find_one({"guild_name": self.gn})
        users_coll.update_one({"_id": i.user.id}, {"$set": {"guild_id": None}})
        if i.user.id in g.get("co_leaders", []): guilds_coll.update_one({"guild_name": self.gn}, {"$pull": {"co_leaders": i.user.id}})
        if g['leader_id'] == i.user.id:
            nm = users_coll.find_one({"guild_id": self.gn})
            if nm: guilds_coll.update_one({"guild_name": self.gn}, {"$set": {"leader_id": nm["_id"]}})
            else: guilds_coll.delete_one({"guild_name": self.gn})
        await i.response.send_message("🚪 Вы покинули гильдию.", ephemeral=True)

@bot.tree.command(name="guild", description="Меню гильдий")
@check_maintenance()
async def guild_menu(interaction: discord.Interaction):
    embed = discord.Embed(title="—— ┌ 🏰 ГИЛЬДИИ АЙНКРАДА ┐ ——", description="Используйте кнопки ниже для взаимодействия:", color=0x2B2D31)
    embed.set_image(url="https://media0.giphy.com/media/v1.Y2lkPTc5MGI3NjExcDdwNWNmdnRyOGJlNW1kYmYzNm12N3Vyc3diaGlzZm92ajZnZ2F1YiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/XG444KXEaA3zW/giphy.gif")
    await interaction.response.send_message(embed=embed, view=GuildMainView(interaction.user.id))

# ==========================================
# 8. АУКЦИОН РОЛЕЙ
# ==========================================
class AuctionPagingView(discord.ui.View):
    def __init__(self, items):
        super().__init__(timeout=300)
        self.items, self.page, self.pp = items, 0, 5
        self.upd()
    def upd(self):
        self.clear_items()
        mx = max(0, (len(self.items) - 1) // self.pp)
        self.add_item(discord.ui.Button(label="Назад", style=discord.ButtonStyle.grey, disabled=self.page == 0, custom_id="prev"))
        self.add_item(discord.ui.Button(label="Вперед", style=discord.ButtonStyle.grey, disabled=self.page >= mx, custom_id="next"))
        slc = self.items[self.page*self.pp : (self.page+1)*self.pp]
        if slc:
            sel = discord.ui.Select(options=[discord.SelectOption(label=x['role_name'], description=f"{x['price']:,}", value=str(x['sale_id'])) for x in slc])
            sel.callback = self.buy; self.add_item(sel)
    async def interaction_check(self, i):
        if i.data.get('custom_id') == "prev": self.page -= 1
        elif i.data.get('custom_id') == "next": self.page += 1
        else: return True
        self.upd(); await i.response.edit_message(embed=self.get_emb(), view=self)
        return False
    async def buy(self, i):
        sid = int(i.data['values'][0])
        item = auction_coll.find_one({"sale_id": sid})
        if not item or i.user.id == item['seller_id'] or get_user(i.user.id)['coins'] < item['price']: 
            return await i.response.send_message("❌ Ошибка покупки!", ephemeral=True)
        update_coins(i.user.id, -item['price'])
        update_coins(item['seller_id'], item['price'])
        custom_roles_coll.update_one({"role_id": item['role_id']}, {"$set": {"user_id": i.user.id}})
        auction_coll.delete_one({"sale_id": sid})
        r = i.guild.get_role(item['role_id'])
        if r:
            try: await i.user.add_roles(r)
            except: pass
        await i.response.send_message(f"🎉 Куплено: **{item['role_name']}**!", ephemeral=True)
    def get_emb(self):
        e = discord.Embed(title="🏛️ АУКЦИОН", color=0xFFD700)
        for it in self.items[self.page*self.pp : (self.page+1)*self.pp]:
            e.add_field(name=f"📦 {it['role_name']}", value=f"Цена: {it['price']:,}\nПродавец: <@{it['seller_id']}>", inline=False)
        return e

@bot.tree.command(name="auction", description="Аукцион")
@check_maintenance()
async def auction(interaction: discord.Interaction):
    items = list(auction_coll.find())
    if not items: return await interaction.response.send_message("📦 Аукцион пуст.", ephemeral=True)
    v = AuctionPagingView(items)
    await interaction.response.send_message(embed=v.get_emb(), view=v, ephemeral=True)

# ==========================================
# 9. АДМИН-КОМАНДЫ (СИСТЕМНЫЕ)
# ==========================================
@bot.tree.command(name="maintenance", description="[АДМИН] Техобслуживание")
async def maint(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    await interaction.response.send_message(f"🛠️ Статус: {'ВКЛЮЧЕН' if MAINTENANCE_MODE else 'ВЫКЛЮЧЕН'}", ephemeral=True)

@bot.tree.command(name="setlevel", description="[АДМИН] Сет этажа")
async def setlevel(i: discord.Interaction, m: discord.Member, l: int):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    get_user(m.id); users_coll.update_one({"_id": m.id}, {"$set": {"level": l, "xp": 0}})
    await check_level_roles(m, l); await i.response.send_message(f"✅ {l} этаж для {m.mention}.", ephemeral=True)

@bot.tree.command(name="setxp", description="[АДМИН] Сет опыта")
async def setxp(i: discord.Interaction, m: discord.Member, xp: int):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    get_user(m.id); users_coll.update_one({"_id": m.id}, {"$set": {"xp": xp}})
    await i.response.send_message(f"✅ {xp} XP для {m.mention}.", ephemeral=True)

@bot.tree.command(name="givexp", description="[АДМИН] Выдать опыт")
async def givexp(i: discord.Interaction, m: discord.Member, a: int):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    await add_xp(i, m.id, a); await i.response.send_message(f"✅ Выдано {a} XP.", ephemeral=True)

@bot.tree.command(name="setcoins", description="[АДМИН] Сет Колов")
async def setcoins(i: discord.Interaction, m: discord.Member, a: int):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    get_user(m.id); users_coll.update_one({"_id": m.id}, {"$set": {"coins": a}})
    await i.response.send_message(f"✅ Баланс {m.mention} = {a}.", ephemeral=True)

@bot.tree.command(name="givecoins", description="[АДМИН] Выдать Колы")
async def givecoins(i: discord.Interaction, m: discord.Member, a: int):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    get_user(m.id); update_coins(m.id, a); await i.response.send_message(f"✅ Выдано {a}.", ephemeral=True)

@bot.tree.command(name="takecoins", description="[АДМИН] Забрать Колы")
async def takecoins(i: discord.Interaction, m: discord.Member, a: int):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    get_user(m.id); update_coins(m.id, -a); await i.response.send_message(f"🔻 Забрано {a}.", ephemeral=True)

@bot.tree.command(name="setstreak", description="[АДМИН] Сет стрика")
async def setstreak(i: discord.Interaction, m: discord.Member, d: int):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    get_user(m.id); users_coll.update_one({"_id": m.id}, {"$set": {"streak": d}})
    await i.response.send_message(f"✅ Стрик {d} дн.", ephemeral=True)

@bot.tree.command(name="resetcd", description="[АДМИН] Сброс КД")
async def resetcd(i: discord.Interaction, m: discord.Member = None):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    t = m or i.user
    users_coll.update_one({"_id": t.id}, {"$set": {"last_daily": 0.0, "last_work": 0.0, "last_crime": 0.0, "last_rob": 0.0}})
    await i.response.send_message(f"⚡ КД сброшены!", ephemeral=True)

@bot.tree.command(name="resetuser", description="[АДМИН] Сброс профиля")
async def resetuser(i: discord.Interaction, m: discord.Member):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    users_coll.delete_one({"_id": m.id}); get_user(m.id)
    await i.response.send_message(f"☢️ Профиль сброшен!", ephemeral=True)

@bot.tree.command(name="resetdb", description="[АДМИН] Полный сброс БД")
async def resetdb(i: discord.Interaction):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    for c in [users_coll, guilds_coll, custom_roles_coll, titles_coll, auction_coll, guild_reqs_coll]: c.delete_many({})
    await i.response.send_message("☢️ БД полностью очищена!", ephemeral=True)

# ==========================================
# 10. НОВОСТИ, СТРИМЫ, ИДЕИ, ВЕРИФИКАЦИЯ
# ==========================================
class NewsModal(discord.ui.Modal, title="Новость"):
    nt = discord.ui.TextInput(label="Заголовок")
    nx = discord.ui.TextInput(label="Текст", style=discord.TextStyle.paragraph)
    ng = discord.ui.TextInput(label="GIF", required=False)
    async def on_submit(self, i):
        embed = discord.Embed(title=f"📢 {self.nt.value}", description=self.nx.value, color=0x00BFFF)
        if self.ng.value: embed.set_image(url=self.ng.value)
        await i.channel.send(content="@everyone", embed=embed)
        await i.response.send_message("✅ Готово!", ephemeral=True)

@bot.tree.command(name="setup_news", description="[АДМИН] Новость")
async def setup_news(i: discord.Interaction):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    await i.response.send_modal(NewsModal())

@bot.tree.command(name="stream", description="[АДМИН] Стрим")
async def stream(i: discord.Interaction):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    embed = discord.Embed(title="🔴 ПРЯМОЙ ЭФИР", description="**Залетайте на стрим!**\n🔗 [КЛИКАЙ СЮДА](https://www.twitch.tv/treihaolvl31)", color=0x9146FF)
    embed.set_image(url="https://media1.tenor.com/images/HsNUWd_R6RYAAAAC/sword-art-online-sao.gif")
    await i.channel.send(content="@everyone", embed=embed)
    await i.response.send_message("✅ Анонс отправлен!", ephemeral=True)

class IdeaModal(discord.ui.Modal, title="Идея"):
    itx = discord.ui.TextInput(label="Текст", style=discord.TextStyle.paragraph)
    async def on_submit(self, i):
        ch = i.guild.get_channel(ADMIN_IDEA_CHANNEL_ID)
        emb = discord.Embed(title="⏳ НОВАЯ ИДЕЯ", description=f"**От:** {i.user.mention}\n\n{self.itx.value}", color=0xF1C40F)
        
        class AdminIdeaView(discord.ui.View):
            @discord.ui.button(label="Одобрить", style=discord.ButtonStyle.green, emoji="✅")
            async def a(self, ia, b):
                pch = ia.guild.get_channel(PUBLIC_IDEA_CHANNEL_ID)
                pe = discord.Embed(title="✨ ОДОБРЕННАЯ ИДЕЯ", description=f"{self.itx}\n\n**От:** {i.user.mention}", color=0x2B2D31)
                msg = await pch.send(embed=pe); await msg.add_reaction("👍"); await msg.add_reaction("👎")
                for c in self.children: c.disabled = True
                await ia.response.edit_message(content="✅ Одобрено", view=self)
            @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌")
            async def r(self, ia, b):
                for c in self.children: c.disabled = True
                await ia.response.edit_message(content="❌ Отклонено", view=self)
                
        await ch.send(embed=emb, view=AdminIdeaView())
        await i.response.send_message("✅ Отправлено!", ephemeral=True)

@bot.tree.command(name="idea", description="Предложить идею")
@check_maintenance()
async def idea(i: discord.Interaction): await i.response.send_modal(IdeaModal())

@bot.tree.command(name="setup_verify", description="[АДМИН] Верификация инфо")
async def setup_verify(i: discord.Interaction):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    emb = discord.Embed(title="🛡️ ИДЕНТИФИКАЦИЯ", description="Зайдите в войс Ожидание верификации...", color=0x3498DB)
    emb.set_image(url="https://media1.tenor.com/images/zLQ4_cEQY0AAAAAC/sao.gif")
    await i.channel.send(embed=emb); await i.response.send_message("✅ Установлено.", ephemeral=True)

@bot.tree.command(name="verify", description="[САППОРТ] Проверить игрока")
@app_commands.choices(gender=[app_commands.Choice(name="Мужчина", value="♂️"), app_commands.Choice(name="Женщина", value="♀️")])
async def verify_user(i: discord.Interaction, m: discord.Member, gender: str):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    role = discord.utils.get(i.guild.roles, name=gender)
    if not role: return await i.response.send_message("❌ Роль не найдена.", ephemeral=True)
    
    try:
        await m.add_roles(role)
        ur = discord.utils.get(i.guild.roles, name="unverify")
        if ur in m.roles: await m.remove_roles(ur)
        
        await i.response.send_message(embed=discord.Embed(title="✅ ВЕРИФИКАЦИЯ УСПЕШНА", description=f"Статус {gender} выдан.", color=0x2ECC71), ephemeral=True)
        ch = i.guild.get_channel(DOCS_CHANNEL_ID)
        if ch:
            d_emb = discord.Embed(title="📁 DOCС", color=0x2B2D31)
            d_emb.add_field(name="Игрок", value=m.mention); d_emb.add_field(name="Статус", value=gender)
            await ch.send(embed=d_emb)
    except: await i.response.send_message("❌ Ошибка.", ephemeral=True)

@bot.tree.command(name="setup_ranks", description="[АДМИН] Ранги инфо")
async def setup_ranks(i: discord.Interaction):
    if not is_admin_or_mod(i.user): return await i.response.send_message("❌ Нет прав!", ephemeral=True)
    emb = discord.Embed(title="⚡ ЭТАЖИ И НАГРАДЫ", description="Информация об иерархии башни...", color=0xE02653)
    emb.set_image(url="https://media1.tenor.com/images/zLQ4_cEQY0AAAAAC/sao.gif")
    await i.channel.send(embed=emb); await i.response.send_message("✅ Готово.", ephemeral=True)

# Запуск
if __name__ == "__main__":
    keep_alive()
    bot.run(os.getenv("TOKEN"))
