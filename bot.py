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
# 0. ФОРМАТИРОВАНИЕ ТЕКСТА (ЦВЕТА)
# ==========================================
def text_blue(text):
    return f"```ansi\n\u001b[1;36m{text}\u001b[0m\n```"

def text_red(text):
    return f"```diff\n- {text}\n```"

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
    roles = [role.name.lower() for role in member.roles]
    return any(role in roles for role in ["модератор", "moderator", "администратор", "administrator", "саппорт", "support", "founder", "co-founder", "content maker", "sigmo brazzers"])

def check_maintenance():
    async def predicate(interaction: discord.Interaction) -> bool:
        if MAINTENANCE_MODE and not is_admin_or_mod(interaction.user):
            await interaction.response.send_message("🛠️ **[ SYSTEM ALERT: КАРДИНАЛ АКТИВЕН ]**\nНа сервере проводятся технические работы.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

async def check_level_roles(member: discord.Member, current_level: int):
    highest_role_name = next((name for level, name in ROLES_MAPPING.items() if current_level >= level), None)
    if not highest_role_name: return

    highest_role = discord.utils.get(member.guild.roles, name=highest_role_name)
    roles_to_remove = [discord.utils.get(member.guild.roles, name=name) for name in ROLES_MAPPING.values() if name != highest_role_name]
    roles_to_remove = [role for role in roles_to_remove if role and role in member.roles]
    
    if roles_to_remove:
        try: await member.remove_roles(*roles_to_remove)
        except Exception: pass

    if highest_role and highest_role not in member.roles:
        try:
            await member.add_roles(highest_role)
            await member.send(f"🎉 Поздравляем! Вы прорвались на **{current_level} этаж** Айнкрада и получили элитный статус **{highest_role_name}**!")
        except Exception: pass

async def add_xp(interaction_or_member, user_id: int, amount: int):
    user_data = get_user(user_id)
    xp, level = user_data['xp'] + amount, user_data['level']
    leveled_up = False
    
    get_next_xp = lambda lvl: int(35 * (lvl ** 1.85) + 80 * lvl + 40)
    
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
                except Exception: pass

# ==========================================
# 3. ИВЕНТЫ (АВТОМОД, ВОЙС, ПРИВЕТСТВИЕ)
# ==========================================
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Бот {bot.user} запущен и полностью готов к работе в Айнкраде!")

def is_afk(voice_state): return voice_state.self_mute or voice_state.mute or voice_state.self_deaf or voice_state.deaf or voice_state.afk

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot: return
    user_id = member.id
    was_afk = before.channel is None or is_afk(before)
    is_afk_now = after.channel is None or is_afk(after)

    if was_afk and not is_afk_now:
        voice_start_times[user_id] = time.time()
    elif not was_afk and is_afk_now and user_id in voice_start_times:
        voice_accumulated[user_id] = voice_accumulated.get(user_id, 0.0) + (time.time() - voice_start_times.pop(user_id))

    if before.channel and not after.channel:
        if user_id in voice_start_times:
            voice_accumulated[user_id] = voice_accumulated.get(user_id, 0.0) + (time.time() - voice_start_times.pop(user_id))
        
        total_time = voice_accumulated.pop(user_id, 0.0)
        minutes = int(total_time // 60)
        if minutes >= 1:
            users_coll.update_one({"_id": user_id}, {"$inc": {"coins": minutes, "voice_time": int(total_time)}}, upsert=True)
            await add_xp(member, user_id, minutes)
            try:
                embed = discord.Embed(title="─────────────── ┌ 🎙️ ВОЙС-АКТИВНОСТЬ ┐ ───────────────", description="Ваш сеанс связи был успешно завершен. Данные сохранены.", color=0x00BFFF)
                embed.add_field(name="🪙 Колы", value=text_blue(f"+{minutes:,}"), inline=True)
                embed.add_field(name="⚡ Опыт", value=text_blue(f"+{minutes:,} XP"), inline=True)
                embed.add_field(name="⏱️ Время", value=text_blue(f"{minutes} мин."), inline=True)
                embed.set_footer(text="Cardinal Anti-AFK System • Айнкрад")
                await member.send(embed=embed)
            except Exception: pass

@bot.event
async def on_member_join(member):
    role = discord.utils.get(member.guild.roles, name="unverify")
    if role: 
        try: await member.add_roles(role)
        except Exception: pass
        
    welcome_channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if welcome_channel:
        content_msg = f"Добро пожаловать в **Айнкрад**, {member.mention}!"
        embed = discord.Embed(color=0x2B2D31)
        embed.set_author(name=f"Участник #{member.guild.member_count}", icon_url=member.display_avatar.url)
        embed.set_image(url="https://media2.giphy.com/media/v1.Y2lkPTc5MGI3NjExZWFmN3l4dDZleDhmdDJ0Y3MxcDlhMzB5cWs4dHgxM29na2Q2ZmQ0diZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/12wr8S2n5fL8lO/giphy.gif")
        try: await welcome_channel.send(content=content_msg, embed=embed)
        except Exception: pass

class MediaModerationView(discord.ui.View):
    def __init__(self, author_id, channel_id, content_text, files_data):
        super().__init__(timeout=None)
        self.author_id = author_id
        self.channel_id = channel_id
        self.content_text = content_text
        self.files_data = files_data

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_or_mod(interaction.user): 
            return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
            
        channel = interaction.guild.get_channel(self.channel_id)
        webhooks = await channel.webhooks()
        webhook = discord.utils.get(webhooks, name="Yui Media") or await channel.create_webhook(name="Yui Media")
        
        files = [discord.File(io.BytesIO(file_data["bytes"]), filename=file_data["filename"]) for file_data in self.files_data]
        content = f"**Отправил:** <@{self.author_id}>\n\n{self.content_text}" if self.content_text else f"**Отправил:** <@{self.author_id}>"
        
        msg = await webhook.send(content=content, files=files, username="Yui", avatar_url=bot.user.display_avatar.url, wait=True)
        try: await msg.add_reaction("❤️")
        except Exception: pass

        embed = interaction.message.embeds[0]
        embed.color = 0x2ECC71
        embed.title = "✅ КОНТЕНТ ОДОБРЕН И ОПУБЛИКОВАН"
        embed.add_field(name="Одобрил", value=interaction.user.mention, inline=False)
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        await add_xp(interaction, self.author_id, random.randint(2, 5))

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_or_mod(interaction.user): 
            return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
            
        embed = interaction.message.embeds[0]
        embed.color = 0xE74C3C
        embed.title = "❌ КОНТЕНТ ОТКЛОНЕН"
        embed.add_field(name="Отклонил", value=interaction.user.mention, inline=False)
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild: return
    if MAINTENANCE_MODE and not is_admin_or_mod(message.author): return

    is_privileged = is_admin_or_mod(message.author)
    user_id = message.author.id
    content_lower = message.content.lower().strip()
    attachments_count = len(message.attachments)
    violation_reason = None
    current_time = time.time()

    if not is_privileged:
        is_media = any(domain in content_lower for domain in ["tenor.com", "giphy.com", "imgur.com", "discordapp.com", "discord.com", "pinimg.com", "klipy.com"]) or ".gif" in content_lower
        has_stream = any(platform in content_lower for platform in ["twitch.tv", "youtube.com/live", "kick.com", "trovo.live", "vkplay.live", "youtu.be", "tiktok.com"])
        has_link = any(p in content_lower for p in ["http://", "https://", "www."]) and not is_media and not has_stream
        
        is_fast_flood = (current_time - user_last_message_time.get(user_id, 0)) < 1.5
        user_last_message_time[user_id] = current_time
        letters = [char for char in message.content if char.isalpha()]
        is_caps_spam = len(letters) > 8 and sum(1 for char in letters if char.isupper()) / len(letters) > 0.7

        if any(invite in content_lower for invite in ["discord.gg/", "discord.com/invite"]): violation_reason = "Попытка публикации стороннего инвайта"
        elif has_link: violation_reason = "Публикация неразрешенной ссылки"
        elif "@everyone" in message.content or "@here" in message.content: violation_reason = "Массовый пинг"
        elif attachments_count >= 3 and message.channel.id not in MEDIA_CHANNELS: violation_reason = "Массовый спам картинками"
        elif is_fast_flood: violation_reason = "Слишком быстрый флуд"
        elif is_caps_spam: violation_reason = "Caps Lock Spam"
        elif message.channel.id in MEDIA_CHANNELS and attachments_count == 0 and not is_media: violation_reason = "В канал можно публиковать только медиафайлы или ссылки на ролики!"
        elif message.channel.id == VIDEO_CHANNEL_ID and not has_stream and attachments_count == 0 and not is_media: violation_reason = "В канал можно публиковать только видеоролики!"
        elif message.channel.id == STREAM_CHANNEL_ID and not has_stream: violation_reason = "В канал можно публиковать только стримы!"

        if violation_reason:
            try: await message.delete()
            except Exception: pass
            
            if current_time - log_cooldowns.get(user_id, 0) > 15.0:
                log_cooldowns[user_id] = current_time
                log_channel = message.guild.get_channel(AUTO_MOD_LOG_CHANNEL_ID)
                if log_channel:
                    embed = discord.Embed(title="⚠️ КАРДИНАЛ: НОВЫЙ ОТЧЕТ АВТОМОДА", color=0xE74C3C)
                    embed.add_field(name="👤 Пользователь", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
                    embed.add_field(name="📅 Аккаунт создан", value=f"<t:{int(message.author.created_at.timestamp())}:R>", inline=True)
                    embed.add_field(name="📁 Канал", value=message.channel.mention, inline=True)
                    embed.add_field(name="⚡ Причина", value=text_blue(violation_reason), inline=False)
                    if message.content:
                        embed.add_field(name="💬 Нарушение", value=text_blue(message.content[:1000]), inline=False)
                    embed.set_footer(text="Aincrad Security Shield • Сообщение удалено")
                    try: await log_channel.send(embed=embed)
                    except Exception: pass
            return

    if message.channel.id in MEDIA_CHANNELS or message.channel.id == VIDEO_CHANNEL_ID:
        if message.attachments or message.embeds or "http" in content_lower or ".gif" in content_lower:
            try:
                mod_channel = bot.get_channel(MEDIA_LOG_CHANNEL_ID)
                files_data = [{"bytes": await attachment.read(), "filename": attachment.filename} for attachment in message.attachments]
                embed = discord.Embed(title="🔍 ПРЕМОДЕРАЦИЯ КОНТЕНТА", description=f"**Автор:** {message.author.mention}\n**Канал:** {message.channel.mention}", color=0xF1C40F)
                if message.content: embed.add_field(name="Текст", value=text_blue(message.content), inline=False)
                if message.attachments: embed.set_image(url=message.attachments[0].url)
                elif message.embeds and message.embeds[0].image: embed.set_image(url=message.embeds[0].image.url)
                
                await mod_channel.send(embed=embed, view=MediaModerationView(message.author.id, message.channel.id, message.content, files_data))
                await message.delete()
            except Exception: pass
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
    user_data = get_user(target.id)
    
    embed = discord.Embed(title="🌐 БАНКОВСКИЙ СЧЕТ АЙНКРАДА", color=0x00BFFF)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="💳 Владелец", value=f"{target.mention}", inline=False)
    embed.add_field(name="💰 Баланс", value=text_blue(f"{user_data['coins']:,} Колов"), inline=False)
    embed.set_footer(text="Aincrad Economy System")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="profile", description="Посмотреть подробный игровой профиль")
@check_maintenance()
async def profile(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    
    # 1. Загрузочный экран (SAO Прелоадер)
    embed_load = discord.Embed(color=0x2B2D31)
    embed_load.set_author(name="Профиль")
    embed_load.description = f"{interaction.user.mention}, профиль загружается..."
    embed_load.set_thumbnail(url=target.display_avatar.url)
    
    await interaction.response.send_message(embed=embed_load)
    
    # Эффект подгрузки системы
    await asyncio.sleep(1.5)
    
    # 2. Формирование основного профиля (Стиль Aruku/Aincrad)
    user_data = get_user(target.id)
    next_level_xp = int(35 * (user_data['level'] ** 1.85) + 80 * user_data['level'] + 40)
    
    # Длинный бар для XP
    progress = int((user_data['xp'] / next_level_xp) * 20 if next_level_xp > 0 else 0)
    bar_filled = "█" * progress
    bar_empty = "▒" * (20 - progress)
    full_bar = f"{bar_filled}{bar_empty}"
    
    voice_hours = int(user_data['voice_time'] // 3600)
    voice_minutes = int((user_data['voice_time'] % 3600) // 60)

    embed = discord.Embed(color=0x00BFFF) # Технологичный голубой Cyan
    embed.set_author(name=f"ИГРОВОЙ ПРОФИЛЬ: {target.display_name}")
    embed.set_thumbnail(url=target.display_avatar.url)
    
    # Распределяем по колонкам
    embed.add_field(name="⚔️ Этаж башни", value=f"```ansi\n\u001b[1;36m{user_data['level']}\u001b[0m\n```", inline=True)
    embed.add_field(name="🪙 Капитал", value=f"```ansi\n\u001b[1;36m{user_data['coins']:,} Колов\u001b[0m\n```", inline=True)
    embed.add_field(name="🔥 Стрик входов", value=f"```ansi\n\u001b[1;36m{user_data['streak']} дн.\u001b[0m\n```", inline=True)
    
    embed.add_field(name="🎙️ Часы в Voice", value=f"```ansi\n\u001b[1;36m{voice_hours} ч. {voice_minutes} м.\u001b[0m\n```", inline=True)
    embed.add_field(name="🏰 Гильдия", value=f"```ansi\n\u001b[1;36m{user_data['guild_id'] or 'Нет'}\u001b[0m\n```", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True) # Пустое поле для ровности сетки
    
    embed.add_field(name="✨ Активный титул", value=f"```ansi\n\u001b[1;36m{user_data['special_title']}\u001b[0m\n```", inline=False)
    
    if user_data.get("partner_id"):
        embed.add_field(name="💞 Партнер", value=f"<@{user_data['partner_id']}>", inline=False)

    # XP Бар на всю ширину снизу
    xp_text = f"**📊 Прогресс опыта (XP)**\n{user_data['xp']} / {next_level_xp} XP\n"
    xp_bar = f"```ansi\n\u001b[1;36m{full_bar}\u001b[0m\n```"
    embed.add_field(name="\u200b", value=xp_text + xp_bar, inline=False)
    
    embed.set_footer(text="Aincrad Status Management System")
    
    await interaction.edit_original_response(embed=embed)

class MarryAcceptView(discord.ui.View):
    def __init__(self, proposer: discord.Member, target: discord.Member):
        super().__init__(timeout=300)
        self.proposer = proposer
        self.target = target

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, emoji="💍")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id: 
            return await interaction.response.send_message("❌ Не для вас!", ephemeral=True)
        if get_user(self.proposer.id)['coins'] < 3000: 
            return await interaction.response.send_message("❌ У инициатора нет средств!", ephemeral=True)
        
        for child in self.children: child.disabled = True
        
        role = discord.utils.get(interaction.guild.roles, name="💞")
        if role:
            try: 
                await self.proposer.add_roles(role)
                await self.target.add_roles(role)
            except Exception: pass
        
        update_coins(self.proposer.id, -3000)
        current_time = time.time()
        
        users_coll.update_one({"_id": self.proposer.id}, {"$set": {"partner_id": self.target.id, "marry_time": current_time}})
        users_coll.update_one({"_id": self.target.id}, {"$set": {"partner_id": self.proposer.id, "marry_time": current_time}})
        
        embed = discord.Embed(title="💖 УСПЕШНО ПОЖЕНИЛИСЬ!", description=f"{self.target.mention} и {self.proposer.mention} теперь состоят в законном браке!", color=0xFF69B4)
        embed.set_image(url="https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExcmwwc2U1cDg3ZHUzcjZ6ZG9ieGhlZ2llcGhsNzgzeTE3Y3k0bHFxYyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/nyGFcsP0kAobm/giphy.gif")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Отказать", style=discord.ButtonStyle.red, emoji="💔")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id: 
            return await interaction.response.send_message("❌ Не для вас!", ephemeral=True)
            
        for child in self.children: child.disabled = True
        
        embed = discord.Embed(title="💔 ОТКАЗ", description=f"{self.target.mention} отверг(ла) предложение.", color=0x2B2D31)
        embed.set_image(url="https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExeXo0aXNtbDVpOHY0NmN5d3NjcnBvdmJrZ2hnYm13dHV3ZnllZ2E3YiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/oS56qcrdYDBw4/giphy.gif")
        await interaction.response.edit_message(embed=embed, view=self)

@bot.tree.command(name="marry", description="Сделать предложение руки и сердца (3000 Колов)")
@check_maintenance()
async def marry(interaction: discord.Interaction, member: discord.Member):
    if member.id == interaction.user.id or member.bot: 
        return await interaction.response.send_message("❌ Ошибка цели!", ephemeral=True)
    if get_user(interaction.user.id)['coins'] < 3000: 
        return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)
        
    if get_user(interaction.user.id).get('partner_id') or get_user(member.id).get('partner_id'):
        return await interaction.response.send_message("❌ Один из вас уже в браке!", ephemeral=True)
        
    embed = discord.Embed(title="💍 ПРЕДЛОЖЕНИЕ", description=f"{member.mention}, игрок {interaction.user.mention} предлагает вам вступить в брак!\nВы согласны?", color=0x2B2D31)
    await interaction.response.send_message(content=member.mention, embed=embed, view=MarryAcceptView(interaction.user, member))

@bot.tree.command(name="divorce", description="Расторгнуть брак (1000 Колов)")
@check_maintenance()
async def divorce(interaction: discord.Interaction):
    user_data = get_user(interaction.user.id)
    if not user_data.get("partner_id"): 
        return await interaction.response.send_message("❌ Вы не в браке!", ephemeral=True)
    if user_data['coins'] < 1000: 
        return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)
        
    partner_id = user_data["partner_id"]
    users_coll.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -1000}, "$set": {"partner_id": None, "marry_time": 0.0}})
    users_coll.update_one({"_id": partner_id}, {"$set": {"partner_id": None, "marry_time": 0.0}})
    
    role = discord.utils.get(interaction.guild.roles, name="💞")
    if role:
        try:
            await interaction.user.remove_roles(role)
            partner_member = interaction.guild.get_member(partner_id)
            if partner_member: await partner_member.remove_roles(role)
        except Exception: pass
        
    await interaction.response.send_message(embed=discord.Embed(title="💔 РАЗВОД", description=f"Вы расторгли брак с <@{partner_id}>.", color=0x2B2D31))

