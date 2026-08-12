import os
import io
import certifi
import random
import time
import asyncio
import aiohttp
import discord
from pymongo import MongoClient
from discord import app_commands
from discord.ext import commands
from keep_alive import keep_alive

# Достаем ссылку на базу из переменных окружения
MONGO_URI = os.getenv('MONGO_URI')

# Подключаемся с поддержкой сертификатов certifi
cluster = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = cluster.aincrad_data
users_collection = db.users
custom_roles_collection = db.custom_roles
auction_collection = db.auction_roles
titles_collection = db.user_titles
guilds_collection = db.guilds
guild_requests_collection = db.guilds_collection if hasattr(db, 'guild_requests') else db.guild_requests

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True 

# Оптимизация памяти для хостинга
bot = commands.Bot(
    command_prefix="!", 
    intents=intents,
    chunk_guilds_at_startup=False,
    max_messages=10
)

# Глобальный флаг режима техобслуживания
MAINTENANCE_MODE = False

# --- НАСТРОЙКИ КАНАЛОВ КОНТЕНТА И АВТОМОДА ---
WELCOME_CHANNEL_ID = 1529471897647976648
MEDIA_CHANNELS = [1534785295696789634, 1534785127572443246, 1534550233315414169] # Каналы с мемами/артами
VIDEO_CHANNEL_ID = 1529472211730043012 # ID канала #ролики
MEDIA_LOG_CHANNEL_ID = 1534789085582065794 # Канал #проверка-медиа для премодерации
STREAM_CHANNEL_ID = 1534785474739179530
AUTO_MOD_LOG_CHANNEL_ID = 1529472394102706336

# --- СЛОВАРИ ДЛЯ ANTI-AFK И АВТОМОДЕРАЦИИ ---
voice_start_times = {} 
voice_accumulated = {} 
user_last_message_time = {}
user_message_timestamps = {}
user_message_history = {}
log_cooldowns = {} 

def get_or_create_user(user_id):
    user = users_collection.find_one({"_id": user_id})
    if user is None:
        new_user = {
            "_id": user_id,
            "coins": 100,
            "xp": 0,
            "level": 1,
            "last_daily": 0.0,
            "last_work": 0.0,
            "last_crime": 0.0,
            "last_rob": 0.0,
            "streak": 0,
            "guild_id": None,
            "special_title": "Отсутствует",
            "voice_time": 0.0 
        }
        users_collection.insert_one(new_user)
        return 100, 0, 1, 0.0, 0.0, 0.0, 0.0, 0, None, 'Отсутствует', 0.0
    
    return (
        user.get("coins", 100),
        user.get("xp", 0),
        user.get("level", 1),
        user.get("last_daily", 0.0),
        user.get("last_work", 0.0),
        user.get("last_crime", 0.0),
        user.get("last_rob", 0.0),
        user.get("streak", 0),
        user.get("guild_id", None),
        user.get("special_title", "Отсутствует"),
        user.get("voice_time", 0.0)
    )

def is_admin_or_mod(member: discord.Member):
    if member.guild_permissions.administrator:
        return True
    role_names = [r.name.lower() for r in member.roles]
    allowed_roles = ["модератор", "moderator", "администратор", "administrator", "саппорт", "support", "founder", "co-founder", "content maker", "sigmo brazzers"]
    if any(role in role_names for role in allowed_roles):
        return True
    return False

# ЖЕСТКИЙ ДЕКОРАТОР ПРОВЕРКИ ТЕХОБСЛУЖИВАНИЯ
def check_maintenance():
    async def predicate(interaction: discord.Interaction) -> bool:
        global MAINTENANCE_MODE
        if MAINTENANCE_MODE and not is_admin_or_mod(interaction.user):
            await interaction.response.send_message(
                "🛠️ **[ SYSTEM ALERT: КАРДИНАЛ АКТИВЕН ]**\n"
                "На сервере проводятся технические работы. Доступ к системным интерфейсам временно заблокирован.", 
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)

async def check_level_roles(member: discord.Member, current_level: int):
    highest_role_name = None
    for req_level, data in sorted(ROLES_MAPPING.items(), reverse=True):
        if current_level >= req_level:
            highest_role_name = data["name"]
            break
            
    if not highest_role_name:
        return

    highest_role = discord.utils.get(member.guild.roles, name=highest_role_name)
    roles_to_remove = []
    for req_level, data in ROLES_MAPPING.items():
        r_name = data["name"]
        if r_name != highest_role_name:
            old_role = discord.utils.get(member.guild.roles, name=r_name)
            if old_role and old_role in member.roles:
                roles_to_remove.append(old_role)
                
    if roles_to_remove:
        try:
            await member.remove_roles(*roles_to_remove)
        except Exception:
            pass

    if highest_role and highest_role not in member.roles:
        try:
            await member.add_roles(highest_role)
            await member.send(f"🎉 Поздравляем! Вы прорвались на **{current_level} этаж** Айнкрада и получили элитный статус **{highest_role_name}**!")
        except Exception:
            pass

ROLES_MAPPING = {
    2: {"name": "Начало Легенды (LVL 2)", "min_daily": 60, "max_daily": 80},
    5: {"name": "Путешественник (LVL 5)", "min_daily": 70, "max_daily": 100},
    10: {"name": "Разведчик Рубежа (LVL 10)", "min_daily": 90, "max_daily": 130},
    15: {"name": "Опытный Мечник (LVL 15)", "min_daily": 110, "max_daily": 150},
    20: {"name": "Передовой Воин (LVL 20)", "min_daily": 140, "max_daily": 180},
    30: {"name": "Закаленный Огнем (LVL 30)", "min_daily": 170, "max_daily": 220},
    40: {"name": "Мастер клинка (LVL 40)", "min_daily": 210, "max_daily": 270},
    50: {"name": "Герой Айнкрада (LVL 50)", "min_daily": 260, "max_daily": 340},
    65: {"name": "Грандмастер (LVL 65)", "min_daily": 350, "max_daily": 450},
    80: {"name": "Вершитель Судеб (LVL 80)", "min_daily": 480, "max_daily": 650},
    100: {"name": "Beater (LVL 100)", "min_daily": 800, "max_daily": 1000},
}

async def add_xp(interaction_or_member, user_id, amount):
    coins, xp, level, _, _, _, _, _, _, _, _ = get_or_create_user(user_id)
    xp += amount
    next_level_xp = int(35 * (level ** 1.85) + 80 * level + 40)
    leveled_up = False
    
    while xp >= next_level_xp:
        level += 1
        xp -= next_level_xp
        next_level_xp = int(35 * (level ** 1.85) + 80 * level + 40)
        leveled_up = True

    users_collection.update_one({"_id": user_id}, {"$set": {"xp": xp, "level": level}})

    if leveled_up:
        if isinstance(interaction_or_member, discord.Member):
            member = interaction_or_member
            channel = None
        else:
            member = getattr(interaction_or_member, 'user', getattr(interaction_or_member, 'author', None))
            channel = getattr(interaction_or_member, 'channel', None)
        
        if member:
            await check_level_roles(member, level)
            if channel:
                lvl_embed = discord.Embed(
                    title="⚡ СИСТЕМНОЕ УВЕДОМЛЕНИЕ: ПОВЫШЕНИЕ ЭТАЖА", 
                    description=f"Поздравляем! Игрок успешно прорвался на **{level} этаж** башни Айнкрад!", 
                    color=0x00BFFF
                )
                lvl_embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
                try:
                    await channel.send(
                        content=f"Внимание, Система: {member.mention} устанавливает новые рекорды!", 
                        embed=lvl_embed, 
                        delete_after=10.0
                    )
                except Exception:
                    pass

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Бот {bot.user} запущен и полностью готов к работе в Айнкраде!")