@bot.tree.command(name="love_profile", description="Профиль вашей пары")
@check_maintenance()
async def love_profile(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    user_data = get_user(target.id)
    
    if not user_data.get("partner_id"): 
        return await interaction.response.send_message("❌ Игрок не в браке.", ephemeral=True)
        
    days = int((time.time() - user_data.get("marry_time", time.time())) // 86400)
    
    embed = discord.Embed(color=0x2B2D31).set_author(name=f"Любовный профиль | {target.display_name}", icon_url=target.display_avatar.url)
    embed.add_field(name="💞 Партнеры", value=f"<@{target.id}> и <@{user_data['partner_id']}>", inline=False)
    embed.add_field(name="⏳ Вместе", value=text_blue(f"{days} дн."), inline=False)
    embed.set_image(url="https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExcmwwc2U1cDg3ZHUzcjZ6ZG9ieGhlZ2llcGhsNzgzeTE3Y3k0bHFxYyZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/nyGFcsP0kAobm/giphy.gif")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Глобальный топ игроков")
@app_commands.choices(category=[
    app_commands.Choice(name="Этажи", value="level"), 
    app_commands.Choice(name="Колы", value="coins"), 
    app_commands.Choice(name="Войс", value="voice"), 
    app_commands.Choice(name="Гильдии", value="guilds")
])
@check_maintenance()
async def leaderboard(interaction: discord.Interaction, category: str = "level"):
    if category == "level":
        top = list(users_coll.find().sort([("level", -1), ("xp", -1)]).limit(10))
        title = "🏆 ТОП-10 (ЭТАЖИ)"
        desc = "\n".join([f"`#{i}` <@{u['_id']}> — **{u.get('level', 1)} этаж**" for i, u in enumerate(top, 1)])
    elif category == "coins":
        top = list(users_coll.find().sort("coins", -1).limit(10))
        title = "💰 ТОП-10 (КОЛЫ)"
        desc = "\n".join([f"`#{i}` <@{u['_id']}> — **{u.get('coins', 0):,} Колов**" for i, u in enumerate(top, 1)])
    elif category == "guilds":
        top = list(guilds_coll.find().sort("bank", -1).limit(10))
        title = "🏰 ТОП-10 ГИЛЬДИЙ"
        desc = "\n".join([f"`#{i}` **{g['guild_name']}** — **{g.get('bank', 0):,} Колов**" for i, g in enumerate(top, 1)])
    else:
        top = list(users_coll.find().sort("voice_time", -1).limit(10))
        title = "🎙️ ТОП-10 (ВОЙС)"
        desc = "\n".join([f"`#{i}` <@{u['_id']}> — **{int(u.get('voice_time',0)//3600)}ч {int((u.get('voice_time',0)%3600)//60)}м**" for i, u in enumerate(top, 1)])

    await interaction.response.send_message(embed=discord.Embed(title=title, description=desc or "Пусто.", color=0x00BFFF))

@bot.tree.command(name="pay", description="Перевод Колов (Комиссия 10%)")
@check_maintenance()
async def pay(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0 or member.id == interaction.user.id: 
        return await interaction.response.send_message("❌ Некорректная сумма/цель!", ephemeral=True)
    if get_user(interaction.user.id)['coins'] < amount: 
        return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)

    get_user(member.id)
    fee = int(amount * 0.10)
    
    update_coins(interaction.user.id, -amount)
    update_coins(member.id, amount - fee)

    embed = discord.Embed(title="💸 ПЕРЕВОД УСПЕШЕН", description=f"Транзакция для {member.mention} проведена.", color=0x2B2D31)
    embed.add_field(name="Списано", value=text_blue(f"{amount:,}"), inline=True)
    embed.add_field(name="Зачислено", value=text_blue(f"{amount - fee:,}"), inline=True)
    embed.add_field(name="Комиссия", value=text_red(f"{fee:,}"), inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="daily", description="Ежедневная награда")
@check_maintenance()
async def daily(interaction: discord.Interaction):
    user_data = get_user(interaction.user.id)
    current_time = time.time()
    
    if current_time - user_data['last_daily'] < 86400:
        left = int(86400 - (current_time - user_data['last_daily']))
        return await interaction.response.send_message(f"⏳ Ждите {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)
    
    streak = 0 if user_data['last_daily'] > 0 and current_time - user_data['last_daily'] > 172800 else user_data['streak'] + 1
    multiplier = 1.0 + (streak * 0.15)
    
    reward_coins = int((40 + user_data['level'] * 8) * multiplier)
    reward_xp = int((15 + user_data['level'] * 3) * multiplier)

    users_coll.update_one({"_id": user_data['_id']}, {"$inc": {"coins": reward_coins}, "$set": {"streak": streak, "last_daily": current_time}})
    await add_xp(interaction, user_data['_id'], reward_xp)

    embed = discord.Embed(title="─────────────── ┌ 🎁 ЕЖЕДНЕВНАЯ НАГРАДА ┐ ───────────────", description="Ваш ежедневный бонус успешно зачислен на баланс.", color=0x00FF00)
    embed.add_field(name="🔥 Стрик", value=text_blue(f"{streak} дн."), inline=True)
    embed.add_field(name="🪙 Колы", value=text_blue(f"+{reward_coins}"), inline=True)
    embed.add_field(name="⚡ Опыт", value=text_blue(f"+{reward_xp} XP"), inline=True)
    embed.set_footer(text="Aincrad Economy System")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="work", description="Работа в Айнкраде")
@check_maintenance()
async def work(interaction: discord.Interaction):
    user_data = get_user(interaction.user.id)
    current_time = time.time()
    
    if current_time - user_data['last_work'] < 7200:
        left = int(7200 - (current_time - user_data['last_work']))
        return await interaction.response.send_message(f"⏳ Усталость. Ждите {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)

    earned = random.randint(40, 120) + (user_data['level'] * 2)
    users_coll.update_one({"_id": user_data['_id']}, {"$inc": {"coins": earned}, "$set": {"last_work": current_time}})
    await add_xp(interaction, user_data['_id'], random.randint(10, 20))

    embed = discord.Embed(title="─────────────── ┌ 🛠️ ОТЧЕТ РАБОТЫ ┐ ───────────────", description="Вы успешно выполнили поручение и получили оплату.", color=0x3498DB)
    embed.add_field(name="💰 Награда", value=text_blue(f"+{earned} Колов"), inline=False)
    embed.set_footer(text="Aincrad Economy System")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="crime", description="Рискованная авантюра")
@check_maintenance()
async def crime(interaction: discord.Interaction):
    user_data = get_user(interaction.user.id)
    current_time = time.time()
    
    if current_time - user_data['last_crime'] < 14400:
        left = int(14400 - (current_time - user_data['last_crime']))
        return await interaction.response.send_message(f"⏳ Ждите {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)

    embed = discord.Embed()
    if random.choice([True, False]):
        reward = random.randint(50, 130) + (user_data['level'] * 2)
        users_coll.update_one({"_id": user_data['_id']}, {"$inc": {"coins": reward}, "$set": {"last_crime": current_time}})
        await add_xp(interaction, user_data['_id'], 15)
        embed.title = "─────────────── ┌ 🥷 УСПЕШНАЯ АВАНТЮРА ┐ ───────────────"
        embed.color = 0x2ECC71
        embed.add_field(name="💰 Получено", value=text_blue(f"+{reward} Колов"), inline=False)
    else:
        fine = random.randint(30, 70)
        users_coll.update_one({"_id": user_data['_id']}, {"$set": {"coins": max(0, user_data['coins'] - fine), "last_crime": current_time}})
        embed.title = "─────────────── ┌ ❌ ПОЛИЦИЯ АЙНКРАДА ┐ ───────────────"
        embed.color = 0xE74C3C
        embed.add_field(name="🚨 Штраф", value=text_red(f"{fine} Колов"), inline=False)
        
    embed.set_footer(text="Aincrad Economy System")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rob", description="Карманная кража")
@check_maintenance()
async def rob(interaction: discord.Interaction, member: discord.Member):
    if member.id == interaction.user.id or member.bot: 
        return await interaction.response.send_message("❌ Невозможно!", ephemeral=True)
        
    target_roles = [role.name.lower() for role in member.roles]
    if any(role in target_roles for role in ["неприкасаемый", "модератор"]): 
        return await interaction.response.send_message("🛡️ Защищен иммунитетом!", ephemeral=True)

    attacker = get_user(interaction.user.id)
    target = get_user(member.id)
    current_time = time.time()
    
    if current_time - attacker['last_rob'] < 10800:
        left = int(10800 - (current_time - attacker['last_rob']))
        return await interaction.response.send_message(f"⏳ Ждите {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)
        
    if target['coins'] < 500: 
        return await interaction.response.send_message("❌ У жертвы мало денег.", ephemeral=True)
        
    if attacker['coins'] > 50000 and attacker['coins'] > target['coins'] * 5: 
        return await interaction.response.send_message("❌ Запрещено грабить бедняков!", ephemeral=True)

    potential_amount = max(20, random.randint(int(target['coins'] * 0.05), int(target['coins'] * 0.10)))
    if attacker['coins'] < potential_amount: 
        return await interaction.response.send_message(f"❌ Нужен залог в {potential_amount:,} Колов!", ephemeral=True)

    users_coll.update_one({"_id": attacker['_id']}, {"$set": {"last_rob": current_time}})
    await interaction.response.send_message(embed=discord.Embed(title="🕵️ ОГРАБЛЕНИЕ", description="Вы подкрадываетесь...", color=0x2C3E50))
    await asyncio.sleep(3.0)

    result_embed = discord.Embed(title="─────────────── ┌ 🕵️ ИТОГ ОГРАБЛЕНИЯ ┐ ───────────────")
    if random.choice([True, False]):
        update_coins(attacker['_id'], potential_amount)
        update_coins(target['_id'], -potential_amount)
        await add_xp(interaction, attacker['_id'], 20)
        
        result_embed.color = 0x2ECC71
        result_embed.add_field(name="✅ Статус", value=text_blue(f"Успех! Украдено: +{potential_amount:,} Колов"), inline=False)
        result_embed.set_image(url="https://i.pinimg.com/originals/58/23/81/582381e4e65d4f6a027116695445d649.gif")
    else:
        update_coins(attacker['_id'], -potential_amount)
        result_embed.color = 0xE74C3C
        result_embed.add_field(name="🚨 Статус", value=text_red(f"Заметили! Штраф: {potential_amount:,} Колов"), inline=False)
        result_embed.set_image(url="https://i.pinimg.com/originals/1d/85/80/1d8580859a663c8c58d2aa9ff9dc87c8.gif")
        
    result_embed.set_footer(text="Aincrad Economy System")
    await interaction.edit_original_response(embed=result_embed)

# ==========================================
# 5. АЗАРТНЫЕ ИГРЫ И ДУЭЛЬ
# ==========================================
async def check_gambling(interaction: discord.Interaction, amount: int):
    if amount < 50:
        await interaction.response.send_message("❌ Мин. ставка: 50 Колов!", ephemeral=True)
        return False
    if get_user(interaction.user.id)['coins'] < amount:
        await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)
        return False
    update_coins(interaction.user.id, -amount)
    return True

class DuelAcceptView(discord.ui.View):
    def __init__(self, challenger: discord.Member, target: discord.Member, amount: int):
        super().__init__(timeout=300)
        self.challenger = challenger
        self.target = target
        self.amount = amount

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, emoji="⚔️")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id: 
            return await interaction.response.send_message("❌ Не для вас!", ephemeral=True)
            
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(view=self)
        
        if get_user(self.challenger.id)['coins'] < self.amount or get_user(self.target.id)['coins'] < self.amount:
            return await interaction.followup.send("❌ Недостаточно средств у одного из бойцов!", ephemeral=True)
        
        embed = discord.Embed(title="─────────────── ┌ ⚔️ АРЕНА ┐ ───────────────", description="Бой начинается...", color=0xE67E22)
        embed.add_field(name="Ставка", value=text_blue(f"{self.amount:,} Колов"), inline=False)
        embed.set_image(url="https://media.tenor.com/HsNUWd_R6RYAAAAC/sword-art-online-sao.gif")
        msg = await interaction.followup.send(embed=embed)
        await asyncio.sleep(3.0)
        
        winner = random.choice([self.challenger, self.target])
        loser = self.target if winner == self.challenger else self.challenger
        
        update_coins(winner.id, self.amount)
        update_coins(loser.id, -self.amount)
        await add_xp(interaction, winner.id, 25)
        
        result_embed = discord.Embed(title="─────────────── ┌ ⚔️ ИТОГ БОЯ ┐ ───────────────", color=0x3498DB)
        result_embed.add_field(name="🏆 Победитель", value=winner.mention, inline=False)
        result_embed.add_field(name="💰 Приз", value=text_blue(f"+{self.amount:,} Колов"), inline=False)
        result_embed.set_footer(text="Aincrad Battle System")
        await msg.edit(embed=result_embed, attachments=[])

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="🏃")
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id: 
            return await interaction.response.send_message("❌ Не для вас!", ephemeral=True)
            
        for child in self.children: child.disabled = True
        cancel_embed = discord.Embed(title="⚔️ ОТМЕНА", description=f"{self.target.mention} отклонил вызов.", color=0x2B2D31)
        await interaction.response.edit_message(embed=cancel_embed, view=self)

@bot.tree.command(name="duel", description="Вызвать на дуэль")
@check_maintenance()
async def duel(interaction: discord.Interaction, target: discord.Member, amount: int):
    if target.id == interaction.user.id or target.bot: 
        return await interaction.response.send_message("❌ Ошибка цели!", ephemeral=True)
    if amount < 50: 
        return await interaction.response.send_message("❌ Мин ставка 50!", ephemeral=True)
        
    if get_user(interaction.user.id)['coins'] < amount or get_user(target.id)['coins'] < amount:
        return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)
    
    embed = discord.Embed(title="⚔️ ВЫЗОВ НА АРЕНУ", description=f"{interaction.user.mention} вызывает {target.mention}!", color=0xE67E22)
    embed.add_field(name="Ставка", value=text_blue(f"{amount:,} Колов"), inline=False)
    await interaction.response.send_message(content=target.mention, embed=embed, view=DuelAcceptView(interaction.user, target, amount))

@bot.tree.command(name="dice", description="Игральные кости (Мин: 50)")
@check_maintenance()
async def dice(interaction: discord.Interaction, amount: int):
    if not await check_gambling(interaction, amount): return
    
    embed = discord.Embed(title="🎲 КОСТИ", description="Бросаем...", color=0x9B59B6)
    embed.set_image(url="https://i.pinimg.com/originals/80/9f/ba/809fba531ccbb8e24010696ffa1503e2.gif")
    await interaction.response.send_message(embed=embed)
    await asyncio.sleep(3.0)

    player_roll, bot_roll = random.randint(1, 6), random.randint(1, 6)
    result_embed = discord.Embed(title="─────────────── ┌ 🎲 ИТОГ ┐ ───────────────", color=0x2ECC71 if player_roll > bot_roll else 0xE74C3C if player_roll < bot_roll else 0xF1C40F)
    result_embed.add_field(name="Вы", value=text_blue(player_roll), inline=True)
    result_embed.add_field(name="Бот", value=text_blue(bot_roll), inline=True)
    
    if player_roll > bot_roll:
        update_coins(interaction.user.id, amount * 2)
        result_embed.add_field(name="💼 Выигрыш", value=text_blue(f"+{amount} Колов"), inline=False)
    elif player_roll < bot_roll: 
        result_embed.add_field(name="💼 Проигрыш", value=text_red(f"{amount} Колов"), inline=False)
    else:
        update_coins(interaction.user.id, amount)
        result_embed.description = "🤝 Ничья. Возврат ставки."
        
    result_embed.set_footer(text="Aincrad Casino")
    await interaction.edit_original_response(embed=result_embed, attachments=[])
    await add_xp(interaction, interaction.user.id, random.randint(5, 10))