# --- КНОПКИ ПРЕМОДЕРАЦИИ МЕДИА И РОЛИКОВ ---
class MediaModerationView(discord.ui.View):
    def __init__(self, author_id: int, channel_id: int, content_text: str, files_data: list):
        super().__init__(timeout=None)
        self.author_id = author_id
        self.channel_id = channel_id
        self.content_text = content_text
        self.files_data = files_data  # Список сохраненных байтов файлов

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_or_mod(interaction.user):
            return await interaction.response.send_message("❌ У вас нет прав для модерации контента!", ephemeral=True)

        target_channel = interaction.guild.get_channel(self.channel_id)
        if not target_channel:
            return await interaction.response.send_message("❌ Целевой канал не найден!", ephemeral=True)

        try:
            webhooks = await target_channel.webhooks()
            webhook = discord.utils.get(webhooks, name="Yui Media")
            if not webhook:
                webhook = await target_channel.create_webhook(name="Yui Media")

            # Восстанавливаем файлы из сохраненных байтов без потери ссылок
            prepared_files = [discord.File(io.BytesIO(f["bytes"]), filename=f["filename"]) for f in self.files_data]

            post_content = f"**Отправил:** <@{self.author_id}>"
            if self.content_text:
                post_content += f"\n\n{self.content_text}"

            sent_message = await webhook.send(
                content=post_content,
                files=prepared_files if prepared_files else [],
                username="Yui",
                avatar_url=bot.user.display_avatar.url,
                wait=True
            )
            try:
                await sent_message.add_reaction("❤️")
            except:
                pass

            embed = interaction.message.embeds[0]
            embed.color = 0x2ECC71
            embed.title = "✅ КОНТЕНТ ОДОБРЕН И ОПУБЛИКОВАН"
            embed.add_field(name="Одобрил", value=interaction.user.mention, inline=False)
            embed.add_field(name="Ссылка на пост", value=f"[Перейти к посту]({sent_message.jump_url})", inline=False)

            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(embed=embed, view=self)

            await add_xp(interaction, self.author_id, random.randint(2, 5))

        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка публикации: {e}", ephemeral=True)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_or_mod(interaction.user):
            return await interaction.response.send_message("❌ У вас нет прав для модерации контента!", ephemeral=True)

        embed = interaction.message.embeds[0]
        embed.color = 0xE74C3C
        embed.title = "❌ КОНТЕНТ ОТКЛОНЕН"
        embed.add_field(name="Отклонил", value=interaction.user.mention, inline=False)

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
        
    global MAINTENANCE_MODE
    if MAINTENANCE_MODE and not is_admin_or_mod(message.author):
        return

    is_privileged = False
    if message.author.guild_permissions.administrator:
        is_privileged = True
    else:
        role_names = [r.name.lower() for r in message.author.roles]
        allowed_roles = [
            "founder", "co-founder", "moderator", "модератор", 
            "support", "саппорт", "content maker", "sigmo brazzers"
        ]
        if any(role in role_names for role in allowed_roles):
            is_privileged = True

    # --- УМНАЯ АВТОМОДЕРАЦИЯ И АНТИ-СПАМ ---
    violation_reason = None
    
    if not is_privileged:
        content_lower = message.content.lower().strip()
        
        safe_domains = [
            "tenor.com", "giphy.com", "imgur.com", "discordapp.com", 
            "discord.com", "pinimg.com", "klipy.com", "alphacoders.com"
        ]
        is_gif_or_media_link = any(domain in content_lower for domain in safe_domains) or ".gif" in content_lower
        
        stream_platforms = [
            "twitch.tv", "youtube.com/live", "youtube.com/@", "kick.com", 
            "trovo.live", "vkplay.live", "youtube.com/watch", "youtu.be", 
            "tiktok.com", "vk.com/video"
        ]
        has_stream_link = any(plat in content_lower for plat in stream_platforms)

        has_invite = "discord.gg/" in content_lower or "discord.com/invite" in content_lower or "invite.gg/" in content_lower
        is_raw_url = "http://" in content_lower or "https://" in content_lower or "www." in content_lower or ".com" in content_lower or ".ru" in content_lower or ".net" in content_lower
        has_link = is_raw_url and not is_gif_or_media_link and not has_stream_link
        
        has_mass_ping = "@everyone" in message.content or "@here" in message.content
        total_attachments = len(message.attachments)
        is_mass_image_spam = total_attachments >= 3 and not is_gif_or_media_link
        
        is_media_channel = message.channel.id in MEDIA_CHANNELS
        is_stream_channel = message.channel.id == STREAM_CHANNEL_ID
        is_video_channel = message.channel.id == VIDEO_CHANNEL_ID

        user_id = message.author.id
        current_time = time.time()
        is_fast_flood = False

        if user_id in user_last_message_time:
            time_diff = current_time - user_last_message_time[user_id]
            if time_diff < 1.5:
                is_fast_flood = True

        user_last_message_time[user_id] = current_time

        letters = [c for c in message.content if c.isalpha()]
        is_caps_spam = False
        if len(letters) > 8:
            caps_count = sum(1 for c in letters if c.isupper())
            if (caps_count / len(letters)) > 0.7:
                is_caps_spam = True

        if has_invite:
            violation_reason = "Попытка публикации стороннего инвайта"
        elif has_link:
            violation_reason = f"Публикация неразрешенной ссылки (`{message.content}`)"
        elif has_mass_ping:
            violation_reason = "Массовый пинг (`@everyone` / `@here`)"
        elif is_mass_image_spam and not is_media_channel:
            violation_reason = f"Массовый спам картинками/скринами ({total_attachments} шт. в одном сообщении)"
        elif is_fast_flood:
            violation_reason = "Слишком быстрый флуд (превышен лимит КД 1.5 сек)"
        elif is_caps_spam:
            violation_reason = "Чрезмерное использование капса (Caps Lock Spam)"
            
        # Запреты по каналам:
        elif is_media_channel and total_attachments == 0 and not is_gif_or_media_link:
            violation_reason = "В медиа-зоны разрешено отправлять только картинки, видео или гифки!"
        elif is_video_channel and not has_stream_link and total_attachments == 0 and not is_gif_or_media_link:
            violation_reason = "В канал #ролики можно публиковать только ссылки на ролики/видео или медиафайлы!"
        elif is_stream_channel and not has_stream_link:
            violation_reason = "В канал #стримы можно публиковать только ссылки на трансляции (Twitch, Kick, YouTube)!"

        if violation_reason:
            try:
                await message.delete()
            except Exception as e:
                print(f"[ОШИБКА АВТОМОДА] Не удалось удалить сообщение: {e}")

            should_send_log = True
            if user_id in log_cooldowns:
                if current_time - log_cooldowns[user_id] < 15.0:
                    should_send_log = False
            
            if should_send_log:
                log_cooldowns[user_id] = current_time
                log_channel = message.guild.get_channel(AUTO_MOD_LOG_CHANNEL_ID)
                if log_channel:
                    created_at = discord.utils.format_dt(message.author.created_at, style='R')
                    log_embed = discord.Embed(
                        title="⚠️ КАРДИНАЛ: НОВЫЙ ОТЧЕТ АВТОМОДА",
                        color=0xE74C3C
                    )
                    log_embed.set_thumbnail(url=message.author.display_avatar.url)
                    log_embed.add_field(name="👤 Пользователь", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
                    log_embed.add_field(name="📅 Аккаунт создан", value=created_at, inline=True)
                    log_embed.add_field(name="📂 Канал", value=message.channel.mention, inline=True)
                    log_embed.add_field(name="⚡ Причина", value=f"```yaml\n{violation_reason}\n```", inline=False)
                    log_embed.add_field(name="💬 Нарушение", value=f"```text\n{message.content if message.content else '[Медиа / Вложение]'}\n```", inline=False)
                    log_embed.set_footer(text="Aincrad Security Shield • Сообщение удалено")
                    try:
                        await log_channel.send(embed=log_embed)
                    except:
                        pass
            return

    # --- ПРЕМОДЕРАЦИЯ МЕДИАКОНТЕНТА И РОЛИКОВ ---
    is_media_zone = message.channel.id in MEDIA_CHANNELS or message.channel.id == VIDEO_CHANNEL_ID
    
    # Если сообщение отправлено в медиа-канал и это не служебное сообщение/нарушение
    if is_media_zone and not violation_reason:
        # Проверяем, есть ли контент (файлы, картинки, встроенные эмбеды, ссылки, гифки)
        has_content = len(message.attachments) > 0 or len(message.embeds) > 0 or "http://" in message.content.lower() or "https://" in message.content.lower() or ".gif" in message.content.lower()
        
        if has_content:
            try:
                mod_channel = bot.get_channel(MEDIA_LOG_CHANNEL_ID)
                if mod_channel:
                    files_data = []
                    for att in message.attachments:
                        file_bytes = await att.read()
                        files_data.append({"bytes": file_bytes, "filename": att.filename})

                    mod_embed = discord.Embed(
                        title="🔍 ПРЕМОДЕРАЦИЯ КОНТЕНТА",
                        description=f"**Автор:** {message.author.mention} (`{message.author.id}`)\n**Канал:** {message.channel.mention}",
                        color=0xF1C40F
                    )
                    if message.content:
                        mod_embed.add_field(name="Текст / Ссылка", value=message.content, inline=False)
                    
                    if message.attachments:
                        mod_embed.set_image(url=message.attachments[0].url)
                    elif message.embeds:
                        if message.embeds[0].thumbnail:
                            mod_embed.set_image(url=message.embeds[0].thumbnail.url)
                        elif message.embeds[0].image:
                            mod_embed.set_image(url=message.embeds[0].image.url)
                    
                    mod_embed.set_footer(text="Aincrad Media Verification System")

                    view = MediaModerationView(
                        author_id=message.author.id,
                        channel_id=message.channel.id,
                        content_text=message.content,
                        files_data=files_data
                    )
                    await mod_channel.send(embed=mod_embed, view=view)

                await message.delete()
            except Exception as e:
                print(f"[ПРЕМОДЕРАЦИЯ ОШИБКА] {e}")
            return

    # Стандартное начисление опыта для обычных сообщений
    await add_xp(message, message.author.id, random.randint(2, 5))
    await bot.process_commands(message)


# --- СИСТЕМА ВОЙС-АКТИВНОСТИ (ANTI-AFK) ---
def is_afk(voice_state):
    return voice_state.self_mute or voice_state.mute or voice_state.self_deaf or voice_state.deaf or voice_state.afk

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot: return
    
    was_afk = before.channel is None or is_afk(before)
    is_afk_now = after.channel is None or is_afk(after)
    
    if was_afk and not is_afk_now:
        voice_start_times[member.id] = time.time()
        if member.id not in voice_accumulated:
            voice_accumulated[member.id] = 0.0

    elif not was_afk and is_afk_now:
        if member.id in voice_start_times:
            session_time = time.time() - voice_start_times.pop(member.id)
            voice_accumulated[member.id] = voice_accumulated.get(member.id, 0.0) + session_time

    if before.channel is not None and after.channel is None:
        if member.id in voice_start_times:
            session_time = time.time() - voice_start_times.pop(member.id)
            voice_accumulated[member.id] = voice_accumulated.get(member.id, 0.0) + session_time
        
        total_time = voice_accumulated.pop(member.id, 0.0)
        duration_minutes = int(total_time // 60)
        
        if duration_minutes < 1:
            return 
            
        earned_coins = duration_minutes
        earned_xp = duration_minutes
        
        users_collection.update_one(
            {"_id": member.id}, 
            {
                "$inc": {
                    "coins": earned_coins, 
                    "voice_time": int(total_time)
                }
            },
            upsert=True
        )
        await add_xp(member, member.id, earned_xp)
        
        try:
            embed = discord.Embed(
                description=(
                    "```ansi\n"
                    "\u001b[36m─────────────── ┌ 🎙️ ВОЙС-АКТИВНОСТЬ ┐ ───────────────\u001b[0m\n"
                    "```\n"
                    "Сеанс связи в голосовом канале успешно завершен!\n"
                    "```ansi\n"
                    "\u001b[36m────────────────────────────────────────────────────────\u001b[0m\n"
                    "```"
                ),
                color=0x00BFFF
            )
            embed.add_field(
                name="🪙 Колы", 
                value=f"```fix\n+{earned_coins:,}\n```", 
                inline=True
            )
            embed.add_field(
                name="⚡ Опыт", 
                value=f"```yaml\n+{earned_xp:,} XP\n```", 
                inline=True
            )
            embed.add_field(
                name="⏱️ Время", 
                value=f"```yaml\n{duration_minutes} мин.\n```", 
                inline=True
            )
            embed.add_field(
                name="\u200b", 
                value="*(Время с выключенным микрофоном или звуком не учитывалось)*", 
                inline=False
            )
            embed.set_footer(text="Cardinal Anti-AFK System • Айнкрад")
            
            await member.send(embed=embed)
        except Exception:
            pass

@bot.event
async def on_member_join(member: discord.Member):
    # 1. Выдача стартовой роли
    role = discord.utils.get(member.guild.roles, name="unverify") 
    if role:
        try:
            await member.add_roles(role)
        except Exception:
            pass

    # 2. Красивое приветствие в канал
    welcome_channel = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if welcome_channel:
        embed = discord.Embed(
            title="⚔️ ДОБРО ПОЖАЛОВАТЬ В АЙНКРАД ⚔️",
            description=f"Приветствуем тебя, {member.mention}!\nТы стал **{member.guild.member_count}-м** игроком, вошедшим в эту башню.\n\nДля начала игры пройди верификацию в голосовом канале.",
            color=0x00BFFF
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        # Гифка встроена в сам эмбед как большой баннер (бэгги стайл)
        embed.set_image(url="https://media.tenor.com/6U-LqU4s2oEAAAAC/sword-art-online-kirito.gif")
        embed.set_footer(text="Aincrad Cardinal System")
        
        try:
            await welcome_channel.send(content=member.mention, embed=embed)
        except Exception as e:
            print(f"[ОШИБКА ПРИВЕТСТВИЯ] Не удалось отправить сообщение: {e}")

# --- АДМИНСКАЯ КОМАНДА ТЕХОБСЛУЖИВАНИЯ ---
@bot.tree.command(name="maintenance", description="[АДМИН] Включить/выключить глобальный режим техобслуживания")
async def maintenance(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user):
        return await interaction.response.send_message("❌ У вас нет прав для управления системным режимом Кардинала!", ephemeral=True)
        
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    status = "🔴 ВКЛЮЧЕН (доступ к боту заблокирован для всех игроков)" if MAINTENANCE_MODE else "🟢 ВЫКЛЮЧЕН (система работает в штатном режиме)"
    embed = discord.Embed(title="🛠️ УПРАВЛЕНИЕ СИСТЕМОЙ КАРДИНАЛ", description=f"Статус техобслуживания изменен:\n**{status}**", color=0xE74C3C if MAINTENANCE_MODE else 0x2ECC71)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- БАЗОВЫЕ КОМАНДЫ ---

@bot.tree.command(name="balance", description="Посмотреть текущий баланс Колов")
@check_maintenance()
async def balance(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(target.id)
    embed = discord.Embed(title="🌐 БАНКОВСКИЙ СЧЕТ АЙНКРАДА", color=0x00BFFF)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="💳 Владелец счета", value=f"{target.mention}", inline=False)
    embed.add_field(name="💰 Доступный баланс", value=f"```fix\n{coins:,} Колов\n```", inline=False)
    embed.set_footer(text="Aincrad Central Bank • Надежное хранение капитала")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="profile", description="Посмотреть подробный игровой профиль")
@check_maintenance()
async def profile(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    coins, xp, level, _, _, _, _, streak, guild_id, special_title, voice_time = get_or_create_user(target.id)
    next_level_xp = int(35 * (level ** 1.85) + 80 * level + 40)
    progress = int((xp / next_level_xp) * 10) if next_level_xp > 0 else 0
    bar = "🟩" * progress + "⬛" * (10 - progress)

    vh = int(voice_time // 3600)
    vm = int((voice_time % 3600) // 60)

    embed = discord.Embed(title=f"🛡️ ИГРОВОЙ ПРОФИЛЬ: {target.display_name}", color=0x00BFFF)
    embed.set_thumbnail(url=target.display_avatar.url)
    
    embed.add_field(name="⚔️ Этаж башни", value=f"```yaml\n{level}\n```", inline=True)
    embed.add_field(name="🪙 Капитал", value=f"```fix\n{coins:,} Колов\n```", inline=True)
    embed.add_field(name="🔥 Стрик входов", value=f"```yaml\n{streak} дн.\n```", inline=True)
    
    embed.add_field(name="🎙️ Часы в Voice", value=f"```yaml\n{vh} ч. {vm} м.\n```", inline=True)
    embed.add_field(name="🏰 Гильдия", value=f"```yaml\n{guild_id if guild_id else 'Нет гильдии'}\n```", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True) 
    
    embed.add_field(name="✨ Активный титул", value=f"```fix\n{special_title}\n```", inline=False)
    embed.add_field(name="📊 Прогресс опыта (XP)", value=f"{xp} / {next_level_xp} XP\n{bar}", inline=False)
    embed.set_footer(text="Aincrad Status Management System")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Посмотреть глобальный топ игроков Айнкрада")
@app_commands.choices(category=[
    app_commands.Choice(name="Топ по Этажам (Уровню)", value="level"),
    app_commands.Choice(name="Топ по Капиталу (Колы)", value="coins"),
    app_commands.Choice(name="Топ по Войс-Активности", value="voice")
])
@check_maintenance()
async def leaderboard(interaction: discord.Interaction, category: str = "level"):
    if category == "level":
        top = list(users_collection.find().sort([("level", -1), ("xp", -1)]).limit(10))
        title = "🏆 ТОП-10: ПРОХОДЦЫ БАШНИ (ЭТАЖИ)"
        desc = "\n".join([f"`#{i}` <@{u['_id']}> — **{u.get('level', 1)} этаж**" for i, u in enumerate(top, 1)])
    elif category == "coins":
        top = list(users_collection.find().sort("coins", -1).limit(10))
        title = "💰 ТОП-10: БОГАТЕЙШИЕ ИГРОКИ"
        desc = "\n".join([f"`#{i}` <@{u['_id']}> — **{u.get('coins', 0):,} Колов**" for i, u in enumerate(top, 1)])
    else:
        top = list(users_collection.find().sort("voice_time", -1).limit(10))
        title = "🎙️ ТОП-10: ВОЙС-АКТИВНОСТЬ"
        desc = "\n".join([f"`#{i}` <@{u['_id']}> — **{int(u.get('voice_time', 0)//3600)} ч. {int((u.get('voice_time', 0)%3600)//60)} м.**" for i, u in enumerate(top, 1)])

    embed = discord.Embed(title=title, description=desc if top else "Таблица пуста.", color=0x00BFFF)
    embed.set_footer(text="Aincrad Global Leaderboard")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="pay", description="Совершить межбанковский перевод Колов другому игроку")
@check_maintenance()
async def pay(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0 or member.id == interaction.user.id:
        return await interaction.response.send_message("❌ Некорректная операция! Нельзя переводить средства самому себе или указывать отрицательную сумму.", ephemeral=True)
    
    sender_coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    if sender_coins < amount:
        return await interaction.response.send_message("❌ Ошибка перевода: на вашем счете недостаточно средств!", ephemeral=True)

    get_or_create_user(member.id)
    users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -amount}})
    users_collection.update_one({"_id": member.id}, {"$inc": {"coins": amount}})

    embed = discord.Embed(title="💸 МЕЖБАНКОВСКИЙ ПЕРЕВОД УСПЕШЕН", color=0x00BFFF)
    embed.description = f"Со счета успешно списано и переведено **{amount:,} Колов** в пользу игрока {member.mention}."
    embed.set_footer(text="Безопасная транзакция Aincrad Network")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="daily", description="Получить ежедневную системную награду и поддержать стрик")
@check_maintenance()
async def daily(interaction: discord.Interaction):
    user_id = interaction.user.id
    current_time = time.time()
    coins, _, level, last_daily, _, _, _, streak, _, _, _ = get_or_create_user(user_id)

    if current_time - last_daily < 86400:
        left = int(86400 - (current_time - last_daily))
        return await interaction.response.send_message(f"⏳ Системная награда еще не готова. Бонус будет доступен через {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)

    if last_daily > 0 and current_time - last_daily > 172800:
        streak = 0

    streak += 1
    base_coins = 40 + (level * 8)
    base_xp = 15 + (level * 3)
    
    multiplier = 1.0 + (streak * 0.15)
    reward_coins = int(base_coins * multiplier)
    reward_xp = int(base_xp * multiplier)

    users_collection.update_one({"_id": user_id}, {"$inc": {"coins": reward_coins}, "$set": {"streak": streak, "last_daily": current_time}})
    await add_xp(interaction, user_id, reward_xp)

    embed = discord.Embed(title="🎁 ЕЖЕДНЕВНАЯ НАГРАДА КАРДИНАЛА", color=0x00FF00)
    embed.add_field(name="🔥 Серия входов", value=f"```yaml\n{streak} дн.\n```", inline=True)
    embed.add_field(name="🪙 Получено Колов", value=f"```fix\n+{reward_coins:,}\n```", inline=True)
    embed.add_field(name="⚡ Получено опыта", value=f"```yaml\n+{reward_xp} XP\n```", inline=True)
    embed.set_footer(text="Заходите ежедневно для увеличения множителя наград!")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="work", description="Отправиться на работу или зачистку ресурсов в Айнкраде")
@check_maintenance()
async def work(interaction: discord.Interaction):
    user_id = interaction.user.id
    current_time = time.time()
    _, _, level, _, last_work, _, _, _, _, _, _ = get_or_create_user(user_id)

    if current_time - last_work < 7200:
        left = int(7200 - (current_time - last_work))
        return await interaction.response.send_message(f"⏳ Персонаж устал. Отдых продлится еще {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)

    job_desc = "Зачистка подземелья опасного этажа" if level > 20 else "Сбор ценных ресурсов в безопасной зоне"
    earned = random.randint(40, 120) + (level * 2)
    
    users_collection.update_one({"_id": user_id}, {"$inc": {"coins": earned}, "$set": {"last_work": current_time}})
    await add_xp(interaction, user_id, random.randint(10, 20))

    embed = discord.Embed(title="🛠️ ОТЧЕТ О ВЫПОЛНЕНИИ РАБОТЫ", color=0x3498DB)
    embed.add_field(name="📋 Задание", value=f"```yaml\n{job_desc} (Этаж {level})\n```", inline=False)
    embed.add_field(name="💰 Награда зачислена", value=f"```fix\n+{earned} Колов\n```", inline=False)
    embed.set_footer(text="Гильдия работников Айнкрада")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="crime", description="Совершить рискованную и незаконную авантюру на свой страх и риск")
@check_maintenance()
async def crime(interaction: discord.Interaction):
    user_id = interaction.user.id
    current_time = time.time()
    coins, _, level, _, _, last_crime, _, _, _, _, _ = get_or_create_user(user_id)

    if current_time - last_crime < 14400:
        left = int(14400 - (current_time - last_crime))
        return await interaction.response.send_message(f"⏳ Слишком высокий риск привлекать внимание стражи. Ждите ещё {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)

    success = random.choice([True, False])
    embed = discord.Embed()
    if success:
        reward = random.randint(50, 130) + (level * 2)
        users_collection.update_one({"_id": user_id}, {"$inc": {"coins": reward}, "$set": {"last_crime": current_time}})
        await add_xp(interaction, user_id, 15)
        embed.title = "🥷 КРИМИНАЛЬНЫЙ УСПЕХ"
        embed.color = 0x2ECC71
        embed.add_field(name="💼 Результат", value=f"```fix\n+{reward} Колов\n```", inline=False)
        embed.description = "Авантюра удалась! Вы провернули темное дело и ушли от преследования."
    else:
        fine = random.randint(30, 70)
        new_coins = max(0, coins - fine)
        users_collection.update_one({"_id": user_id}, {"$set": {"coins": new_coins, "last_crime": current_time}})
        embed.title = "❌ ПОЙМАН СТРАЖЕЙ ПОРЯДКА"
        embed.color = 0xE74C3C
        embed.add_field(name="⚖️ Штраф", value=f"```diff\n-{fine} Колов\n```", inline=False)
        embed.description = "Вас застигли на месте преступления! Элитная стража выписала штраф."
    embed.set_footer(text="Криминальный мир нижних уровней")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rob", description="Попытаться совершить карманную кражу у другого игрока")
@check_maintenance()
async def rob(interaction: discord.Interaction, member: discord.Member):
    attacker = interaction.user
    if member.id == attacker.id:
        return await interaction.response.send_message("❌ Нельзя пытаться обокрасть самого себя!", ephemeral=True)
    if member.bot:
        return await interaction.response.send_message("❌ У ботов нет карманов с Колами.", ephemeral=True)

    target_role_names = [r.name.lower() for r in member.roles]
    if "неприкасаемый" in target_role_names or "модератор" in target_role_names:
        return await interaction.response.send_message(f"🛡️ Игрок {member.mention} защищен элитным статусом иммунитета!", ephemeral=True)

    current_time = time.time()
    att_coins, _, _, _, _, _, att_last_rob, _, _, _, _ = get_or_create_user(attacker.id)
    if current_time - att_last_rob < 10800:
        left = int(10800 - (current_time - att_last_rob))
        return await interaction.response.send_message(f"⏳ Следующая попытка ограбления будет доступна через {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)

    target_coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(member.id)
    
    if target_coins < 500:
        return await interaction.response.send_message(f"❌ У игрока {member.mention} слишком мало средств (меньше 500 Колов).", ephemeral=True)

    if att_coins > 50000 and att_coins > target_coins * 5:
        return await interaction.response.send_message(f"❌ Кодекс чести запрещает грабить бедняков!", ephemeral=True)

    potential_amount = max(20, random.randint(int(target_coins * 0.05), int(target_coins * 0.10)))
    if att_coins < potential_amount:
        return await interaction.response.send_message(f"❌ Требуется минимум **{potential_amount:,}** Колов для залога на случай провала!", ephemeral=True)

    users_collection.update_one({"_id": attacker.id}, {"$set": {"last_rob": current_time}})

    embed_loading = discord.Embed(title="🕵️ ОПЕРАЦИЯ ПО ОГРАБЛЕНИЮ", description=f"Вы скрытно подкрадываетесь к карманам игрока {member.mention}...", color=0x2C3E50)
    await interaction.response.send_message(embed=embed_loading)
    await asyncio.sleep(3.0)

    success = random.choice([True, False])
    embed_res = discord.Embed(title="🕵️ РЕЗУЛЬТАТ ОГРАБЛЕНИЯ")

    if success:
        embed_res.set_image(url="https://i.pinimg.com/originals/58/23/81/582381e4e65d4f6a027116695445d649.gif")
        users_collection.update_one({"_id": attacker.id}, {"$inc": {"coins": potential_amount}})
        users_collection.update_one({"_id": member.id}, {"$inc": {"coins": -potential_amount}})
        await add_xp(interaction, attacker.id, 20)
        embed_res.description = f"🎉 Успех! Вы ювелирно вытащили **+{potential_amount:,} Колов** у {member.mention}!"
        embed_res.color = 0x2ECC71
    else:
        embed_res.set_image(url="https://media.tenor.com/LjXd-V-BrwIAAAAd/kazuma-run-kazuma-scared.gif")
        users_collection.update_one({"_id": attacker.id}, {"$inc": {"coins": -potential_amount}})
        embed_res.description = f"🚨 Вас заметили! Вы выплатили штраф в размере **-{potential_amount:,} Колов**."
        embed_res.color = 0xE74C3C

    await interaction.edit_original_response(embed=embed_res)

# --- АЗАРТНЫЕ ИГРЫ И ДУЭЛЬ ---

class DuelAcceptView(discord.ui.View):
    def __init__(self, challenger: discord.Member, target: discord.Member, amount: int):
        super().__init__(timeout=300)
        self.challenger = challenger
        self.target = target
        self.amount = amount

    @discord.ui.button(label="Принять вызов", style=discord.ButtonStyle.green, emoji="⚔️")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("❌ Этот вызов брошен не вам!", ephemeral=True)

        for child in self.children: 
            child.disabled = True
        await interaction.response.edit_message(view=self)

        c_coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(self.challenger.id)
        t_coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(self.target.id)

        if c_coins < self.amount or t_coins < self.amount:
            return await interaction.followup.send("❌ У одного из участников больше нет нужной суммы на счете!", ephemeral=True)

        embed_loading = discord.Embed(title="⚔️ АРЕНА ДУЭЛЕЙ АЙНКРАДА", description=f"Скрещены клинки между {self.challenger.mention} и {self.target.mention}!\nСтавка матча: **{self.amount:,} Колов**.", color=0xE67E22)
        embed_loading.set_image(url="https://giffiles.alphacoders.com/131/131044.gif")
        msg = await interaction.followup.send(embed=embed_loading)
        await asyncio.sleep(3.0)

        winner = random.choice([self.challenger, self.target])
        loser = self.target if winner == self.challenger else self.challenger

        users_collection.update_one({"_id": winner.id}, {"$inc": {"coins": self.amount}})
        users_collection.update_one({"_id": loser.id}, {"$inc": {"coins": -self.amount}})

        embed_res = discord.Embed(title="⚔️ ИТОГ СМЕРТЕЛЬНОГО ПОЕДИНКА", color=0x3498DB)
        embed_res.description = f"🏆 **Победитель дуэли:** {winner.mention}!\nЗабирает ставку в размере **{self.amount:,} Колов** у {loser.mention}."

        await msg.edit(embed=embed_res, attachments=[])
        await add_xp(interaction, winner.id, 25)

    @discord.ui.button(label="Отклонить бой", style=discord.ButtonStyle.red, emoji="🏃")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("❌ Этот вызов брошен не вам!", ephemeral=True)
        for child in self.children: 
            child.disabled = True
        await interaction.response.edit_message(embed=discord.Embed(title="⚔️ ДУЭЛЬ ОТМЕНЕНА", description=f"Игрок {self.target.mention} отклонил вызов на арену.", color=0x2B2D31), view=self)

@bot.tree.command(name="duel", description="Вызвать конкретного игрока на честную дуэль с кнопкой подтверждения")
@check_maintenance()
async def duel(interaction: discord.Interaction, target: discord.Member, amount: int):
    if target.id == interaction.user.id:
        return await interaction.response.send_message("❌ Нельзя вызывать на дуэль самого себя!", ephemeral=True)
    if target.bot:
        return await interaction.response.send_message("❌ Искусственный интеллект не принимает дуэли.", ephemeral=True)
    if amount < 50:
        return await interaction.response.send_message("❌ Минимальная ставка для дуэли составляет **50 Колов**!", ephemeral=True)

    my_coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    target_coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(target.id)

    if my_coins < amount:
        return await interaction.response.send_message("❌ У вас недостаточно средств для выставления такой ставки!", ephemeral=True)
    if target_coins < amount:
        return await interaction.response.send_message(f"❌ У противника ({target.mention}) недостаточно средств для принятия вызова!", ephemeral=True)

    embed = discord.Embed(title="⚔️ ВЫЗОВ НА ДУЭЛЬ", description=f"{interaction.user.mention} бросает вызов игроку {target.mention}!\n\n• **Ставка:** `{amount:,}` Колов\n• **Условия:** Победитель забирает всё.", color=0xE67E22)
    embed.set_footer(text="У противника есть 5 минут на принятие решения.")
    await interaction.response.send_message(content=target.mention, embed=embed, view=DuelAcceptView(interaction.user, target, amount))

@bot.tree.command(name="dice", description="Бросить игральные кости против системы (Мин. ставка: 50)")
@check_maintenance()
async def dice(interaction: discord.Interaction, amount: int):
    if amount < 50:
        return await interaction.response.send_message("❌ Минимальная ставка для азартных игр — **50 Колов**!", ephemeral=True)
        
    coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    if coins < amount: return await interaction.response.send_message("❌ Недостаточно средств на балансе!", ephemeral=True)

    users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -amount}})

    embed_loading = discord.Embed(title="🎲 ИГРАЛЬНЫЕ КОСТИ", description="Ставки сделаны. Кости выбрасываются на игровой стол...", color=0x9B59B6)
    embed_loading.set_image(url="https://i.pinimg.com/originals/80/9f/ba/809fba531ccbb8e24010696ffa1503e2.gif")
    await interaction.response.send_message(embed=embed_loading)
    await asyncio.sleep(3.0)

    p_roll, b_roll = random.randint(1, 6), random.randint(1, 6)
    embed_res = discord.Embed(title="🎲 ИТОГ БРОСКА КОСТЕЙ", color=0x9B59B6)

    if p_roll > b_roll:
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": amount * 2}})
        embed_res.add_field(name="🎲 Ваш бросок", value=f"```yaml\n{p_roll}\n```", inline=True)
        embed_res.add_field(name="🤖 Бросок системы", value=f"```yaml\n{b_roll}\n```", inline=True)
        embed_res.add_field(name="💼 Выигрыш", value=f"```fix\n+{amount:,} Колов\n```", inline=False)
        embed_res.color = 0x2ECC71
    elif p_roll < b_roll:
        embed_res.add_field(name="🎲 Ваш бросок", value=f"```yaml\n{p_roll}\n```", inline=True)
        embed_res.add_field(name="🤖 Бросок системы", value=f"```yaml\n{b_roll}\n```", inline=True)
        embed_res.add_field(name="💼 Проигрыш", value=f"```diff\n-{amount:,} Колов\n```", inline=False)
        embed_res.color = 0xE74C3C
    else:
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": amount}})
        embed_res.description = f"🤝 **Ничья.** (`{p_roll}:{b_roll}`). Ставка полностью возвращена."
        embed_res.color = 0xF1C40F
    
    await interaction.edit_original_response(embed=embed_res, attachments=[])
    await add_xp(interaction, interaction.user.id, random.randint(5, 10))

@bot.tree.command(name="coinflip", description="Испытать удачу в подбросе монетки (Орел и решка, Мин. ставка: 50)")
@check_maintenance()
@app_commands.choices(choice=[app_commands.Choice(name="Орел", value="орел"), app_commands.Choice(name="Решка", value="решка")])
async def coinflip(interaction: discord.Interaction, choice: str, amount: int):
    if amount < 50:
        return await interaction.response.send_message("❌ Минимальная ставка для азартных игр — **50 Колов**!", ephemeral=True)
        
    coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    if coins < amount: return await interaction.response.send_message("❌ Недостаточно средств на балансе!", ephemeral=True)

    users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -amount}})

    embed_loading = discord.Embed(title="🪙 ОРЕЛ И РЕШКА", description="Монета подброшена высоко в воздух...", color=0xF1C40F)
    embed_loading.set_image(url="https://media.tenor.com/9PALsSO_XpsAAAAC/misaka-mikoto.gif")
    await interaction.response.send_message(embed=embed_loading)
    await asyncio.sleep(3.0)

    result = random.choice(["орел", "решка"])
    embed_res = discord.Embed(title="🪙 РЕЗУЛЬТАТ МОНЕТКИ", color=0x2ECC71)
    
    if choice == result:
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": amount * 2}})
        embed_res.add_field(name="🎯 Выпало", value=f"```yaml\n{result.upper()}\n```", inline=False)
        embed_res.add_field(name="💼 Выигрыш", value=f"```fix\n+{amount:,} Колов\n```", inline=False)
    else:
        embed_res.add_field(name="🎯 Выпало", value=f"```yaml\n{result.upper()}\n```", inline=False)
        embed_res.add_field(name="💼 Проигрыш", value=f"```diff\n-{amount:,} Колов\n```", inline=False)
        embed_res.color = 0xE74C3C
        
    await interaction.edit_original_response(embed=embed_res, attachments=[])
    await add_xp(interaction, interaction.user.id, random.randint(5, 15))