@bot.tree.command(name="coinflip", description="Монетка (Мин: 50)")
@app_commands.choices(choice=[app_commands.Choice(name="Орел", value="орел"), app_commands.Choice(name="Решка", value="решка")])
@check_maintenance()
async def coinflip(interaction: discord.Interaction, choice: str, amount: int):
    if not await check_gambling(interaction, amount): return
    
    embed = discord.Embed(title="🪙 ОРЕЛ И РЕШКА", description="Бросаем...", color=0xF1C40F)
    embed.set_image(url="https://i.pinimg.com/originals/5d/3b/f5/5d3bf5ea6706bd4dc96cec12418765ea.gif")
    await interaction.response.send_message(embed=embed)
    await asyncio.sleep(3.0)

    result_flip = random.choice(["орел", "решка"])
    result_embed = discord.Embed(title="🪙 РЕЗУЛЬТАТ МОНЕТКИ", color=0x2ECC71 if choice == result_flip else 0xE74C3C)
    result_embed.add_field(name="🎯 Выпало", value=text_blue(result_flip.upper()), inline=False)
    
    if choice == result_flip:
        update_coins(interaction.user.id, amount * 2)
        result_embed.add_field(name="💼 Выигрыш", value=text_blue(f"+{amount} Колов"), inline=False)
    else: 
        result_embed.add_field(name="💼 Проигрыш", value=text_red(f"{amount} Колов"), inline=False)
        
    await interaction.edit_original_response(embed=result_embed, attachments=[])
    await add_xp(interaction, interaction.user.id, random.randint(5, 15))

@bot.tree.command(name="roulette", description="Русская рулетка (Мин: 50)")
@check_maintenance()
async def roulette(interaction: discord.Interaction, amount: int):
    if not await check_gambling(interaction, amount): return
    
    embed = discord.Embed(title="🎯 РУССКАЯ РУЛЕТКА", description="Вращаем барабан...", color=0xE74C3C)
    embed.set_image(url="https://i.pinimg.com/originals/ac/56/c5/ac56c5c7e6037a698e22c9a30a8dccda.gif")
    await interaction.response.send_message(embed=embed)
    await asyncio.sleep(3.0)

    shot = random.choice([True, False, False, False, False, False])
    result_embed = discord.Embed(title="🎯 ИТОГ РУССКОЙ РУЛЕТКИ", color=0xE74C3C if shot else 0x2ECC71)
    
    if not shot:
        update_coins(interaction.user.id, amount * 2)
        result_embed.add_field(name="💀 Барабан", value=text_blue("Пусто (Щелк)"), inline=False)
        result_embed.add_field(name="💼 Выигрыш", value=text_blue(f"+{amount} Колов"), inline=False)
    else:
        result_embed.add_field(name="💀 Барабан", value=text_red("Смертельный выстрел (БАХ)"), inline=False)
        result_embed.add_field(name="💼 Проигрыш", value=text_red(f"{amount} Колов"), inline=False)
        
    await interaction.edit_original_response(embed=result_embed, attachments=[])
    await add_xp(interaction, interaction.user.id, random.randint(10, 20))

# ==========================================
# 6. МАГАЗИН, РОЛИ, ТИТУЛЫ
# ==========================================
class CustomRoleModal(discord.ui.Modal, title="Кастомная роль"):
    role_name = discord.ui.TextInput(label="Название", max_length=50)
    role_color = discord.ui.TextInput(label="HEX Цвет", max_length=6, min_length=6)
    
    def __init__(self, price: int): 
        super().__init__()
        self.price = price
        
    async def on_submit(self, interaction: discord.Interaction):
        role_n = self.role_name.value.strip()
        if custom_roles_coll.find_one({"role_name": {"$regex": f"^{role_n}$", "$options": "i"}}) or discord.utils.get(interaction.guild.roles, name=role_n):
            return await interaction.response.send_message("❌ Роль существует!", ephemeral=True)
            
        try: 
            color_int = int(self.role_color.value.strip(), 16)
        except Exception: 
            return await interaction.response.send_message("❌ Неверный HEX!", ephemeral=True)
        
        update_coins(interaction.user.id, -self.price)
        try:
            new_role = await interaction.guild.create_role(name=role_n, color=discord.Color(color_int))
            await interaction.user.add_roles(new_role)
            custom_roles_coll.insert_one({"role_id": new_role.id, "user_id": interaction.user.id, "role_name": role_n})
            await interaction.response.send_message(f"✅ Роль **{role_n}** создана!", ephemeral=True)
        except Exception:
            update_coins(interaction.user.id, self.price)
            await interaction.response.send_message("❌ Ошибка создания.", ephemeral=True)

class CustomTitleModal(discord.ui.Modal, title="Кастомный титул"):
    title_text = discord.ui.TextInput(label="Текст", max_length=30)
    
    def __init__(self, price: int): 
        super().__init__()
        self.price = price
        
    async def on_submit(self, interaction: discord.Interaction):
        title_t = self.title_text.value.strip()
        users_coll.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}, "$set": {"special_title": title_t}})
        titles_coll.insert_one({"user_id": interaction.user.id, "title_name": title_t})
        await interaction.response.send_message(f"👑 Титул **{title_t}** куплен!", ephemeral=True)