@bot.tree.command(name="roulette", description="Сыграть в смертельную Русскую рулетку (Мин. ставка: 50)")
@check_maintenance()
async def roulette(interaction: discord.Interaction, amount: int):
    if amount < 50:
        return await interaction.response.send_message("❌ Минимальная ставка для азартных игр — **50 Колов**!", ephemeral=True)
        
    coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    if coins < amount: return await interaction.response.send_message("❌ Недостаточно средств на балансе!", ephemeral=True)

    users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -amount}})

    embed_loading = discord.Embed(title="🎯 РУССКАЯ РУЛЕТКА", description="Барабан револьвера заряжен и начинает вращение...", color=0xE74C3C)
    embed_loading.set_image(url="https://i.pinimg.com/originals/ac/56/c5/ac56c5c7e6037a698e22c9a30a8dccda.gif")
    await interaction.response.send_message(embed=embed_loading)
    await asyncio.sleep(3.0)

    shot = random.choice([True, False, False, False, False, False])
    embed_res = discord.Embed(title="🎯 ИТОГ РУССКОЙ РУЛЕТКИ", color=0xE74C3C)
    
    if not shot:
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": amount * 2}})
        embed_res.add_field(name="💥 Барабан", value="```yaml\nПустая камера (ЩЕЛК)\n```", inline=False)
        embed_res.add_field(name="💼 Выигрыш", value=f"```fix\n+{amount:,} Колов\n```", inline=False)
        embed_res.color = 0x2ECC71
    else:
        embed_res.add_field(name="💀 Барабан", value="```diff\n- Смертельный выстрел (БАХ)\n```", inline=False)
        embed_res.add_field(name="💼 Проигрыш", value=f"```diff\n-{amount:,} Колов\n```", inline=False)
        
    await interaction.edit_original_response(embed=embed_res, attachments=[])
    await add_xp(interaction, interaction.user.id, random.randint(10, 20))


# --- МАГАЗИН И УПРАВЛЕНИЕ РОЛЯМИ / ТИТУЛАМИ ---

class CustomRoleModal(discord.ui.Modal, title="Создание кастомной роли"):
    role_name = discord.ui.TextInput(label="Название роли", placeholder="Темный Страж", max_length=50)
    role_color = discord.ui.TextInput(label="HEX-код цвета (без #)", placeholder="FF5733", max_length=6, min_length=6)

    def __init__(self, price: int):
        super().__init__()
        self.price = price

    async def on_submit(self, interaction: discord.Interaction):
        r_name = self.role_name.value.strip()

        exists = custom_roles_collection.find_one({"role_name": {"$regex": f"^{r_name}$", "$options": "i"}})
        if exists or discord.utils.get(interaction.guild.roles, name=r_name):
            return await interaction.response.send_message("❌ Роль с таким названием уже зарегистрирована!", ephemeral=True)

        try:
            color_int = int(self.role_color.value.strip(), 16)
        except ValueError:
            return await interaction.response.send_message("❌ Неверный формат HEX-цвета!", ephemeral=True)

        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}})
        
        try:
            new_role = await interaction.guild.create_role(name=r_name, color=discord.Color(color_int))
            await interaction.user.add_roles(new_role)
            custom_roles_collection.insert_one({"role_id": new_role.id, "user_id": interaction.user.id, "role_name": r_name})
            await interaction.response.send_message(f"✅ Роль **{r_name}** успешно создана и добавлена в ваш арсенал!", ephemeral=True)
        except Exception:
            users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": self.price}})
            await interaction.response.send_message("❌ Ошибка при создании роли. Средства возвращены.", ephemeral=True)

class CustomTitleModal(discord.ui.Modal, title="Покупка кастомного титула"):
    title_text = discord.ui.TextInput(label="Текст вашего титула", placeholder="Черный Мечник", max_length=30)

    def __init__(self, price: int):
        super().__init__()
        self.price = price

    async def on_submit(self, interaction: discord.Interaction):
        t_text = self.title_text.value.strip()
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}, "$set": {"special_title": t_text}})
        titles_collection.insert_one({"user_id": interaction.user.id, "title_name": t_text})
        await interaction.response.send_message(f"👑 Престижный кастомный титул **{t_text}** успешно приобретен!", ephemeral=True)

class ShopButtonsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Неприкасаемый (15,000)", style=discord.ButtonStyle.blurple, emoji="🛡️")
    async def buy_untouchable(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = discord.utils.get(interaction.guild.roles, name="Неприкасаемый")
        if not role:
            return await interaction.response.send_message("❌ Роль «Неприкасаемый» не найдена на сервере. Обратитесь к администрации.", ephemeral=True)
            
        if role in interaction.user.roles:
            return await interaction.response.send_message("❌ У вас уже активирован этот статус!", ephemeral=True)
        
        price = 15000
        coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < price: return await interaction.response.send_message("❌ Недостаточно средств! Требуется 15,000 Колов.", ephemeral=True)
        
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -price}})
        await interaction.user.add_roles(role)
        await interaction.response.send_message(f"🎉 Вы успешно приобрели элитный статус **Неприкасаемый**!", ephemeral=True)

    @discord.ui.button(label="Кастомная роль (10,000)", style=discord.ButtonStyle.green, emoji="✨")
    async def buy_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        count = custom_roles_collection.count_documents({"user_id": interaction.user.id})
        if count >= 2:
            return await interaction.response.send_message("❌ Достигнут лимит кастомных ролей (максимум 2)!", ephemeral=True)
        
        coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < 10000: return await interaction.response.send_message("❌ Недостаточно средств! Требуется 10,000 Колов.", ephemeral=True)
        await interaction.response.send_modal(CustomRoleModal(10000))

    @discord.ui.button(label="Кастомный титул (5,000)", style=discord.ButtonStyle.grey, emoji="👑")
    async def buy_title(self, interaction: discord.Interaction, button: discord.ui.Button):
        price = 5000
        coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < price: return await interaction.response.send_message("❌ Недостаточно средств! Требуется 5,000 Колов.", ephemeral=True)
        await interaction.response.send_modal(CustomTitleModal(5000))

@bot.tree.command(name="shop", description="Интерактивный расширенный магазин уникальных предметов")
@check_maintenance()
async def shop(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛒 ЦЕНТРАЛЬНЫЙ ИГРОВОЙ МАГАЗИН АЙНКРАДА", 
        description="Добро пожаловать в торговый интерфейс системы. Выберите нужную привилегию для покупки с помощью кнопок ниже.", 
        color=0x00BFFF
    )
    # Заменено на надежную ссылку Tenor
    embed.set_image(url="https://media.tenor.com/D_i-i_lXw1QAAAAC/sao-agil.gif")
    embed.add_field(
        name="🛡️ Элитный статус «Неприкасаемый»", 
        value="```fix\nСтоимость: 15,000 Колов\n```\nОбеспечивает абсолютный и бессрочный иммунитет от любых попыток карманных краж и грабежей другими игроками.", 
        inline=False
    )
    embed.add_field(
        name="✨ Персональная Кастомная Роль", 
        value="```fix\nСтоимость: 10,000 Колов\n```\nПозволяет зарегистрировать собственное уникальное имя роли и персональный цвет в формате HEX с выдачей в ваш профиль.", 
        inline=False
    )
    embed.add_field(
        name="👑 Уникальный Кастомный Титул", 
        value="```fix\nСтоимость: 5,000 Колов\n```\nУстанавливает индивидуальный престижный текстовый статус, который отображается в вашем персональном `/profile`.", 
        inline=False
    )
    embed.set_footer(text="Aincrad Economy System • Используйте кнопки интерфейса для взаимодействия")
    await interaction.response.send_message(embed=embed, view=ShopButtonsView())
    
class EditRoleModal(discord.ui.Modal, title="Редактирование кастомной роли"):
    role_name = discord.ui.TextInput(label="Новое название роли", max_length=50)
    role_color = discord.ui.TextInput(label="Новый HEX-цвет (без #)", placeholder="FF5733", max_length=6, min_length=6)

    def __init__(self, role: discord.Role, price: int):
        super().__init__()
        self.role = role
        self.price = price

    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.role_name.value.strip()
        exists = custom_roles_collection.find_one({"role_name": {"$regex": f"^{new_name}$", "$options": "i"}, "role_id": {"$ne": self.role.id}})
        if exists or discord.utils.get(interaction.guild.roles, name=new_name):
            return await interaction.response.send_message("❌ Ошибка: Роль с таким названием уже существует!", ephemeral=True)

        try:
            color_int = int(self.role_color.value.strip(), 16)
        except ValueError:
            return await interaction.response.send_message("❌ Неверный формат HEX-цвета!", ephemeral=True)

        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}})
        await self.role.edit(name=new_name, color=discord.Color(color_int))
        custom_roles_collection.update_one({"role_id": self.role.id}, {"$set": {"role_name": new_name}})
        auction_collection.update_one({"role_id": self.role.id}, {"$set": {"role_name": new_name}})
        await interaction.response.send_message(f"✅ Параметры роли успешно обновлены на **{new_name}**!", ephemeral=True)