class ShopButtonsView(discord.ui.View):
    def __init__(self): 
        super().__init__(timeout=300)
        
    @discord.ui.button(label="Неприкасаемый (15,000)", style=discord.ButtonStyle.blurple, emoji="🛡️")
    async def buy_untouchable(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = discord.utils.get(interaction.guild.roles, name="Неприкасаемый")
        if not role: 
            return await interaction.response.send_message("❌ Роли нет на сервере.", ephemeral=True)
        if role in interaction.user.roles: 
            return await interaction.response.send_message("❌ Уже есть!", ephemeral=True)
        if get_user(interaction.user.id)['coins'] < 15000: 
            return await interaction.response.send_message("❌ Нет средств!", ephemeral=True)
            
        update_coins(interaction.user.id, -15000)
        await interaction.user.add_roles(role)
        await interaction.response.send_message("🎉 Статус куплен!", ephemeral=True)
        
    @discord.ui.button(label="Кастомная роль (10,000)", style=discord.ButtonStyle.green, emoji="✨")
    async def buy_custom_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        if custom_roles_coll.count_documents({"user_id": interaction.user.id}) >= 2: 
            return await interaction.response.send_message("❌ Лимит ролей!", ephemeral=True)
        if get_user(interaction.user.id)['coins'] < 10000: 
            return await interaction.response.send_message("❌ Нет средств!", ephemeral=True)
            
        await interaction.response.send_modal(CustomRoleModal(10000))
        
    @discord.ui.button(label="Кастомный титул (5,000)", style=discord.ButtonStyle.grey, emoji="👑")
    async def buy_custom_title(self, interaction: discord.Interaction, button: discord.ui.Button):
        if get_user(interaction.user.id)['coins'] < 5000: 
            return await interaction.response.send_message("❌ Нет средств!", ephemeral=True)
            
        await interaction.response.send_modal(CustomTitleModal(5000))

@bot.tree.command(name="shop", description="Магазин предметов")
@check_maintenance()
async def shop(interaction: discord.Interaction):
    embed = discord.Embed(title="🛒 ЦЕНТРАЛЬНЫЙ ИГРОВОЙ МАГАЗИН АЙНКРАДА", description="Добро пожаловать в торговый интерфейс системы. Выберите нужную привилегию для покупки с помощью кнопок ниже.", color=0x2B2D31)
    
    embed.add_field(name="🛡️ Элитный статус «Неприкасаемый»", value=f"{text_blue('Стоимость: 15,000 Колов')}Обеспечивает абсолютный и бессрочный иммунитет от любых попыток карманных краж и грабежей другими игроками.", inline=False)
    embed.add_field(name="✨ Персональная Кастомная Роль", value=f"{text_blue('Стоимость: 10,000 Колов')}Позволяет зарегистрировать собственное уникальное имя роли и персональный цвет в формате HEX с выдачей в ваш профиль.", inline=False)
    embed.add_field(name="👑 Уникальный Кастомный Титул", value=f"{text_blue('Стоимость: 5,000 Колов')}Устанавливает индивидуальный престижный текстовый статус, который отображается в вашем персональном `/profile`.", inline=False)
    
    embed.set_image(url="https://media1.giphy.com/media/v1.Y2lkPTc5MGI3NjExMmZrb210em05Ync0M2p6bnE2anJwZGM2NDk2MG9ieDluN3JzbTk2ZCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/DU3DhzJli9dsc/giphy.gif")
    embed.set_footer(text="Aincrad Economy System • Используйте кнопки интерфейса для взаимодействия")
    await interaction.response.send_message(embed=embed, view=ShopButtonsView())

class EditRoleModal(discord.ui.Modal, title="Редактирование"):
    role_name = discord.ui.TextInput(label="Новое имя", max_length=50)
    role_color = discord.ui.TextInput(label="HEX", max_length=6, min_length=6)
    
    def __init__(self, target_role: discord.Role, price: int): 
        super().__init__()
        self.target_role = target_role
        self.price = price
        
    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.role_name.value
        if custom_roles_coll.find_one({"role_name": new_name, "role_id": {"$ne": self.target_role.id}}): 
            return await interaction.response.send_message("❌ Имя занято!", ephemeral=True)
            
        try: 
            color_int = int(self.role_color.value, 16)
        except Exception: 
            return await interaction.response.send_message("❌ Неверный HEX!", ephemeral=True)
            
        update_coins(interaction.user.id, -self.price)
        await self.target_role.edit(name=new_name, color=discord.Color(color_int))
        
        custom_roles_coll.update_one({"role_id": self.target_role.id}, {"$set": {"role_name": new_name}})
        auction_coll.update_one({"role_id": self.target_role.id}, {"$set": {"role_name": new_name}})
        
        await interaction.response.send_message(f"✅ Обновлено на **{new_name}**!", ephemeral=True)

class SelectRoleView(discord.ui.View):
    def __init__(self, roles_list, callback_func):
        super().__init__(timeout=300)
        select = discord.ui.Select(options=[discord.SelectOption(label=r.name, value=str(r.id)) for r in roles_list])
        select.callback = callback_func
        self.add_item(select)

@bot.tree.command(name="editrole", description="Изменить роль (3000)")
@check_maintenance()
async def editrole(interaction: discord.Interaction):
    if get_user(interaction.user.id)['coins'] < 3000: 
        return await interaction.response.send_message("❌ Нет средств!", ephemeral=True)
        
    roles = [interaction.guild.get_role(r["role_id"]) for r in list(custom_roles_coll.find({"user_id": interaction.user.id})) if interaction.guild.get_role(r["role_id"])]
    if not roles: 
        return await interaction.response.send_message("❌ Нет ролей!", ephemeral=True)
        
    async def edit_callback(inter: discord.Interaction): 
        await inter.response.send_modal(EditRoleModal(inter.guild.get_role(int(inter.data['values'][0])), 3000))
        
    await interaction.response.send_message(embed=discord.Embed(title="🛠️ РЕДАКТИРОВАНИЕ", color=0x3498DB), view=SelectRoleView(roles, edit_callback), ephemeral=True)

@bot.tree.command(name="deleterole", description="Удалить роль (5000)")
@check_maintenance()
async def deleterole(interaction: discord.Interaction):
    if get_user(interaction.user.id)['coins'] < 5000: 
        return await interaction.response.send_message("❌ Нет средств!", ephemeral=True)
        
    roles = [interaction.guild.get_role(r["role_id"]) for r in list(custom_roles_coll.find({"user_id": interaction.user.id})) if interaction.guild.get_role(r["role_id"])]
    if not roles: 
        return await interaction.response.send_message("❌ Нет ролей!", ephemeral=True)

    async def delete_callback(inter: discord.Interaction):
        role_id = int(inter.data['values'][0])
        role_to_del = inter.guild.get_role(role_id)
        
        update_coins(inter.user.id, -5000)
        custom_roles_coll.delete_one({"role_id": role_id})
        auction_coll.delete_one({"role_id": role_id})
        
        if role_to_del: 
            try: await role_to_del.delete()
            except Exception: pass
            
        await inter.response.send_message("🗑️ Роль удалена!", ephemeral=True)

    await interaction.response.send_message(embed=discord.Embed(title="🗑️ УДАЛЕНИЕ", color=0xE74C3C), view=SelectRoleView(roles, delete_callback), ephemeral=True)

@bot.tree.command(name="settitle", description="Выбрать титул")
@check_maintenance()
async def settitle(interaction: discord.Interaction):
    titles_list = [t["title_name"] for t in list(titles_coll.find({"user_id": interaction.user.id}))]
    if not titles_list: 
        return await interaction.response.send_message("❌ Нет титулов!", ephemeral=True)
        
    select = discord.ui.Select(options=[discord.SelectOption(label=t, value=t) for t in titles_list])
    
    async def title_callback(inter: discord.Interaction):
        users_coll.update_one({"_id": inter.user.id}, {"$set": {"special_title": inter.data['values'][0]}})
        await inter.response.send_message(f"✅ Титул изменен на: **{inter.data['values'][0]}**!", ephemeral=True)
        
    select.callback = title_callback
    view = discord.ui.View(timeout=300)
    view.add_item(select)
    
    await interaction.response.send_message(embed=discord.Embed(title="👑 ВЫБОР ТИТУЛА", color=0xFFD700), view=view, ephemeral=True)

# ==========================================
# 7. ГИЛЬДИИ (ПОЛНЫЙ ФУНКЦИОНАЛ)
# ==========================================
class GuildModal(discord.ui.Modal):
    input_field = discord.ui.TextInput(label="Ввод", max_length=30)
    
    def __init__(self, mode: str, guild_name: str = ""): 
        super().__init__(title="Гильдия")
        self.mode = mode
        self.guild_name = guild_name
        
    async def on_submit(self, interaction: discord.Interaction):
        value = self.input_field.value.strip()
        
        if self.mode == "create":
            if get_user(interaction.user.id)['coins'] < 25000: 
                return await interaction.response.send_message("❌ Нужно 25k!", ephemeral=True)
            if guilds_coll.find_one({"guild_name": {"$regex": f"^{value}$", "$options": "i"}}): 
                return await interaction.response.send_message("❌ Имя занято!", ephemeral=True)
                
            users_coll.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -25000}, "$set": {"guild_id": value}})
            guilds_coll.insert_one({"guild_name": value, "leader_id": interaction.user.id, "co_leaders": [], "bank": 0, "is_private": False, "entry_fee": 0})
            await interaction.response.send_message(f"🏰 Создана гильдия: **{value}**!", ephemeral=True)
            
        elif self.mode == "deposit":
            try: val_int = int(value)
            except Exception: return await interaction.response.send_message("❌ Ошибка формата!", ephemeral=True)
            
            if val_int <= 0 or get_user(interaction.user.id)['coins'] < val_int: 
                return await interaction.response.send_message("❌ Недостаточно средств для этой суммы!", ephemeral=True)
                
            update_coins(interaction.user.id, -val_int)
            guilds_coll.update_one({"guild_name": self.guild_name}, {"$inc": {"bank": val_int}})
            await interaction.response.send_message(f"✅ Внесено {val_int} Колов в казну!", ephemeral=True)
            
        elif self.mode == "fee":
            try: val_int = int(value)
            except Exception: return await interaction.response.send_message("❌ Ошибка формата!", ephemeral=True)
            
            guilds_coll.update_one({"guild_name": self.guild_name}, {"$set": {"entry_fee": max(0, val_int)}})
            await interaction.response.send_message(f"✅ Цена входа установлена: {max(0, val_int)}.", ephemeral=True)

class GuildJoinModal(discord.ui.Modal, title="Вступление в гильдию"):
    guild_name_input = discord.ui.TextInput(label="Название гильдии", max_length=30)
    
    async def on_submit(self, interaction: discord.Interaction):
        target_name = self.guild_name_input.value.strip()
        guild_data = guilds_coll.find_one({"guild_name": {"$regex": f"^{target_name}$", "$options": "i"}})
        if not guild_data:
            return await interaction.response.send_message("❌ Гильдия не найдена!", ephemeral=True)
            
        user_data = get_user(interaction.user.id)
        if user_data.get('guild_id'):
            return await interaction.response.send_message("❌ Вы уже состоите в гильдии!", ephemeral=True)
            
        fee = guild_data.get('entry_fee', 0)
        if user_data['coins'] < fee:
            return await interaction.response.send_message(f"❌ Для входа требуется {fee:,} Колов!", ephemeral=True)
            
        update_coins(interaction.user.id, -fee)
        if fee > 0:
            guilds_coll.update_one({"guild_name": guild_data['guild_name']}, {"$inc": {"bank": fee}})
            
        users_coll.update_one({"_id": interaction.user.id}, {"$set": {"guild_id": guild_data['guild_name']}})
        await interaction.response.send_message(f"✅ Вы успешно присоединились к гильдии **{guild_data['guild_name']}**!", ephemeral=True)

class LeaderPanelView(discord.ui.View):
    def __init__(self, guild_name):
        super().__init__(timeout=300)
        self.guild_name = guild_name
        
    @discord.ui.button(label="Установить налог", style=discord.ButtonStyle.grey, emoji="🪙")
    async def set_fee(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuildModal("fee", self.guild_name))