class EditRoleSelect(discord.ui.View):
    def __init__(self, roles_list, price):
        super().__init__(timeout=300)
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles_list]
        self.select = discord.ui.Select(placeholder="Выберите роль для изменения...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
        self.price = price

    async def select_callback(self, interaction: discord.Interaction):
        role_id = int(self.select.values[0])
        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message("❌ Указанная роль не найдена.", ephemeral=True)
        await interaction.response.send_modal(EditRoleModal(role, self.price))

@bot.tree.command(name="editrole", description="Изменить цвет и название своей кастомной роли (3,000 Колов)")
@check_maintenance()
async def editrole(interaction: discord.Interaction):
    coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    if coins < 3000:
        return await interaction.response.send_message("❌ Для редактирования требуется минимум 3,000 Колов!", ephemeral=True)

    rows = list(custom_roles_collection.find({"user_id": interaction.user.id}))
    if not rows:
        return await interaction.response.send_message("❌ У вас нет купленных кастомных ролей для изменения!", ephemeral=True)

    user_roles = [interaction.guild.get_role(r["role_id"]) for r in rows if interaction.guild.get_role(r["role_id"])]
    if not user_roles:
        return await interaction.response.send_message("❌ Ваши роли не обнаружены на сервере.", ephemeral=True)

    view = EditRoleSelect(user_roles, 3000)
    embed = discord.Embed(title="🛠️ РЕДАКТИРОВАНИЕ КАСТОМНОЙ РОЛИ", description="Выберите из выпадающего списка роль, параметры которой хотите изменить:", color=0x3498DB)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class DeleteRoleSelect(discord.ui.View):
    def __init__(self, roles_list, price):
        super().__init__(timeout=300)
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles_list]
        self.select = discord.ui.Select(placeholder="Выберите роль для удаления...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
        self.price = price

    async def select_callback(self, interaction: discord.Interaction):
        role_id = int(self.select.values[0])
        role = interaction.guild.get_role(role_id)
        
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}})
        custom_roles_collection.delete_one({"role_id": role_id})
        auction_collection.delete_one({"role_id": role_id})

        if role:
            try:
                await role.delete(reason="Удалено владельцем через команду")
            except Exception:
                pass
                
        await interaction.response.send_message(f"🗑️ Кастомная роль успешно удалена! Комиссия: **{self.price:,} Колов**.", ephemeral=True)

@bot.tree.command(name="deleterole", description="Полностью удалить свою кастомную роль с сервера (5,000 Колов)")
@check_maintenance()
async def deleterole(interaction: discord.Interaction):
    coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    if coins < 5000:
        return await interaction.response.send_message("❌ Для удаления роли требуется 5,000 Колов!", ephemeral=True)

    rows = list(custom_roles_collection.find({"user_id": interaction.user.id}))
    if not rows:
        return await interaction.response.send_message("❌ У вас нет кастомных ролей для удаления!", ephemeral=True)

    user_roles = [interaction.guild.get_role(r["role_id"]) for r in rows if interaction.guild.get_role(r["role_id"])]
    if not user_roles:
        return await interaction.response.send_message("❌ Ваши роли не найдены на сервере.", ephemeral=True)

    view = DeleteRoleSelect(user_roles, 5000)
    embed = discord.Embed(title="🗑️ УДАЛЕНИЕ КАСТОМНОЙ РОЛИ", description="Выберите роль из списка, которую хотите навсегда стереть с сервера:", color=0xE74C3C)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class SetTitleSelect(discord.ui.View):
    def __init__(self, titles_list):
        super().__init__(timeout=300)
        options = [discord.SelectOption(label=t, value=t) for t in titles_list]
        self.select = discord.ui.Select(placeholder="Выберите титул из списка...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        chosen_title = self.select.values[0]
        users_collection.update_one({"_id": interaction.user.id}, {"$set": {"special_title": chosen_title}})
        await interaction.response.send_message(f"✅ Активный титул в профиле успешно изменен на: **{chosen_title}**!", ephemeral=True)

@bot.tree.command(name="settitle", description="Выбрать активный титул из ранее купленных")
@check_maintenance()
async def settitle(interaction: discord.Interaction):
    rows = list(titles_collection.find({"user_id": interaction.user.id}))
    if not rows:
        return await interaction.response.send_message("❌ У вас пока нет приобретенных кастомных титулов!", ephemeral=True)
    titles = [r["title_name"] for r in rows]
    embed = discord.Embed(title="👑 ВЫБОР АКТИВНОГО ТИТУЛА", description="Выберите титул, который будет отображаться в вашем `/profile`:", color=0xFFD700)
    await interaction.response.send_message(embed=embed, view=SetTitleSelect(titles), ephemeral=True)


# =====================================================================
# --- ГИЛЬДИИ С ПОЛНЫМ УПРАВЛЕНИЕМ, ЛОГАМИ И ИНВАЙТАМИ ---
# =====================================================================

class GuildDepositModal(discord.ui.Modal, title="Пополнение казны"):
    amount = discord.ui.TextInput(label="Сумма в Колах", placeholder="1000", max_length=10)
    def __init__(self, guild_name):
        super().__init__()
        self.guild_name = guild_name
    async def on_submit(self, interaction: discord.Interaction):
        try: val = int(self.amount.value)
        except ValueError: return await interaction.response.send_message("❌ Ошибка формата!", ephemeral=True)
        if val <= 0: return await interaction.response.send_message("❌ Сумма должна быть больше 0!", ephemeral=True)
        coins, *_ = get_or_create_user(interaction.user.id)
        if coins < val: return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -val}})
        guilds_collection.update_one({"guild_name": self.guild_name}, {"$inc": {"bank": val}})
        await interaction.response.send_message(f"✅ Внесено **{val:,}** Колов в казну гильдии!", ephemeral=True)

class GuildSetFeeModal(discord.ui.Modal, title="Настройка цены за вход"):
    fee_input = discord.ui.TextInput(label="Цена (0 = бесплатно)", placeholder="500", max_length=10)
    def __init__(self, guild_name):
        super().__init__()
        self.guild_name = guild_name
    async def on_submit(self, interaction: discord.Interaction):
        try: val = int(self.fee_input.value)
        except ValueError: return await interaction.response.send_message("❌ Ошибка формата!", ephemeral=True)
        if val < 0: return await interaction.response.send_message("❌ Цена не может быть отрицательной!", ephemeral=True)
        guilds_collection.update_one({"guild_name": self.guild_name}, {"$set": {"entry_fee": val}})
        await interaction.response.send_message(f"✅ Цена за вход в гильдию установлена: **{val:,} Колов**.", ephemeral=True)

class GuildInviteAcceptView(discord.ui.View):
    def __init__(self, guild_name, target_id):
        super().__init__(timeout=300)
        self.guild_name = guild_name
        self.target_id = target_id
    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, emoji="🤝")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id: return await interaction.response.send_message("❌ Это приглашение адресовано не вам!", ephemeral=True)
        g_data = guilds_collection.find_one({"guild_name": self.guild_name})
        if not g_data: return await interaction.response.send_message("❌ Данная гильдия была распущена.", ephemeral=True)
        coins, _, _, _, _, _, _, _, user_g, *_ = get_or_create_user(interaction.user.id)
        if user_g: return await interaction.response.send_message("❌ Вы уже состоите в гильдии!", ephemeral=True)
        fee = g_data.get('entry_fee', 0)
        if coins < fee: return await interaction.response.send_message(f"❌ Для входа требуется **{fee:,}** Колов.", ephemeral=True)
        users_collection.update_one({"_id": interaction.user.id}, {"$set": {"guild_id": self.guild_name}, "$inc": {"coins": -fee}})
        if fee > 0: guilds_collection.update_one({"guild_name": self.guild_name}, {"$inc": {"bank": fee}})
        for c in self.children: c.disabled = True
        embed = interaction.message.embeds[0]
        embed.color, embed.title, embed.description = 0x2ECC71, "✅ ПРИГЛАШЕНИЕ ПРИНЯТО", f"{interaction.user.mention} официально вступил в **{self.guild_name}**!"
        await interaction.response.edit_message(embed=embed, view=self)
    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id: return await interaction.response.send_message("❌ Это приглашение адресовано не вам!", ephemeral=True)
        for c in self.children: c.disabled = True
        embed = interaction.message.embeds[0]
        embed.color, embed.title, embed.description = 0xE74C3C, "❌ ПРИГЛАШЕНИЕ ОТКЛОНЕНО", f"{interaction.user.mention} отклонил приглашение."
        await interaction.response.edit_message(embed=embed, view=self)

class GuildInviteModal(discord.ui.Modal, title="Пригласить игрока"):
    uid_input = discord.ui.TextInput(label="Discord ID пользователя", max_length=25)
    def __init__(self, guild_name):
        super().__init__()
        self.guild_name = guild_name
    async def on_submit(self, interaction: discord.Interaction):
        try: target_id = int(self.uid_input.value.strip())
        except ValueError: return await interaction.response.send_message("❌ Неверный формат ID!", ephemeral=True)
        target_user = interaction.guild.get_member(target_id)
        if not target_user or target_user.bot: return await interaction.response.send_message("❌ Пользователь не найден на сервере или это бот.", ephemeral=True)
        embed = discord.Embed(title="📨 ПРИГЛАШЕНИЕ В ГИЛЬДИЮ", description=f"{interaction.user.mention} приглашает вас присоединиться к элитной гильдии **{self.guild_name}**!", color=0x9B59B6)
        await interaction.channel.send(content=target_user.mention, embed=embed, view=GuildInviteAcceptView(self.guild_name, target_id))
        await interaction.response.send_message("✅ Персональное приглашение отправлено в чат!", ephemeral=True)

class GuildKickSelectView(discord.ui.View):
    def __init__(self, guild_name, members_list):
        super().__init__(timeout=180)
        self.guild_name = guild_name
        options = [
            discord.SelectOption(label=m.display_name, value=str(m.id), description=f"ID: {m.id}") 
            for m in members_list[:25]
        ]
        self.select = discord.ui.Select(placeholder="Выберите участника для изгнания...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        target_id = int(self.select.values[0])
        if target_id == interaction.user.id:
            return await interaction.response.send_message("❌ Нельзя изгнать самого себя!", ephemeral=True)
            
        g_data = guilds_collection.find_one({"guild_name": self.guild_name})
        if g_data.get("leader_id") == target_id:
            return await interaction.response.send_message("❌ Нельзя изгнать лидера гильдии!", ephemeral=True)

        users_collection.update_one({"_id": target_id}, {"$set": {"guild_id": None}})
        if target_id in g_data.get("co_leaders", []):
            guilds_collection.update_one({"guild_name": self.guild_name}, {"$pull": {"co_leaders": target_id}})

        embed = discord.Embed(
            title="────── ┌ 👢 ИЗГНАНИЕ ИЗ ГИЛЬДИИ ┐ ──────",
            description=f"Участник <@{target_id}> был успешно изгнан из гильдии **{self.guild_name}**.",
            color=0xE74C3C
        )
        await interaction.response.edit_message(embed=embed, view=None)

class GuildCoLeaderSelectView(discord.ui.View):
    def __init__(self, guild_name, members_list):
        super().__init__(timeout=180)
        self.guild_name = guild_name
        options = [
            discord.SelectOption(label=m.display_name, value=str(m.id), description=f"ID: {m.id}") 
            for m in members_list[:25]
        ]
        self.select = discord.ui.Select(placeholder="Выберите участника в Вице-президенты...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        target_id = int(self.select.values[0])
        g_data = guilds_collection.find_one({"guild_name": self.guild_name})
        co_leaders = g_data.get("co_leaders", [])
        
        if target_id in co_leaders:
            return await interaction.response.send_message("❌ Этот участник уже является вице-президентом!", ephemeral=True)
            
        if len(co_leaders) >= 3:
            return await interaction.response.send_message("❌ Достигнут лимит вице-президентов (максимум 3)!", ephemeral=True)

        guilds_collection.update_one({"guild_name": self.guild_name}, {"$push": {"co_leaders": target_id}})
        
        embed = discord.Embed(
            title="────── ┌ 👑 НАЗНАЧЕНИЕ ВИЦЕ-ПРЕЗИДЕНТА ┐ ──────",
            description=f"Игрок <@{target_id}> успешно назначен **Вице-президентом** гильдии **{self.guild_name}**!",
            color=0x00BFFF
        )
        await interaction.response.edit_message(embed=embed, view=None)

class GuildLogsView(discord.ui.View):
    def __init__(self, guild_name, requests):
        super().__init__(timeout=120)
        self.guild_name, self.requests, self.index = guild_name, requests, 0
        self.update_btn()
    def update_btn(self):
        self.prev_btn.disabled = self.index == 0
        self.next_btn.disabled = self.index >= len(self.requests) - 1
        if not self.requests: self.accept_btn.disabled = self.reject_btn.disabled = True
    def get_embed(self):
        if not self.requests: return discord.Embed(title="📋 ЛОГИ ЗАЯВОК", description="Активных заявок на вступление нет.", color=0x95A5A6)
        req = self.requests[self.index]
        return discord.Embed(title="📋 УПРАВЛЕНИЕ ЗАЯВКАМИ", description=f"Игрок <@{req['user_id']}> желает вступить в гильдию.\nЗаявка `{self.index + 1}` из `{len(self.requests)}`", color=0xF1C40F)
    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, emoji="✅", row=0)
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        req, g_data = self.requests[self.index], guilds_collection.find_one({"guild_name": self.guild_name})
        uid, fee = req["user_id"], g_data.get('entry_fee', 0)
        coins, _, _, _, _, _, _, _, user_g, *_ = get_or_create_user(uid)
        guild_requests_collection.delete_one({"_id": req["_id"]})
        if user_g or coins < fee:
            await interaction.response.send_message("❌ Игрок уже вступил в другую гильдию или у него не хватает средств. Заявка аннулирована.", ephemeral=True)
        else:
            users_collection.update_one({"_id": uid}, {"$set": {"guild_id": self.guild_name}, "$inc": {"coins": -fee}})
            if fee > 0: guilds_collection.update_one({"guild_name": self.guild_name}, {"$inc": {"bank": fee}})
            await interaction.response.send_message(f"✅ Игрок <@{uid}> успешно принят в гильдию!", ephemeral=True)
        self.requests.pop(self.index)
        if self.index > 0: self.index -= 1
        self.update_btn()
        await interaction.message.edit(embed=self.get_embed(), view=self)
    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌", row=0)
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_requests_collection.delete_one({"_id": self.requests[self.index]["_id"]})
        await interaction.response.send_message("🗑️ Заявка игрока отклонена.", ephemeral=True)
        self.requests.pop(self.index)
        if self.index > 0: self.index -= 1
        self.update_btn()
        await interaction.message.edit(embed=self.get_embed(), view=self)
    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.grey, row=1)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index -= 1
        self.update_btn()
        await interaction.message.edit(embed=self.get_embed(), view=self)
    @discord.ui.button(label="➡️", style=discord.ButtonStyle.grey, row=1)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index += 1
        self.update_btn()
        await interaction.message.edit(embed=self.get_embed(), view=self)