class GuildMainView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.guild_name = get_user(user_id).get('guild_id')

    @discord.ui.button(label="Создать (25k)", style=discord.ButtonStyle.green, emoji="🏰", row=0)
    async def create_guild(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuildModal("create"))
        
    @discord.ui.button(label="Вступить", style=discord.ButtonStyle.blurple, emoji="🤝", row=0)
    async def join_guild(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuildJoinModal())

    @discord.ui.button(label="Информация", style=discord.ButtonStyle.blurple, emoji="🛡️", row=0)
    async def info_guild(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.guild_name: 
            return await interaction.response.send_message("❌ Вы не состоите в гильдии!", ephemeral=True)
            
        guild_data = guilds_coll.find_one({"guild_name": self.guild_name})
        members = list(users_coll.find({"guild_id": self.guild_name}))
        
        embed = discord.Embed(title=f"🛡️ {self.guild_name}", color=0x9B59B6)
        embed.add_field(name="👑 Лидер", value=f"<@{guild_data['leader_id']}>", inline=False)
        embed.add_field(name="💰 Казна", value=text_blue(f"{guild_data.get('bank', 0):,} Колов"), inline=True)
        embed.add_field(name="🎟️ Вход", value=text_blue(f"{guild_data.get('entry_fee', 0):,} Колов"), inline=True)
        embed.add_field(name=f"👥 Участники ({len(members)})", value=", ".join([f"<@{x['_id']}>" for x in members]), inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Пополнить", style=discord.ButtonStyle.grey, emoji="💰", row=1)
    async def deposit_guild(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.guild_name: 
            return await interaction.response.send_message("❌ Нет гильдии!", ephemeral=True)
        await interaction.response.send_modal(GuildModal("deposit", self.guild_name))
        
    @discord.ui.button(label="Панель лидера", style=discord.ButtonStyle.grey, emoji="⚙️", row=1)
    async def leader_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.guild_name:
            return await interaction.response.send_message("❌ Вы не состоите в гильдии!", ephemeral=True)
            
        guild_data = guilds_coll.find_one({"guild_name": self.guild_name})
        if interaction.user.id != guild_data['leader_id'] and interaction.user.id not in guild_data.get('co_leaders', []):
            return await interaction.response.send_message("❌ Ошибка доступа: Вы не являетесь лидером!", ephemeral=True)
            
        await interaction.response.send_message("⚙️ Панель управления гильдией открыта.", view=LeaderPanelView(self.guild_name), ephemeral=True)

    @discord.ui.button(label="Покинуть", style=discord.ButtonStyle.red, emoji="🚪", row=1)
    async def leave_guild(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.guild_name: 
            return await interaction.response.send_message("❌ Нет гильдии!", ephemeral=True)
            
        guild_data = guilds_coll.find_one({"guild_name": self.guild_name})
        users_coll.update_one({"_id": interaction.user.id}, {"$set": {"guild_id": None}})
        
        if interaction.user.id in guild_data.get("co_leaders", []): 
            guilds_coll.update_one({"guild_name": self.guild_name}, {"$pull": {"co_leaders": interaction.user.id}})
            
        if guild_data['leader_id'] == interaction.user.id:
            new_leader = users_coll.find_one({"guild_id": self.guild_name})
            if new_leader: 
                guilds_coll.update_one({"guild_name": self.guild_name}, {"$set": {"leader_id": new_leader["_id"]}})
            else: 
                guilds_coll.delete_one({"guild_name": self.guild_name})
                
        await interaction.response.send_message("🚪 Вы покинули гильдию.", ephemeral=True)

@bot.tree.command(name="guild", description="Меню гильдий")
@check_maintenance()
async def guild_menu(interaction: discord.Interaction):
    embed = discord.Embed(title="▬▬ ┌ 🏰 ГИЛЬДИИ АЙНКРАДА ┐ ▬▬", description="Используйте кнопки ниже для взаимодействия:", color=0x2B2D31)
    # Generic SAO Theme Banner to match the screenshot layout
    embed.set_image(url="https://media0.giphy.com/media/v1.Y2lkPTc5MGI3NjExcDdwNWNmdnRyOGJlNW1kYmYzNm12N3Vyc3diaGlzZm92ajZnZ2F1YiZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/XG444KXEaA3zW/giphy.gif")
    await interaction.response.send_message(embed=embed, view=GuildMainView(interaction.user.id))

# ==========================================
# 8. АУКЦИОН РОЛЕЙ
# ==========================================
class AuctionPagingView(discord.ui.View):
    def __init__(self, items):
        super().__init__(timeout=300)
        self.items = items
        self.page = 0
        self.per_page = 5
        self.update_view()

    def update_view(self):
        self.clear_items()
        max_pages = max(0, (len(self.items) - 1) // self.per_page)
        
        self.add_item(discord.ui.Button(label="Назад", style=discord.ButtonStyle.grey, disabled=self.page == 0, custom_id="prev"))
        self.add_item(discord.ui.Button(label="Вперед", style=discord.ButtonStyle.grey, disabled=self.page >= max_pages, custom_id="next"))
        
        slice_items = self.items[self.page * self.per_page : (self.page + 1) * self.per_page]
        if slice_items:
            select = discord.ui.Select(options=[discord.SelectOption(label=item['role_name'], description=f"{item['price']:,}", value=str(item['sale_id'])) for item in slice_items])
            select.callback = self.buy_item
            self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.data.get('custom_id') == "prev": 
            self.page -= 1
        elif interaction.data.get('custom_id') == "next": 
            self.page += 1
        else: 
            return True
            
        self.update_view()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)
        return False

    async def buy_item(self, interaction: discord.Interaction):
        sale_id = int(interaction.data['values'][0])
        item = auction_coll.find_one({"sale_id": sale_id})
        
        if not item or interaction.user.id == item['seller_id'] or get_user(interaction.user.id)['coins'] < item['price']: 
            return await interaction.response.send_message("❌ Ошибка покупки!", ephemeral=True)
            
        update_coins(interaction.user.id, -item['price'])
        update_coins(item['seller_id'], item['price'])
        
        custom_roles_coll.update_one({"role_id": item['role_id']}, {"$set": {"user_id": interaction.user.id}})
        auction_coll.delete_one({"sale_id": sale_id})
        
        role = interaction.guild.get_role(item['role_id'])
        if role:
            try: await interaction.user.add_roles(role)
            except Exception: pass
            
        await interaction.response.send_message(f"🎉 Куплено: **{item['role_name']}**!", ephemeral=True)

    def get_embed(self):
        embed = discord.Embed(title="─────────────── ┌ 🏛️ АУКЦИОН ┐ ───────────────", color=0xFFD700)
        for item in self.items[self.page * self.per_page : (self.page + 1) * self.per_page]:
            embed.add_field(name=f"📦 {item['role_name']}", value=f"Цена: {text_blue(f'{item['price']:,}')} Продавец: <@{item['seller_id']}>", inline=False)
        return embed

@bot.tree.command(name="auction", description="Аукцион")
@check_maintenance()
async def auction(interaction: discord.Interaction):
    items = list(auction_coll.find())
    if not items: 
        return await interaction.response.send_message("📦 Аукцион пуст.", ephemeral=True)
    view = AuctionPagingView(items)
    await interaction.response.send_message(embed=view.get_embed(), view=view, ephemeral=True)

# ==========================================
# 9. АДМИН-КОМАНДЫ (СИСТЕМНЫЕ)
# ==========================================
@bot.tree.command(name="maintenance", description="[АДМИН] Техобслуживание")
async def maint(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    await interaction.response.send_message(f"🛠️ Статус: {'ВКЛЮЧЕН' if MAINTENANCE_MODE else 'ВЫКЛЮЧЕН'}", ephemeral=True)

@bot.tree.command(name="setlevel", description="[АДМИН] Установить этаж")
async def setlevel(interaction: discord.Interaction, member: discord.Member, level: int):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_user(member.id)
    users_coll.update_one({"_id": member.id}, {"$set": {"level": level, "xp": 0}})
    await check_level_roles(member, level)
    await interaction.response.send_message(f"✅ Установлен {level} этаж для {member.mention}.", ephemeral=True)

@bot.tree.command(name="setxp", description="[АДМИН] Установить опыт")
async def setxp(interaction: discord.Interaction, member: discord.Member, xp: int):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_user(member.id)
    users_coll.update_one({"_id": member.id}, {"$set": {"xp": xp}})
    await interaction.response.send_message(f"✅ Установлено {xp} XP для {member.mention}.", ephemeral=True)

@bot.tree.command(name="givexp", description="[АДМИН] Выдать опыт")
async def givexp(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    await add_xp(interaction, member.id, amount)
    await interaction.response.send_message(f"✅ Выдано {amount} XP.", ephemeral=True)

@bot.tree.command(name="setcoins", description="[АДМИН] Установить баланс")
async def setcoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_user(member.id)
    users_coll.update_one({"_id": member.id}, {"$set": {"coins": amount}})
    await interaction.response.send_message(f"✅ Баланс {member.mention} = {amount}.", ephemeral=True)

@bot.tree.command(name="givecoins", description="[АДМИН] Выдать Колы")
async def givecoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_user(member.id)
    update_coins(member.id, amount)
    await interaction.response.send_message(f"✅ Выдано {amount}.", ephemeral=True)

@bot.tree.command(name="takecoins", description="[АДМИН] Забрать Колы")
async def takecoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_user(member.id)
    update_coins(member.id, -amount)
    await interaction.response.send_message(f"🔻 Списано {amount}.", ephemeral=True)

@bot.tree.command(name="setstreak", description="[АДМИН] Установить стрик")
async def setstreak(interaction: discord.Interaction, member: discord.Member, days: int):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_user(member.id)
    users_coll.update_one({"_id": member.id}, {"$set": {"streak": days}})
    await interaction.response.send_message(f"✅ Стрик установлен в {days} дн.", ephemeral=True)

@bot.tree.command(name="resetcd", description="[АДМИН] Сброс КД")
async def resetcd(interaction: discord.Interaction, member: discord.Member = None):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    target = member or interaction.user
    users_coll.update_one({"_id": target.id}, {"$set": {"last_daily": 0.0, "last_work": 0.0, "last_crime": 0.0, "last_rob": 0.0}})
    await interaction.response.send_message(f"⚡ Кулдауны успешно сброшены!", ephemeral=True)

@bot.tree.command(name="resetuser", description="[АДМИН] Полный сброс профиля")
async def resetuser(interaction: discord.Interaction, member: discord.Member):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    users_coll.delete_one({"_id": member.id})
    get_user(member.id)
    await interaction.response.send_message(f"☢️ Профиль {member.mention} сброшен!", ephemeral=True)

@bot.tree.command(name="resetdb", description="[АДМИН] Полный сброс БД")
async def resetdb(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    for collection in [users_coll, guilds_coll, custom_roles_coll, titles_coll, auction_coll, guild_reqs_coll]: 
        collection.delete_many({})
    await interaction.response.send_message("☢️ БД полностью очищена!", ephemeral=True)

# ==========================================
# 10. НОВОСТИ, СТРИМЫ, ИДЕИ, ВЕРИФИКАЦИЯ
# ==========================================
class NewsModal(discord.ui.Modal, title="Новость"):
    news_title = discord.ui.TextInput(label="Заголовок")
    news_text = discord.ui.TextInput(label="Текст", style=discord.TextStyle.paragraph)
    news_gif = discord.ui.TextInput(label="GIF", required=False)
    
    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title=f"📢 {self.news_title.value}", description=self.news_text.value, color=0x00BFFF)
        if self.news_gif.value: 
            embed.set_image(url=self.news_gif.value)
        await interaction.channel.send(content="@everyone", embed=embed)
        await interaction.response.send_message("✅ Готово!", ephemeral=True)

@bot.tree.command(name="setup_news", description="[АДМИН] Новость")
async def setup_news(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    await interaction.response.send_modal(NewsModal())

@bot.tree.command(name="stream", description="[АДМИН] Стрим")
async def stream(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    embed = discord.Embed(title="🔴 ПРЯМОЙ ЭФИР", description="**Залетайте на стрим!**\n🔗 [КЛИКАЙ СЮДА](https://www.twitch.tv/treihaolvl31)", color=0x9146FF)
    embed.set_image(url="https://media.tenor.com/HsNUWd_R6RYAAAAC/sword-art-online-sao.gif")
    await interaction.channel.send(content="@everyone", embed=embed)
    await interaction.response.send_message("✅ Анонс отправлен!", ephemeral=True)

class IdeaModal(discord.ui.Modal, title="Идея"):
    idea_text = discord.ui.TextInput(label="Текст", style=discord.TextStyle.paragraph)
    
    async def on_submit(self, interaction: discord.Interaction):
        admin_channel = interaction.guild.get_channel(ADMIN_IDEA_CHANNEL_ID)
        embed = discord.Embed(title="⏳ НОВАЯ ИДЕЯ", description=f"**От:** {interaction.user.mention}\n\n{self.idea_text.value}", color=0xF1C40F)
        
        class AdminIdeaView(discord.ui.View):
            @discord.ui.button(label="Одобрить", style=discord.ButtonStyle.green, emoji="✅")
            async def approve(self, admin_inter: discord.Interaction, button: discord.ui.Button):
                public_channel = admin_inter.guild.get_channel(PUBLIC_IDEA_CHANNEL_ID)
                public_embed = discord.Embed(title="✅ ИДЕЯ ОДОБРЕНА И ОПУБЛИКОВАНА", color=0x2ECC71)
                public_embed.add_field(name="От пользователя:", value=interaction.user.mention, inline=False)
                public_embed.add_field(name="Текст:", value=self.idea_text.value, inline=False)
                public_embed.add_field(name="Проверил модератор:", value=admin_inter.user.mention, inline=False)
                msg = await public_channel.send(embed=public_embed)
                await msg.add_reaction("👍")
                await msg.add_reaction("👎")
                
                for child in self.children: child.disabled = True
                await admin_inter.response.edit_message(content="✅ Одобрено", view=self)

            @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌")
            async def reject(self, admin_inter: discord.Interaction, button: discord.ui.Button):
                reject_embed = discord.Embed(title="❌ ИДЕЯ ОТКЛОНЕНА", color=0xE74C3C)
                reject_embed.add_field(name="От пользователя:", value=interaction.user.mention, inline=False)
                reject_embed.add_field(name="Текст:", value=self.idea_text.value, inline=False)
                reject_embed.add_field(name="Отклонил модератор:", value=admin_inter.user.mention, inline=False)
                
                for child in self.children: child.disabled = True
                await admin_inter.response.edit_message(embed=reject_embed, view=self)
                
        await admin_channel.send(embed=embed, view=AdminIdeaView())
        await interaction.response.send_message("✅ Отправлено!", ephemeral=True)

@bot.tree.command(name="idea", description="Предложить идею")
@check_maintenance()
async def idea(interaction: discord.Interaction): 
    await interaction.response.send_modal(IdeaModal())

@bot.tree.command(name="setup_verify", description="[АДМИН] Верификация инфо")
async def setup_verify(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    embed = discord.Embed(title="🛡️ ИДЕНТИФИКАЦИЯ", description="Зайдите в войс Ожидание верификации...", color=0x3498DB)
    embed.set_image(url="https://media.tenor.com/zLQ4_cEQY0AAAAAC/sao.gif")
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Установлено.", ephemeral=True)

@bot.tree.command(name="verify", description="[САППОРТ] Проверить игрока")
@app_commands.choices(gender=[app_commands.Choice(name="Мужчина", value="♂️"), app_commands.Choice(name="Женщина", value="♀️")])
async def verify_user(interaction: discord.Interaction, member: discord.Member, gender: str):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
        
    role = discord.utils.get(interaction.guild.roles, name=gender)
    if not role: 
        return await interaction.response.send_message("❌ Роль не найдена.", ephemeral=True)
    
    try:
        await member.add_roles(role)
        unverify_role = discord.utils.get(interaction.guild.roles, name="unverify")
        if unverify_role in member.roles: 
            await member.remove_roles(unverify_role)
        
        await interaction.response.send_message(embed=discord.Embed(title="✅ ВЕРИФИКАЦИЯ УСПЕШНА", description=f"Статус {gender} выдан.", color=0x2ECC71), ephemeral=True)
        
        docs_channel = interaction.guild.get_channel(DOCS_CHANNEL_ID)
        if docs_channel:
            docs_embed = discord.Embed(title="────── ┌ 📁 DOCC: ИДЕНТИФИКАЦИЯ ┐ ──────", color=0x2B2D31)
            docs_embed.add_field(name="Пользователь", value=f"{member.mention}\n`{member.id}`", inline=True)
            docs_embed.add_field(name="Выданный статус", value=gender, inline=True)
            docs_embed.add_field(name="Саппорт / Модер", value=interaction.user.mention, inline=False)
            docs_embed.add_field(name="Аккаунт создан", value=f"<t:{int(member.created_at.timestamp())}:F>", inline=False)
            docs_embed.add_field(name="Discord Sensor", value=f"🔗 [Открыть профиль](https://discord.com/users/{member.id})", inline=False)
            docs_embed.set_thumbnail(url=member.display_avatar.url)
            await docs_channel.send(embed=docs_embed)
    except Exception: 
        await interaction.response.send_message("❌ Ошибка при выдаче роли.", ephemeral=True)

@bot.tree.command(name="setup_ranks", description="[АДМИН] Ранги инфо")
async def setup_ranks(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    embed = discord.Embed(title="⚡ ЭТАЖИ И НАГРАДЫ", description="Информация об иерархии башни...", color=0xE02653)
    embed.set_image(url="https://media.tenor.com/zLQ4_cEQY0AAAAAC/sao.gif")
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Готово.", ephemeral=True)

# Запуск
if __name__ == "__main__":
    keep_alive()
    bot.run(os.getenv("TOKEN"))