class GuildLeaderView(discord.ui.View):
    def __init__(self, guild_name):
        super().__init__(timeout=120)
        self.guild_name = guild_name
    @discord.ui.button(label="Набор (Откр/Закр)", style=discord.ButtonStyle.blurple, emoji="🔒", row=0)
    async def toggle_private(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = guilds_collection.find_one({"guild_name": self.guild_name})
        new_status = not g.get("is_private", False)
        guilds_collection.update_one({"guild_name": self.guild_name}, {"$set": {"is_private": new_status}})
        await interaction.response.send_message(f"🔒 Набор в гильдию теперь: **{'ЗАКРЫТ (требуется одобрение)' if new_status else 'ОТКРЫТ (свободный вход)'}**.", ephemeral=True)
    @discord.ui.button(label="Цена входа", style=discord.ButtonStyle.green, emoji="💰", row=0)
    async def set_fee(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuildSetFeeModal(self.guild_name))
    @discord.ui.button(label="Инвайт в чат", style=discord.ButtonStyle.secondary, emoji="📨", row=0)
    async def invite_player(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuildInviteModal(self.guild_name))
    @discord.ui.button(label="Логи (Заявки)", style=discord.ButtonStyle.secondary, emoji="📋", row=1)
    async def view_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        reqs = list(guild_requests_collection.find({"guild_name": self.guild_name, "type": "application"}))
        view = GuildLogsView(self.guild_name, reqs)
        await interaction.response.send_message(embed=view.get_embed(), view=view, ephemeral=True)
    @discord.ui.button(label="Изгнать игрока", style=discord.ButtonStyle.red, emoji="👢", row=1)
    async def kick_player(self, interaction: discord.Interaction, button: discord.ui.Button):
        members_data = list(users_collection.find({"guild_id": self.guild_name}))
        members_list = [interaction.guild.get_member(m["_id"]) for m in members_data if interaction.guild.get_member(m["_id"]) and m["_id"] != interaction.user.id]
        if not members_list:
            return await interaction.response.send_message("❌ В вашей гильдии нет других участников для изгнания.", ephemeral=True)
        view = GuildKickSelectView(self.guild_name, members_list)
        embed = discord.Embed(
            title="────── ┌ 👢 ИЗГНАНИЕ УЧАСТНИКА ┐ ──────",
            description="Выберите участника из выпадающего списка, которого хотите изгнать из гильдии:",
            color=0xE74C3C
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    @discord.ui.button(label="Вице-президент", style=discord.ButtonStyle.blurple, emoji="👑", row=1)
    async def set_co_leader(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = guilds_collection.find_one({"guild_name": self.guild_name})
        if interaction.user.id != g.get("leader_id"):
            return await interaction.response.send_message("❌ Назначать вице-президентов может только Главный Лидер гильдии!", ephemeral=True)
        members_data = list(users_collection.find({"guild_id": self.guild_name}))
        members_list = [interaction.guild.get_member(m["_id"]) for m in members_data if interaction.guild.get_member(m["_id"]) and m["_id"] != interaction.user.id]
        if not members_list:
            return await interaction.response.send_message("❌ В вашей гильдии нет других участников.", ephemeral=True)
        view = GuildCoLeaderSelectView(self.guild_name, members_list)
        embed = discord.Embed(
            title="────── ┌ 👑 НАЗНАЧЕНИЕ ЗАМЕСТИТЕЛЯ ┐ ──────",
            description="Выберите участника из выпадающего списка, чтобы назначить его Вице-президентом:",
            color=0x00BFFF
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class GuildCreateModal(discord.ui.Modal, title="Создание гильдии"):
    guild_name = discord.ui.TextInput(label="Название гильдии", max_length=30)
    async def on_submit(self, interaction: discord.Interaction):
        uid, name, price = interaction.user.id, self.guild_name.value.strip(), 25000
        coins, *_ = get_or_create_user(uid)
        user_g = get_or_create_user(uid)[8]
        if user_g: return await interaction.response.send_message("❌ Вы уже состоите в гильдии!", ephemeral=True)
        if coins < price: return await interaction.response.send_message("❌ Для создания гильдии требуется **25,000** Колов!", ephemeral=True)
        if guilds_collection.find_one({"guild_name": {"$regex": f"^{name}$", "$options": "i"}}): 
            return await interaction.response.send_message("❌ Гильдия с таким названием уже существует!", ephemeral=True)
        users_collection.update_one({"_id": uid}, {"$inc": {"coins": -price}, "$set": {"guild_id": name}})
        guilds_collection.insert_one({"guild_name": name, "leader_id": uid, "co_leaders": [], "bank": 0, "level": 1, "is_private": False, "entry_fee": 0})
        await interaction.response.send_message(f"🏰 Гильдия **{name}** успешно создана!", ephemeral=True)

class GuildJoinSelectView(discord.ui.View):
    def __init__(self, guilds_list):
        super().__init__(timeout=180)
        options = [
            discord.SelectOption(
                label=g["guild_name"], 
                description=f"Вход: {g.get('entry_fee', 0):,} Колов | {'🔒 Закрытая' if g.get('is_private') else '🟢 Открытая'}",
                value=g["guild_name"]
            ) for g in guilds_list[:25]
        ]
        self.select = discord.ui.Select(placeholder="Выберите гильдию из списка...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        target_g = self.select.values[0]
        g_data = guilds_collection.find_one({"guild_name": target_g})
        if not g_data: 
            return await interaction.response.send_message("❌ Эта гильдия больше не существует.", ephemeral=True)
            
        coins, *_ = get_or_create_user(interaction.user.id)
        user_g = get_or_create_user(interaction.user.id)[8]
        if user_g: 
            return await interaction.response.send_message("❌ Вы уже состоите в гильдии! Сначала покиньте текущую.", ephemeral=True)
            
        fee = g_data.get('entry_fee', 0)
        if coins < fee: 
            return await interaction.response.send_message(f"❌ Недостаточно средств! Вход в гильдию стоит **{fee:,}** Колов.", ephemeral=True)
            
        if g_data.get('is_private'):
            guild_requests_collection.update_one(
                {"user_id": interaction.user.id, "guild_name": g_data["guild_name"]}, 
                {"$set": {"type": "application"}}, 
                upsert=True
            )
            await interaction.response.send_message(f"✅ Набор в **{target_g}** закрытый. Ваша заявка успешно отправлена лидеру на рассмотрение!", ephemeral=True)
        else:
            users_collection.update_one({"_id": interaction.user.id}, {"$set": {"guild_id": g_data["guild_name"]}, "$inc": {"coins": -fee}})
            if fee > 0: 
                guilds_collection.update_one({"guild_name": g_data["guild_name"]}, {"$inc": {"bank": fee}})
            await interaction.response.send_message(f"🎉 Вы успешно вступили в гильдию **{target_g}**!", ephemeral=True)

class GuildMainView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.g_name = get_or_create_user(user_id)[8]
    @discord.ui.button(label="Создать (25k)", style=discord.ButtonStyle.green, emoji="🏰", row=0)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuildCreateModal())
    @discord.ui.button(label="Вступить", style=discord.ButtonStyle.blurple, emoji="🤝", row=0)
    async def join_guild(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.g_name: return await interaction.response.send_message("❌ Вы уже состоите в гильдии!", ephemeral=True)
        all_guilds = list(guilds_collection.find())
        if not all_guilds:
            return await interaction.response.send_message("❌ На сервере еще не создано ни одной гильдии.", ephemeral=True)
        view = GuildJoinSelectView(all_guilds)
        embed = discord.Embed(
            title="────── ┌ 🤝 ВСТУПЛЕНИЕ В ГИЛЬДИЮ ┐ ──────",
            description="Выберите нужную гильдию из выпадающего списка ниже:", 
            color=0x00BFFF
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    @discord.ui.button(label="Информация", style=discord.ButtonStyle.blurple, emoji="🛡️", row=0)
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.g_name: return await interaction.response.send_message("❌ Вы не состоите в гильдии!", ephemeral=True)
        g_data = guilds_collection.find_one({"guild_name": self.g_name})
        members = list(users_collection.find({"guild_id": self.g_name}))
        m_str = ", ".join([f"<@{m['_id']}>" for m in members]) if members else "Пусто"
        
        co_leaders = g_data.get('co_leaders', [])
        co_str = ", ".join([f"<@{cid}>" for cid in co_leaders]) if co_leaders else "Нет"

        embed = discord.Embed(
            title=f"────── ┌ 🛡️ ГИЛЬДИЯ: {self.g_name} ┐ ──────", 
            color=0x9B59B6
        )
        embed.add_field(name="👑 Главный лидер", value=f"<@{g_data['leader_id']}>", inline=False)
        embed.add_field(name="⭐ Вице-президенты", value=co_str, inline=False)
        embed.add_field(name="💰 Казна", value=f"```fix\n{g_data.get('bank', 0):,} Колов\n```", inline=True)
        embed.add_field(name="🎟️ Цена входа", value=f"```fix\n{g_data.get('entry_fee', 0):,} Колов\n```", inline=True)
        embed.add_field(name=f"👥 Участники ({len(members)})", value=m_str, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    @discord.ui.button(label="Пополнить", style=discord.ButtonStyle.grey, emoji="💰", row=1)
    async def deposit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.g_name: return await interaction.response.send_message("❌ Вы не состоите в гильдии.", ephemeral=True)
        await interaction.response.send_modal(GuildDepositModal(self.g_name))
    @discord.ui.button(label="Панель лидера", style=discord.ButtonStyle.grey, emoji="⚙️", row=1)
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.g_name: return await interaction.response.send_message("❌ Вы не состоите в гильдии.", ephemeral=True)
        g_data = guilds_collection.find_one({"guild_name": self.g_name})
        is_leader = g_data.get('leader_id') == interaction.user.id
        is_co_leader = interaction.user.id in g_data.get('co_leaders', [])
        if not is_leader and not is_co_leader: 
            return await interaction.response.send_message("❌ Управлять гильдией может только её лидер или вице-президент!", ephemeral=True)
        await interaction.response.send_message("⚙️ Панель управления гильдией:", view=GuildLeaderView(self.g_name), ephemeral=True)
    @discord.ui.button(label="Покинуть", style=discord.ButtonStyle.red, emoji="🚪", row=1)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.g_name: return await interaction.response.send_message("❌ Вы не состоите в гильдии.", ephemeral=True)
        g_data = guilds_collection.find_one({"guild_name": self.g_name})
        users_collection.update_one({"_id": interaction.user.id}, {"$set": {"guild_id": None}})
        if interaction.user.id in g_data.get("co_leaders", []):
            guilds_collection.update_one({"guild_name": self.g_name}, {"$pull": {"co_leaders": interaction.user.id}})
        if g_data['leader_id'] == interaction.user.id:
            new_member = users_collection.find_one({"guild_id": self.g_name})
            if new_member: 
                guilds_collection.update_one({"guild_name": self.g_name}, {"$set": {"leader_id": new_member["_id"]}})
                if new_member["_id"] in g_data.get("co_leaders", []):
                    guilds_collection.update_one({"guild_name": self.g_name}, {"$pull": {"co_leaders": new_member["_id"]}})
                await interaction.response.send_message(f"🚪 Вы покинули гильдию. Новым лидером назначен <@{new_member['_id']}>.", ephemeral=True)
            else: 
                guilds_collection.delete_one({"guild_name": self.g_name})
                await interaction.response.send_message("🚪 Вы покинули гильдию. Поскольку участников не осталось, гильдия была распущена.", ephemeral=True)
        else: await interaction.response.send_message("🚪 Вы успешно покинули гильдию.", ephemeral=True)

@bot.tree.command(name="guild", description="Управление гильдиями")
@check_maintenance()
async def guild_menu(interaction: discord.Interaction):
    embed = discord.Embed(
        title="────── ┌ 🏰 ГИЛЬДИИ АЙНКРАДА ┐ ──────", 
        description="Используйте кнопки ниже для взаимодействия:", 
        color=0x9B59B6
    )
    # Заменено на надежную ссылку Tenor
    embed.set_image(url="https://media.tenor.com/V-E-j1tXb8sAAAAC/sword-art-online-sao.gif")
    await interaction.response.send_message(embed=embed, view=GuildMainView(interaction.user.id))

# --- АУКЦИОН РОЛЕЙ (ЕДИНОЕ МЕНЮ С ПАГИНАЦИЕЙ И ФИКСАМИ) ---

class SellRoleModal(discord.ui.Modal, title="Выставить роль на аукцион"):
    price_input = discord.ui.TextInput(label="Цена в Колах", placeholder="5000", max_length=10)

    def __init__(self, role_id, role_name):
        super().__init__()
        self.role_id = role_id
        self.role_name = role_name

    async def on_submit(self, interaction: discord.Interaction):
        try: 
            price = int(self.price_input.value)
        except ValueError: 
            return await interaction.response.send_message("❌ Неверный формат цены!", ephemeral=True)
            
        if price <= 0: 
            return await interaction.response.send_message("❌ Цена должна быть больше 0!", ephemeral=True)
            
        last_item = auction_collection.find_one(sort=[("sale_id", -1)])
        next_id = (last_item["sale_id"] + 1) if last_item else 1
        
        auction_collection.insert_one({
            "sale_id": next_id, 
            "role_id": self.role_id, 
            "seller_id": interaction.user.id, 
            "price": price, 
            "role_name": self.role_name
        })
        
        role = interaction.guild.get_role(self.role_id)
        if role: 
            try: 
                await interaction.user.remove_roles(role)
            except: 
                pass
                
        await interaction.response.send_message(f"✅ Роль **{self.role_name}** выставлена на глобальный аукцион за **{price:,} Колов**!", ephemeral=True)

class SellRoleSelect(discord.ui.View):
    def __init__(self, roles_list):
        super().__init__(timeout=300)
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles_list]
        self.select = discord.ui.Select(placeholder="Выберите роль для выставления...", options=options)
        self.select.callback = self.cb
        self.add_item(self.select)

    async def cb(self, interaction: discord.Interaction):
        r_id = int(self.select.values[0])
        r = interaction.guild.get_role(r_id)
        if not r: 
            return await interaction.response.send_message("❌ Роль не найдена.", ephemeral=True)
        await interaction.response.send_modal(SellRoleModal(r_id, r.name))

class AuctionPagingView(discord.ui.View):
    def __init__(self, items):
        super().__init__(timeout=300)
        self.items = items
        self.page = 0
        self.per_page = 5
        self.update_components()

    def update_components(self):
        self.clear_items()
        max_pages = max(0, (len(self.items) - 1) // self.per_page)

        prev_btn = discord.ui.Button(label="Назад", style=discord.ButtonStyle.grey, emoji="⬅️", disabled=self.page == 0)
        prev_btn.callback = self.prev_page
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(label="Вперед", style=discord.ButtonStyle.grey, emoji="➡️", disabled=self.page >= max_pages)
        next_btn.callback = self.next_page
        self.add_item(next_btn)

        start = self.page * self.per_page
        current_slice = self.items[start:start + self.per_page]

        if current_slice:
            options = [
                discord.SelectOption(
                    label=i["role_name"], 
                    description=f"Цена: {i['price']:,} Колов", 
                    value=str(i["sale_id"])
                ) for i in current_slice
            ]
            select = discord.ui.Select(placeholder="Купить лот с этой страницы...", options=options)
            select.callback = self.buy_callback
            self.add_item(select)

    async def prev_page(self, interaction: discord.Interaction):
        self.page -= 1
        self.update_components()
        await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

    async def next_page(self, interaction: discord.Interaction):
        self.page += 1
        self.update_components()
        await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

    async def buy_callback(self, interaction: discord.Interaction):
        select_component = [c for c in self.children if isinstance(c, discord.ui.Select)][0]
        sale_id = int(select_component.values[0])
        
        item = auction_collection.find_one({"sale_id": sale_id})
        if not item: 
            return await interaction.response.send_message("❌ Этот лот уже продан!", ephemeral=True)
            
        if interaction.user.id == item["seller_id"]: 
            return await interaction.response.send_message("❌ Нельзя покупать собственные лоты!", ephemeral=True)
            
        buyer_coins, _, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if buyer_coins < item["price"]: 
            return await interaction.response.send_message("❌ У вас недостаточно средств!", ephemeral=True)
            
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -item["price"]}})
        users_collection.update_one({"_id": item["seller_id"]}, {"$inc": {"coins": item["price"]}})
        custom_roles_collection.update_one({"role_id": item["role_id"]}, {"$set": {"user_id": interaction.user.id}})
        auction_collection.delete_one({"sale_id": sale_id})
        
        role = interaction.guild.get_role(item["role_id"])
        if role: 
            try: 
                await interaction.user.add_roles(role)
            except: 
                pass
                
        await interaction.response.send_message(f"🎉 Вы успешно приобрели роль **{item['role_name']}** за **{item['price']:,} Колов**!", ephemeral=True)
        
        self.items = list(auction_collection.find())
        max_pages = max(0, (len(self.items) - 1) // self.per_page)
        if self.page > max_pages:
            self.page = max_pages
        self.update_components()
        await interaction.message.edit(embed=self.get_current_embed(), view=self)

    def get_current_embed(self):
        embed = discord.Embed(
            title="────── ┌ 🏛️ ГЛОБАЛЬНЫЙ АУКЦИОН РОЛЕЙ ┐ ──────", 
            description="Торговая площадка уникальных кастомных ролей Айнкрада.", 
            color=0xFFD700
        )
        start = self.page * self.per_page
        current_slice = self.items[start:start + self.per_page]
        
        if not current_slice:
            embed.description = "На этой странице пусто. Лоты закончились."
            
        for item in current_slice:
            embed.add_field(
                name=f"📦 Лот: {item['role_name']}", 
                value=f"```fix\nЦена: {item['price']:,} Колов\n```\nПродавец: <@{item['seller_id']}>", 
                inline=False
            )
            
        max_pages = max(1, (len(self.items) + self.per_page - 1) // self.per_page)
        embed.set_footer(text=f"Страница {self.page + 1} из {max_pages}")
        return embed

class AuctionMainView(discord.ui.View):
    def __init__(self): 
        super().__init__(timeout=300)

    @discord.ui.button(label="Посмотреть лоты", style=discord.ButtonStyle.blurple, emoji="🛒")
    async def browse(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = list(auction_collection.find())
        if not items: 
            return await interaction.response.send_message("📦 На текущий момент торговая площадка пуста.", ephemeral=True)
        view = AuctionPagingView(items)
        await interaction.response.send_message(embed=view.get_current_embed(), view=view, ephemeral=True)

    @discord.ui.button(label="Выставить роль на продажу", style=discord.ButtonStyle.green, emoji="🏷️")
    async def sell(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = list(custom_roles_collection.find({"user_id": interaction.user.id}))
        user_roles = [interaction.guild.get_role(r["role_id"]) for r in rows if interaction.guild.get_role(r["role_id"])]
        if not user_roles: 
            return await interaction.response.send_message("❌ У вас нет кастомных ролей для продажи!", ephemeral=True)
        await interaction.response.send_message("Выберите роль для выставления на аукцион:", view=SellRoleSelect(user_roles), ephemeral=True)

@bot.tree.command(name="auction", description="Открыть глобальный аукцион кастомных ролей")
@check_maintenance()
async def auction(interaction: discord.Interaction):
    embed = discord.Embed(
        title="────── ┌ 🏛️ ЦЕНТРАЛЬНЫЙ АУКЦИОН АЙНКРАДА ┐ ──────", 
        description="Покупайте уникальные роли других игроков или выставляйте на продажу свои собственные творения.", 
        color=0xFFD700
    )
    await interaction.response.send_message(embed=embed, view=AuctionMainView())


# --- СИСТЕМА НОВОСТЕЙ И СТРИМОВ ---

class NewsModal(discord.ui.Modal, title="Создание системной новости"):
    news_title = discord.ui.TextInput(label="Заголовок новости", placeholder="СИСТЕМНОЕ ОБНОВЛЕНИЕ", max_length=100)
    news_text = discord.ui.TextInput(label="Основной текст", style=discord.TextStyle.paragraph, placeholder="Добавил пару фишек...\n\n| 🔻 Роли...")
    news_gif = discord.ui.TextInput(label="Ссылка на GIF/Картинку", required=False, placeholder="https://media.tenor.com/...")
    news_color = discord.ui.TextInput(label="HEX Цвет (без #)", default="00BFFF", max_length=6)

    async def on_submit(self, interaction: discord.Interaction):
        try: color_val = int(self.news_color.value, 16)
        except: color_val = 0x00BFFF

        embed = discord.Embed(
            title=f"────── ┌ 📢 {self.news_title.value.upper()} ┐ ──────", 
            description=f"\n{self.news_text.value}\n", 
            color=color_val
        )
        if self.news_gif.value:
            embed.set_image(url=self.news_gif.value)
        
        await interaction.channel.send(content="@everyone", embed=embed)
        await interaction.response.send_message("✅ Новость успешно опубликована!", ephemeral=True)

@bot.tree.command(name="setup_news", description="[АДМИН] Красиво опубликовать системную новость в канал")
async def setup_news(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    await interaction.response.send_modal(NewsModal())

@bot.tree.command(name="stream", description="[АДМИН] Объявить о начале стрима на Twitch")
async def stream(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    
    embed = discord.Embed(
        title="────── ┌ 🔴 ПРЯМОЙ ЭФИР НА TWITCH ┐ ──────",
        description=(
            "**Йоу, стрим запущен! Залетайте!**\n\n"
            "Стример - **@Koro'L hikka** и Brother - **@Тимур**.\n"
            "Заходите, будет очень весело и атмосферно!\n\n"
            "🔗 **[КЛИКАЙ СЮДА И ЗАЛЕТАЙ НА СТРИМ](https://www.twitch.tv/treihaolvl31)**"
        ),
        color=0x9146FF
    )
    embed.set_image(url="https://media.tenor.com/PZcK_wQfGgcAAAAC/sao-kirito.gif") 
    
    await interaction.channel.send(content="@everyone Ребята, переходите на стрим!", embed=embed)
    await interaction.response.send_message("✅ Анонс стрима отправлен!", ephemeral=True)


# --- ПОЛНЫЙ НАБОР АДМИНСКИХ ЧИТ-КОМАНД ---

@bot.tree.command(name="setlevel", description="[АДМИН] Установить точный этаж игроку")
async def setlevel(interaction: discord.Interaction, member: discord.Member, level: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    get_or_create_user(member.id)
    users_collection.update_one({"_id": member.id}, {"$set": {"level": level, "xp": 0}})
    await check_level_roles(member, level)
    await interaction.response.send_message(f"✅ Установлен {level} этаж для пользователя {member.mention}.", ephemeral=True)

@bot.tree.command(name="setxp", description="[АДМИН] Установить точное количество опыта (XP)")
async def setxp(interaction: discord.Interaction, member: discord.Member, xp: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    get_or_create_user(member.id)
    users_collection.update_one({"_id": member.id}, {"$set": {"xp": xp}})
    await interaction.response.send_message(f"✅ Установлено `{xp:,} XP` для пользователя {member.mention}.", ephemeral=True)

@bot.tree.command(name="givexp", description="[АДМИН] Выдать опыт (XP) игроку")
async def givexp(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    get_or_create_user(member.id)
    await add_xp(interaction, member.id, amount)
    await interaction.response.send_message(f"✅ Выдано `{amount:,} XP` пользователю {member.mention}.", ephemeral=True)

@bot.tree.command(name="setcoins", description="[АДМИН] Установить точный баланс Колов игроку")
async def setcoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    get_or_create_user(member.id)
    users_collection.update_one({"_id": member.id}, {"$set": {"coins": amount}})
    await interaction.response.send_message(f"✅ Баланс игрока {member.mention} изменен ровно на `{amount:,}` Колов.", ephemeral=True)

@bot.tree.command(name="givecoins", description="[АДМИН] Выдать Колы игроку")
async def givecoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    get_or_create_user(member.id)
    users_collection.update_one({"_id": member.id}, {"$inc": {"coins": amount}})
    await interaction.response.send_message(f"✅ Выдано `{amount:,}` Колов пользователю {member.mention}.", ephemeral=True)

@bot.tree.command(name="takecoins", description="[АДМИН] Забрать Колы у игрока")
async def takecoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    get_or_create_user(member.id)
    users_collection.update_one({"_id": member.id}, {"$inc": {"coins": -amount}})
    await interaction.response.send_message(f"🔻 Списано `{amount:,}` Колов у пользователя {member.mention}.", ephemeral=True)

@bot.tree.command(name="setstreak", description="[АДМИН] Установить текущий стрик ежедневных входов")
async def setstreak(interaction: discord.Interaction, member: discord.Member, days: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    get_or_create_user(member.id)
    users_collection.update_one({"_id": member.id}, {"$set": {"streak": days}})
    await interaction.response.send_message(f"✅ Установлен стрик в `{days} дн.` для пользователя {member.mention}.", ephemeral=True)

@bot.tree.command(name="resetcd", description="[АДМИН] Сбросить все кулдауны команд игроку")
async def resetcd(interaction: discord.Interaction, member: discord.Member = None):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    target = member or interaction.user
    users_collection.update_one({"_id": target.id}, {"$set": {"last_daily": 0.0, "last_work": 0.0, "last_crime": 0.0, "last_rob": 0.0}})
    await interaction.response.send_message(f"⚡ Все игровые кулдауны для {target.mention} успешно сброшены!", ephemeral=True)

@bot.tree.command(name="resetuser", description="[АДМИН] Полностью сбросить профиль конкретного игрока")
async def resetuser(interaction: discord.Interaction, member: discord.Member):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    users_collection.delete_one({"_id": member.id})
    get_or_create_user(member.id)
    await interaction.response.send_message(f"☢️ Профиль игрока {member.mention} полностью сброшен к заводским настройкам!", ephemeral=True)

@bot.tree.command(name="resetdb", description="[АДМИН] Полная глобальная очистка всей базы данных сервера")
async def resetdb(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Недостаточно прав!", ephemeral=True)
    users_collection.delete_many({})
    guilds_collection.delete_many({})
    custom_roles_collection.delete_many({})
    titles_collection.delete_many({})
    auction_collection.delete_many({})
    guild_requests_collection.delete_many({})
    await interaction.response.send_message("☢️ Внимание: Облачная база данных полностью очищена!", ephemeral=True)


# --- СИСТЕМА ИДЕЙ И ПРЕДЛОЖЕНИЙ С ПРЕМОДЕРАЦИЕЙ ---

PUBLIC_IDEA_CHANNEL_ID = 1532592402223730739  
ADMIN_IDEA_CHANNEL_ID = 1532719050319466610   

class AdminIdeaView(discord.ui.View):
    def __init__(self, author: discord.Member, idea_text: str):
        super().__init__(timeout=None)
        self.author = author
        self.idea_text = idea_text

    @discord.ui.button(label="Одобрить", style=discord.ButtonStyle.green, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
        public_channel = interaction.guild.get_channel(PUBLIC_IDEA_CHANNEL_ID)
        if not public_channel:
            return await interaction.response.send_message("❌ Ошибка: Публичный канал идей не найден.", ephemeral=True)

        public_embed = discord.Embed(
            title="────── ┌ ✨ ОДОБРЕННАЯ ИДЕЯ ┐ ──────",
            description=f"**Идея:**\n{self.idea_text}\n\n**Прислал:**\n{self.author.mention}", 
            color=0x2B2D31
        )
        public_embed.set_thumbnail(url=self.author.display_avatar.url)
        
        msg = await public_channel.send(embed=public_embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
        
        admin_embed = interaction.message.embeds[0]
        admin_embed.color = 0x2ECC71
        admin_embed.title = "✅ ИДЕЯ ОДОБРЕНА И ОПУБЛИКОВАНА"
        admin_embed.add_field(name="Проверил модератор:", value=interaction.user.mention, inline=False)
        
        for child in self.children: 
            child.disabled = True
        await interaction.response.edit_message(embed=admin_embed, view=self)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
        admin_embed = interaction.message.embeds[0]
        admin_embed.color = 0xE74C3C
        admin_embed.title = "❌ ИДЕЯ ОТКЛОНЕНА"
        admin_embed.add_field(name="Отклонил модератор:", value=interaction.user.mention, inline=False)
        
        for child in self.children: 
            child.disabled = True
        await interaction.response.edit_message(embed=admin_embed, view=self)

class IdeaModal(discord.ui.Modal, title="Предложить идею для сервера"):
    idea_text = discord.ui.TextInput(label="Суть вашей идеи", style=discord.TextStyle.paragraph, placeholder="Я предлагаю...", max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        admin_channel = interaction.guild.get_channel(ADMIN_IDEA_CHANNEL_ID)
        if not admin_channel:
            return await interaction.response.send_message("❌ Ошибка: Канал проверки идей не найден.", ephemeral=True)

        embed = discord.Embed(
            title="────── ┌ ⏳ НОВАЯ ИДЕЯ ┐ ──────", 
            description=f"**От пользователя:** {interaction.user.mention}\n\n**Текст:**\n{self.idea_text.value}", 
            color=0xF1C40F
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        view = AdminIdeaView(author=interaction.user, idea_text=self.idea_text.value)
        await admin_channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ Ваша идея успешно отправлена модераторам на премодерацию!", ephemeral=True)

@bot.tree.command(name="idea", description="Предложить новую идею для развития сервера Айнкрад")
@check_maintenance()
async def idea(interaction: discord.Interaction):
    await interaction.response.send_modal(IdeaModal())

# --- СИСТЕМА ВЕРИФИКАЦИИ И DOCS-ЛОГИРОВАНИЯ ---

DOCS_CHANNEL_ID = 1533682208487903483

@bot.tree.command(name="setup_verify", description="[АДМИН] Установить сообщение с инструкцией по верификации")
async def setup_verify(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ У вас нет прав для этой команды.", ephemeral=True)
    
    embed = discord.Embed(
        title="────── ┌ 🛡️ ИДЕНТИФИКАЦИЯ ИГРОКОВ ┐ ──────", 
        description=(
            "Добро пожаловать в Айнкрад!\n\n"
            "Чтобы получить доступ к этажам сервера и начать игру, вам необходимо пройти быструю голосовую проверку.\n\n"
            "**Как получить доступ?**\n"
            "1️⃣ Зайдите в голосовой канал **🔊 Ожидание верификации**.\n"
            "2️⃣ Дождитесь свободного Саппорта или Модератора.\n"
            "3️⃣ Вас перекинут в закрытый канал для короткой проверки.\n"
            "4️⃣ После подтверждения вам выдадут гендерную роль и полный доступ к серверу."
        ), 
        color=0x3498DB
    )
    embed.set_image(url="https://i.pinimg.com/originals/44/ee/12/44ee12a9754f7a26f8eb7ba48de30c6a.gif")
    embed.set_footer(text="Aincrad Security System • Ручная проверка Кардинала")
    
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Инструкция по голосовой верификации успешно установлена.", ephemeral=True)

@bot.tree.command(name="verify", description="[САППОРТ] Пройти проверку пользователя и выдать гендерную роль")
@app_commands.choices(gender=[
    app_commands.Choice(name="Мужчина ♂️", value="♂️"),
    app_commands.Choice(name="Женщина ♀️", value="♀️")
])
async def verify_user(interaction: discord.Interaction, member: discord.Member, gender: str):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ У вас нет прав саппорта или модератора для верификации игроков!", ephemeral=True)
        
    role = discord.utils.get(interaction.guild.roles, name=gender)
    if not role:
        return await interaction.response.send_message(f"❌ Системная ошибка: Роль «{gender}» не найдена на сервере.", ephemeral=True)
        
    try:
        await member.add_roles(role)
        
        unverified_role = discord.utils.get(interaction.guild.roles, name="unverify")
        if unverified_role and unverified_role in member.roles:
            await member.remove_roles(unverified_role)
            
        embed = discord.Embed(
            title="────── ┌ ✅ ВЕРИФИКАЦИЯ УСПЕШНА ┐ ──────", 
            description=f"Игрок {member.mention} прошел голосовую проверку и получил статус **{gender}**.\nРоль выдал: {interaction.user.mention}", 
            color=0x2ECC71
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

        docs_channel = interaction.guild.get_channel(DOCS_CHANNEL_ID)
        if docs_channel:
            docs_embed = discord.Embed(
                title="────── ┌ 📁 DOCС: ИДЕНТИФИКАЦИЯ ┐ ──────", 
                color=0x2B2D31
            )
            docs_embed.set_thumbnail(url=member.display_avatar.url)
            docs_embed.add_field(name="Пользователь", value=f"{member.mention}\n`{member.id}`", inline=True)
            docs_embed.add_field(name="Выданный статус", value=f"**{gender}**", inline=True)
            docs_embed.add_field(name="Саппорт / Модер", value=interaction.user.mention, inline=False)
            
            created_at = discord.utils.format_dt(member.created_at, style='F')
            docs_embed.add_field(name="Аккаунт создан", value=created_at, inline=False)
            
            sensor_link = f"https://discord-sensor.com/members/{member.id}"
            docs_embed.add_field(name="Discord Sensor", value=f"🔗 [Открыть профиль]({sensor_link})", inline=False)
            
            await docs_channel.send(embed=docs_embed)
            
    except Exception as e:
        await interaction.response.send_message(f"❌ Ошибка доступа: бот не может выдать роль. Подробности: {e}", ephemeral=True)

# --- КОМАНДА SETUP RANKS ---

@bot.tree.command(name="setup_ranks", description="[АДМИН] Установить информационное сообщение о системе рангов и этажей")
async def setup_ranks(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): 
        return await interaction.response.send_message("❌ У вас нет прав для этой команды.", ephemeral=True)
    
    embed = discord.Embed(
        title="────── ┌ ⚡ СИСТЕМА ПРОХОЖДЕНИЯ БАШНИ ┐ ──────", 
        description=(
            "«Добро пожаловать в мир, где каждый шаг наверх приносит награду. Общайтесь в чатах и сидите в войсах, чтобы преодолевать этажи и открывать новые зоны». 🛡️\n\n"
            "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
            "🏆 **ИЕРАРХИЯ ЭТАЖЕЙ И НАГРАДЫ**\n"
            "👤 `Обычный житель` (Без этажа)\n"
            "⚪ `Начало Легенды` (LVL 2) — 60-80 Колов / день\n"
            "🟢 `Путешественник` (LVL 5) — 70-100 Колов / день\n"
            "🔵 `Разведчик Рубежа` (LVL 10) — 90-130 Колов / день\n"
            "💠 `Опытный Мечник` (LVL 15) — 110-150 Колов / день\n"
            "🟣 `Передовой Воин` (LVL 20) — 140-180 Колов / день\n"
            "🟠 `Закаленный Огнем` (LVL 30) — 170-220 Колов / день\n"
            "🔴 `Мастер клинка` (LVL 40) — 210-270 Колов / день\n"
            "🩷 `Герой Айнкрада` (LVL 50) — 260-340 Колов / день\n"
            "⚫ `Грандмастер` (LVL 65) — 350-450 Колов / день\n"
            "👑 `Вершитель Судеб` (LVL 80) — 480-650 Колов / день\n"
            "⚔️ `Beater` (LVL 100) — 800-1000 Колов / день\n\n"
            "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
            "💎 **ЭЛИТНЫЙ СТАТУС БУСТЕРА**\n"
            "🌟 `Бустер сервера` — **500 Колов / день!**\n\n"
            "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
            "⚙️ **ПОЛЕЗНЫЕ КОМАНДЫ КАРДИНАЛА**\n"
            "📊 `/profile` — ваш текущий этаж, капитал и прогресс.\n"
            "🏆 `/leaderboard` — глобальная таблица топ-10 игроков.\n"
            "🎁 `/daily` — забрать системную награду в виде **Колов и Опыта (XP)**. Размер бонуса растет от вашего этажа и серии ежедневных входов!"
        ),
        color=0xE02653
    )
    embed.set_image(url="https://i.pinimg.com/originals/8e/ec/f2/8eecf2daed72882f84ff0cdd8eaa9333.gif")
    embed.set_footer(text="Aincrad Leveling System • Разработано системой Кардинал")
    
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Информационное сообщение о рангах успешно установлено в канал.", ephemeral=True)

keep_alive()
bot.run(os.getenv("TOKEN"))
