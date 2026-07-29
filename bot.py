import sqlite3
import random
import time
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Оптимизация памяти для хостинга
bot = commands.Bot(
    command_prefix="!", 
    intents=intents,
    chunk_guilds_at_startup=False,
    max_messages=10
)

# Глобальный флаг режима техобслуживания (бета-тест)
MAINTENANCE_MODE = False

def init_db():
    db = sqlite3.connect("database.db")
    cursor = db.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        coins INTEGER DEFAULT 100,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        last_daily REAL DEFAULT 0,
        last_work REAL DEFAULT 0,
        last_crime REAL DEFAULT 0,
        last_rob REAL DEFAULT 0,
        streak INTEGER DEFAULT 0,
        guild_id TEXT DEFAULT NULL,
        special_title TEXT DEFAULT 'Отсутствует'
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS custom_roles (
        role_id INTEGER PRIMARY KEY,
        user_id INTEGER,
        role_name TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS auction_roles (
        sale_id INTEGER PRIMARY KEY AUTOINCREMENT,
        role_id INTEGER,
        seller_id INTEGER,
        price INTEGER,
        role_name TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_titles (
        user_id INTEGER,
        title_name TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS guilds (
        guild_name TEXT PRIMARY KEY,
        leader_id INTEGER,
        bank INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        entry_fee INTEGER DEFAULT 0,
        is_private INTEGER DEFAULT 0,
        last_daily REAL DEFAULT 0
    )
    """)
    db.commit()
    db.close()

init_db()

def execute_db(query, params=(), fetchone=False, fetchall=False, commit=False):
    db = sqlite3.connect("database.db", timeout=10.0)
    cursor = db.cursor()
    cursor.execute(query, params)
    res = None
    if fetchone:
        res = cursor.fetchone()
    elif fetchall:
        res = cursor.fetchall()
    if commit:
        db.commit()
    db.close()
    return res

for col_sql in [
    "ALTER TABLE users ADD COLUMN last_daily REAL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN last_work REAL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN last_crime REAL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN last_rob REAL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN streak INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN guild_id TEXT DEFAULT NULL",
    "ALTER TABLE users ADD COLUMN special_title TEXT DEFAULT 'Отсутствует'",
    "ALTER TABLE guilds ADD COLUMN entry_fee INTEGER DEFAULT 0",
    "ALTER TABLE guilds ADD COLUMN is_private INTEGER DEFAULT 0",
    "ALTER TABLE guilds ADD COLUMN last_daily REAL DEFAULT 0"
]:
    try:
        execute_db(col_sql, commit=True)
    except sqlite3.OperationalError:
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

def get_or_create_user(user_id):
    row = execute_db("SELECT coins, xp, level, last_daily, last_work, last_crime, last_rob, streak, guild_id, special_title FROM users WHERE user_id = ?", (user_id,), fetchone=True)
    if row is None:
        execute_db("INSERT INTO users (user_id) VALUES (?)", (user_id,), commit=True)
        return 100, 0, 1, 0, 0, 0, 0, 0, None, 'Отсутствует'
    return row

def is_admin_or_mod(member: discord.Member):
    if member.guild_permissions.administrator:
        return True
    role_names = [r.name.lower() for r in member.roles]
    if "модератор" in role_names or "администратор" in role_names:
        return True
    return False

@bot.tree.interaction_check
async def global_interaction_check(interaction: discord.Interaction) -> bool:
    global MAINTENANCE_MODE
    if MAINTENANCE_MODE and not is_admin_or_mod(interaction.user):
        await interaction.response.send_message("🛠️ **Технические работы:** Бот находится в режиме бета-разработки и временно недоступен. Попробуйте позже!", ephemeral=True)
        return False
    return True

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
            await member.send(f"🎉 Поздравляем! Ты достиг **{current_level} этажа** в Айнкраде и получил статус **{highest_role_name}**!")
        except Exception:
            pass

async def add_xp(interaction, user_id, amount):
    coins, xp, level, _, _, _, _, _, _, _ = get_or_create_user(user_id)
    xp += amount
    next_level_xp = int(35 * (level ** 1.85) + 80 * level + 40)
    leveled_up = False
    
    while xp >= next_level_xp:
        level += 1
        xp -= next_level_xp
        next_level_xp = int(35 * (level ** 1.85) + 80 * level + 40)
        leveled_up = True

    execute_db("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (xp, level, user_id), commit=True)

    if leveled_up:
        member = getattr(interaction, 'user', None)
        if member:
            await check_level_roles(member, level)
            lvl_embed = discord.Embed(title="⚡ ПОВЫШЕНИЕ ЭТАЖА", description=f"Вы успешно поднялись на **{level} этаж** башни Айнкрад!", color=0x00BFFF)
            lvl_embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
            channel = getattr(interaction, 'channel', None)
            if channel:
                await channel.send(content=f"Внимание, Система: {member.mention} прорывается наверх!", embed=lvl_embed)

@bot.event
async def on_ready():
  await bot.tree.sync()
  print(f"Бот {bot.user} запущен и полностью готов к работе в Айнкраде!")

@bot.event
async def on_message(message):
  if message.author.bot or not message.guild:
    return
  global MAINTENANCE_MODE
  if MAINTENANCE_MODE and not is_admin_or_mod(message.author):
      return
  await add_xp(message, message.author.id, random.randint(2, 5))
  await bot.process_commands(message)

# --- АДМИНСКАЯ КОМАНДА ТЕХОБСЛУЖИВАНИЯ ---

@bot.tree.command(name="maintenance", description="[АДМИН] Включить/выключить режим техобслуживания")
@app_commands.default_permissions(administrator=True)
async def maintenance(interaction: discord.Interaction):
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    status = "🔴 ВКЛЮЧЕН (доступ закрыт для игроков)" if MAINTENANCE_MODE else "🟢 ВЫКЛЮЧЕН (бот работает)"
    embed = discord.Embed(title="🛠️ РЕЖИМ ТЕХОБСЛУЖИВАНИЯ", description=f"Статус изменен: **{status}**", color=0xE74C3C if MAINTENANCE_MODE else 0x2ECC71)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- БАЗОВЫЕ КОМАНДЫ ---

@bot.tree.command(name="balance", description="Посмотреть баланс Колов")
async def balance(interaction: discord.Interaction, member: discord.Member = None):
  target = member or interaction.user
  coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(target.id)
  embed = discord.Embed(title="[ 🌐 БАНК АЙНКРАДА ]", color=0x00BFFF)
  embed.set_thumbnail(url=target.display_avatar.url)
  embed.add_field(name="Владелец счета", value=f"{target.mention}", inline=True)
  embed.add_field(name="Баланс", value=f"**{coins:,}** Колов <:col:1530575386457542817>", inline=True)
  await interaction.response.send_message(embed=embed)

@bot.tree.command(name="profile", description="Посмотреть игровой профиль")
async def profile(interaction: discord.Interaction, member: discord.Member = None):
  target = member or interaction.user
  coins, xp, level, _, _, _, _, streak, guild_id, special_title = get_or_create_user(target.id)
  next_level_xp = int(35 * (level ** 1.85) + 80 * level + 40)
  progress = int((xp / next_level_xp) * 10) if next_level_xp > 0 else 0
  bar = "🟩" * progress + "⬛" * (10 - progress)

  embed = discord.Embed(title=f"🛡️ ИГРОВОЙ ПРОФИЛЬ: {target.display_name}", color=0xFFD700)
  embed.set_thumbnail(url=target.display_avatar.url)
  embed.add_field(name="⚔️ Этаж", value=f"**{level}**", inline=True)
  embed.add_field(name="🪙 Колы", value=f"**{coins:,}**", inline=True)
  embed.add_field(name="🔥 Стрик", value=f"**{streak} дн.**", inline=True)
  embed.add_field(name="🏰 Гильдия", value=f"**{guild_id if guild_id else 'Нет'}**", inline=True)
  embed.add_field(name="✨ Титул", value=f"**{special_title}**", inline=False)
  embed.add_field(name="📊 Прогресс опыта", value=f"{xp} / {next_level_xp} XP\n{bar}", inline=False)
  await interaction.response.send_message(embed=embed)

@bot.tree.command(name="pay", description="Перевести Колы игроку")
async def pay(interaction: discord.Interaction, member: discord.Member, amount: int):
  if amount <= 0 or member.id == interaction.user.id:
    return await interaction.response.send_message("❌ Некорректная операция!", ephemeral=True)
  
  sender_coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
  if sender_coins < amount:
    return await interaction.response.send_message("❌ Недостаточно средств на счете!", ephemeral=True)

  get_or_create_user(member.id)
  execute_db("UPDATE users SET coins = coins - ? WHERE user_id = ?", (amount, interaction.user.id), commit=True)
  execute_db("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, member.id), commit=True)

  embed = discord.Embed(title="[ 💸 МЕЖБАНКОВСКИЙ ПЕРЕВОД ]", color=0x00FF00)
  embed.description = f"Успешно переведено **{amount:,}** <:col:1530575386457542817> для {member.mention}!"
  await interaction.response.send_message(embed=embed)

@bot.tree.command(name="daily", description="Получить ежедневную награду со стриком")
async def daily(interaction: discord.Interaction):
  user_id = interaction.user.id
  current_time = time.time()
  coins, _, level, last_daily, _, _, _, streak, _, _ = get_or_create_user(user_id)

  if current_time - last_daily < 86400:
      left = int(86400 - (current_time - last_daily))
      return await interaction.response.send_message(f"⏳ Награда будет доступна через {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)

  if last_daily > 0 and current_time - last_daily > 172800:
      streak = 0

  streak += 1
  base_coins = 40 + (level * 8)
  base_xp = 15 + (level * 3)
  
  multiplier = 1.0 + (streak * 0.15)
  reward_coins = int(base_coins * multiplier)
  reward_xp = int(base_xp * multiplier)

  execute_db("UPDATE users SET coins = coins + ?, streak = ?, last_daily = ? WHERE user_id = ?", (reward_coins, streak, current_time, user_id), commit=True)
  await add_xp(interaction, user_id, reward_xp)

  embed = discord.Embed(title="[ 🎁 ЕЖЕДНЕВНАЯ НАГРАДА И СТРИК ]", color=0x00FF00)
  embed.description = f"🔥 Стрик входов: **{streak} дн.**\n🪙 Награда: **{reward_coins:,}** Колов\n⚡ Опыт: **+{reward_xp} XP**"
  await interaction.response.send_message(embed=embed)

@bot.tree.command(name="work", description="Поработать на этажах Айнкрада")
async def work(interaction: discord.Interaction):
  user_id = interaction.user.id
  current_time = time.time()
  _, _, level, _, last_work, _, _, _, _, _ = get_or_create_user(user_id)

  if current_time - last_work < 7200:
      left = int(7200 - (current_time - last_work))
      return await interaction.response.send_message(f"⏳ Персонаж устал. Отдых еще {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)

  job_desc = "Зачистка подземелья" if level > 20 else "Сбор ресурсов в стартовой зоне"
  earned = random.randint(40, 120) + (level * 2)
  
  execute_db("UPDATE users SET coins = coins + ?, last_work = ? WHERE user_id = ?", (earned, current_time, user_id), commit=True)
  await add_xp(interaction, user_id, random.randint(10, 20))

  embed = discord.Embed(title="[ 🛠️ РАБОТА В АЙНКРАДЕ ]", color=0x3498DB)
  embed.description = f"Задание *«{job_desc}»* (Этаж {level}) принесло **{earned}** Колов <:col:1530575386457542817>!"
  await interaction.response.send_message(embed=embed)

@bot.tree.command(name="crime", description="Совершить рискованную авантюру")
async def crime(interaction: discord.Interaction):
  user_id = interaction.user.id
  current_time = time.time()
  coins, _, level, _, _, last_crime, _, _, _, _ = get_or_create_user(user_id)

  if current_time - last_crime < 14400:
      left = int(14400 - (current_time - last_crime))
      return await interaction.response.send_message(f"⏳ Слишком опасно. Ждите ещё {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)

  success = random.choice([True, False])
  if success:
      reward = random.randint(50, 130) + (level * 2)
      execute_db("UPDATE users SET coins = coins + ?, last_crime = ? WHERE user_id = ?", (reward, current_time, user_id), commit=True)
      await add_xp(interaction, user_id, 15)
      embed = discord.Embed(title="[ 🥷 КРИМИНАЛЬНЫЙ УСПЕХ ]", color=0x2ECC71)
      embed.description = f"Куш сорван! Получено **{reward}** Колов <:col:1530575386457542817>!"
  else:
      fine = random.randint(30, 70)
      execute_db("UPDATE users SET coins = MAX(0, coins - ?), last_crime = ? WHERE user_id = ?", (fine, current_time, user_id), commit=True)
      embed = discord.Embed(title="[ ❌ ПОЙМАН СТРАЖЕЙ ]", color=0xE74C3C)
      embed.description = f"Вас поймали! Штраф: **{fine}** Колов <:col:1530575386457542817>."
  await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rob", description="Попытаться ограбить другого игрока")
async def rob(interaction: discord.Interaction, member: discord.Member):
    attacker = interaction.user
    if member.id == attacker.id:
        return await interaction.response.send_message("❌ Нельзя грабить себя!", ephemeral=True)
    if member.bot:
        return await interaction.response.send_message("❌ Ботов грабить бесполезно.", ephemeral=True)

    target_role_names = [r.name.lower() for r in member.roles]
    if "неприкасаемый" in target_role_names or "модератор" in target_role_names:
        return await interaction.response.send_message(f"🛡️ Игрок {member.mention} под защитой элитного статуса!", ephemeral=True)

    current_time = time.time()
    att_coins, _, _, _, _, _, att_last_rob, _, _, _ = get_or_create_user(attacker.id)
    if current_time - att_last_rob < 10800:
        left = int(10800 - (current_time - att_last_rob))
        return await interaction.response.send_message(f"⏳ Следующее ограбление через {left // 3600}ч {(left % 3600) // 60}м!", ephemeral=True)

    target_coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(member.id)
    if target_coins < 200:
        return await interaction.response.send_message(f"❌ У игрока недостаточно средств для грабежа.", ephemeral=True)

    execute_db("UPDATE users SET last_rob = ? WHERE user_id = ?", (current_time, attacker.id), commit=True)

    if random.randint(1, 100) <= 40:
        stolen = int(target_coins * random.uniform(0.10, 0.20))
        execute_db("UPDATE users SET coins = coins + ? WHERE user_id = ?", (stolen, attacker.id), commit=True)
        execute_db("UPDATE users SET coins = coins - ? WHERE user_id = ?", (stolen, member.id), commit=True)
        await add_xp(interaction, attacker.id, 20)
        embed = discord.Embed(title="[ 🥷 УСПЕШНОЕ ОГРАБЛЕНИЕ ]", color=0x2ECC71)
        embed.description = f"💥 Вы вытащили **{stolen}** Колов у {member.mention}!"
        embed.set_image(url="https://i.pinimg.com/originals/58/23/81/582381e4e65d4f6a027116695445d649.gif")
        await interaction.response.send_message(embed=embed)
    else:
        fine = random.randint(40, 90)
        execute_db("UPDATE users SET coins = MAX(0, coins - ?) WHERE user_id = ?", (fine, attacker.id), commit=True)
        embed = discord.Embed(title="[ ❌ ПРОВАЛ ОГРАБЛЕНИЯ ]", color=0xE74C3C)
        embed.description = f"🚨 Стража поймала вас! Штраф: **{fine}** Колов."
        embed.set_image(url="https://i.pinimg.com/originals/1d/85/80/1d8580859a663c8c58d2aa9ff9dc87c8.gif")
        await interaction.response.send_message(embed=embed)

# --- АЗАРТНЫЕ ИГРЫ ---

@bot.tree.command(name="dice", description="Бросить кости против системы (Мин. ставка: 50)")
async def dice(interaction: discord.Interaction, amount: int):
  if not is_admin_or_mod(interaction.user) and amount < 50:
      return await interaction.response.send_message("❌ Минимальная ставка для азартных игр — **50 Колов**!", ephemeral=True)
      
  coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
  if coins < amount: return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)

  execute_db("UPDATE users SET coins = coins - ? WHERE user_id = ?", (amount, interaction.user.id), commit=True)

  embed_loading = discord.Embed(title="[ 🎲 КОСТИ АЙНКРАДА ]", description="Бросаем кости...", color=0x9B59B6)
  embed_loading.set_image(url="https://i.pinimg.com/originals/80/9f/ba/809fba531ccbb8e24010696ffa1503e2.gif")
  await interaction.response.send_message(embed=embed_loading)
  await asyncio.sleep(3.0)

  p_roll, b_roll = random.randint(1, 6), random.randint(1, 6)
  embed_res = discord.Embed(title="[ 🎲 КОСТИ АЙНКРАДА ]", color=0x9B59B6)
  embed_res.set_image(url="https://i.pinimg.com/originals/80/9f/ba/809fba531ccbb8e24010696ffa1503e2.gif")

  if p_roll > b_roll:
      execute_db("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount * 2, interaction.user.id), commit=True)
      embed_res.description = f"🎉 **Победа!** Вы выбросили `🎲 {p_roll}`, бот — `🤖 {b_roll}`.\nВыиграно: **{amount}** Колов!"
      embed_res.color = 0x2ECC71
  elif p_roll < b_roll:
      embed_res.description = f"💀 **Поражение.** Вы выбросили `🎲 {p_roll}`, бот — `🤖 {b_roll}`.\nПотеряно: **{amount}** Колов."
      embed_res.color = 0xE74C3C
  else:
      execute_db("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, interaction.user.id), commit=True)
      embed_res.description = f"🤝 **Ничья.** Обычные кости (`{p_roll}:{b_roll}`). Ставка возвращена."
      embed_res.color = 0xF1C40F
  
  await interaction.edit_original_response(embed=embed_res)
  await add_xp(interaction, interaction.user.id, random.randint(5, 10))

@bot.tree.command(name="coinflip", description="Орел и решка (Мин. ставка: 50)")
@app_commands.choices(choice=[app_commands.Choice(name="Орел", value="орел"), app_commands.Choice(name="Решка", value="решка")])
async def coinflip(interaction: discord.Interaction, choice: str, amount: int):
  if not is_admin_or_mod(interaction.user) and amount < 50:
      return await interaction.response.send_message("❌ Минимальная ставка для азартных игр — **50 Колов**!", ephemeral=True)
      
  coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
  if coins < amount: return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)

  execute_db("UPDATE users SET coins = coins - ? WHERE user_id = ?", (amount, interaction.user.id), commit=True)

  gif_url = "https://media1.tenor.com/m/9PALsSO_XpsAAAAC/misaka-mikoto.gif"
  embed_loading = discord.Embed(title="[ 🪙 ОРЕЛ И РЕШКА ]", description="Монетка подброшена в воздух...", color=0xF1C40F)
  embed_loading.set_image(url=gif_url)
  await interaction.response.send_message(embed=embed_loading)
  await asyncio.sleep(3.0)

  result = random.choice(["орел", "решка"])
  embed_res = discord.Embed(title="[ 🪙 ИТОГ ПОДБРОСА ]", color=0x2ECC71)
  embed_res.set_image(url=gif_url)
  
  if choice == result:
      execute_db("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount * 2, interaction.user.id), commit=True)
      embed_res.description = f"🎉 Выпал **{result.upper()}**! Вы угадали и выиграли **{amount:,}** Колов!"
  else:
      embed_res.description = f"❌ Выпал **{result.upper()}**. Увы, вы проиграли **{amount:,}** Колов."
      embed_res.color = 0xE74C3C
      
  await interaction.edit_original_response(embed=embed_res)
  await add_xp(interaction, interaction.user.id, random.randint(5, 15))

@bot.tree.command(name="roulette", description="Русская рулетка (Мин. ставка: 50)")
async def roulette(interaction: discord.Interaction, amount: int):
  if not is_admin_or_mod(interaction.user) and amount < 50:
      return await interaction.response.send_message("❌ Минимальная ставка для азартных игр — **50 Колов**!", ephemeral=True)
      
  coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
  if coins < amount: return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)

  execute_db("UPDATE users SET coins = coins - ? WHERE user_id = ?", (amount, interaction.user.id), commit=True)

  embed_loading = discord.Embed(title="[ 🎯 РУССКАЯ РУЛЕТКА ]", description="Барабан вращается...", color=0xE74C3C)
  embed_loading.set_image(url="https://i.pinimg.com/originals/ac/56/c5/ac56c5c7e6037a698e22c9a30a8dccda.gif")
  await interaction.response.send_message(embed=embed_loading)
  await asyncio.sleep(3.0)

  shot = random.choice([True, False, False, False, False, False])
  embed_res = discord.Embed(title="[ 🎯 РУССКАЯ РУЛЕТКА ]", color=0xE74C3C)
  embed_res.set_image(url="https://i.pinimg.com/originals/ac/56/c5/ac56c5c7e6037a698e22c9a30a8dccda.gif")
  if not shot:
      execute_db("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount * 2, interaction.user.id), commit=True)
      embed_res.description = f"💥 *ЩЕЛК!* Барабан пуст. Вам повезло, вы выиграли **{amount:,}** Колов!"
      embed_res.color = 0x2ECC71
  else:
      embed_res.description = f"💀 *БАХ!* Вы поймали пулю. Проиграно **{amount:,}** Колов."
      
  await interaction.edit_original_response(embed=embed_res)
  await add_xp(interaction, interaction.user.id, random.randint(10, 20))

# --- УНИКАЛЬНЫЕ РОЛИ И МОДАЛКИ ДЛЯ МАГАЗИНА ---

class EditRoleModal(discord.ui.Modal, title="Изменение кастомной роли"):
    role_name = discord.ui.TextInput(label="Новое название", max_length=50)
    role_color = discord.ui.TextInput(label="Новый HEX-цвет (без #)", placeholder="FF5733", max_length=6, min_length=6)

    def __init__(self, role: discord.Role, price: int):
        super().__init__()
        self.role = role
        self.price = price

    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.role_name.value.strip()
        exists = execute_db("SELECT role_id FROM custom_roles WHERE LOWER(role_name) = LOWER(?) AND role_id != ?", (new_name, self.role.id), fetchone=True)
        if exists or discord.utils.get(interaction.guild.roles, name=new_name):
            return await interaction.response.send_message("❌ Роль с таким названием уже существует на сервере!", ephemeral=True)

        try:
            color_int = int(self.role_color.value.strip(), 16)
        except ValueError:
            return await interaction.response.send_message("❌ Неверный формат HEX-цвета!", ephemeral=True)

        execute_db("UPDATE users SET coins = coins - ? WHERE user_id = ?", (self.price, interaction.user.id), commit=True)

        await self.role.edit(name=new_name, color=discord.Color(color_int))
        execute_db("UPDATE custom_roles SET role_name = ? WHERE role_id = ?", (new_name, self.role.id), commit=True)
        execute_db("UPDATE auction_roles SET role_name = ? WHERE role_id = ?", (new_name, self.role.id), commit=True)
        await interaction.response.send_message(f"✅ Роль успешно изменена на **{new_name}**!", ephemeral=True)

class EditRoleSelect(discord.ui.View):
    def __init__(self, roles_list, price):
        super().__init__(timeout=30)
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles_list]
        self.select = discord.ui.Select(placeholder="Выберите нужную роль...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
        self.price = price

    async def select_callback(self, interaction: discord.Interaction):
        role_id = int(self.select.values[0])
        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message("❌ Ошибка: Роль не найдена.", ephemeral=True)
        await interaction.response.send_modal(EditRoleModal(role, self.price))

@bot.tree.command(name="editrole", description="Изменить цвет и имя кастомной роли (3 000 Колов)")
async def editrole(interaction: discord.Interaction):
    coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    if coins < 3000:
        return await interaction.response.send_message("❌ Нужно минимум 3 000 Колов!", ephemeral=True)

    rows = execute_db("SELECT role_id FROM custom_roles WHERE user_id = ?", (interaction.user.id,), fetchall=True)
    if not rows:
        return await interaction.response.send_message("❌ У вас нет купленных кастомных ролей!", ephemeral=True)

    user_roles = [interaction.guild.get_role(r[0]) for r in rows if interaction.guild.get_role(r[0])]
    if not user_roles:
        return await interaction.response.send_message("❌ Ваши кастомные роли не найдены на сервере.", ephemeral=True)

    view = EditRoleSelect(user_roles, 3000)
    embed = discord.Embed(title="[ 🛠️ РЕДАКТИРОВАНИЕ РОЛИ ]", description="Выберите из списка ниже, какую именно роль вы хотите изменить:", color=0x3498DB)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class DeleteRoleSelect(discord.ui.View):
    def __init__(self, roles_list, price):
        super().__init__(timeout=30)
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles_list]
        self.select = discord.ui.Select(placeholder="Выберите роль для удаления...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)
        self.price = price

    async def select_callback(self, interaction: discord.Interaction):
        role_id = int(self.select.values[0])
        role = interaction.guild.get_role(role_id)
        
        execute_db("UPDATE users SET coins = coins - ? WHERE user_id = ?", (self.price, interaction.user.id), commit=True)
        execute_db("DELETE FROM custom_roles WHERE role_id = ?", (role_id,), commit=True)
        execute_db("DELETE FROM auction_roles WHERE role_id = ?", (role_id,), commit=True)

        if role:
            try:
                await role.delete(reason="Удалено пользователем за колы")
            except Exception:
                pass
                
        await interaction.response.send_message(f"🗑️ Кастомная роль успешно удалена! Списано **{self.price:,}** Колов.", ephemeral=True)

@bot.tree.command(name="deleterole", description="Удалить свою кастомную роль (5 000 Колов)")
async def deleterole(interaction: discord.Interaction):
    coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    if coins < 5000:
        return await interaction.response.send_message("❌ Для удаления роли нужно 5 000 Колов!", ephemeral=True)

    rows = execute_db("SELECT role_id FROM custom_roles WHERE user_id = ?", (interaction.user.id,), fetchall=True)
    if not rows:
        return await interaction.response.send_message("❌ У вас нет кастомных ролей для удаления!", ephemeral=True)

    user_roles = [interaction.guild.get_role(r[0]) for r in rows if interaction.guild.get_role(r[0])]
    if not user_roles:
        return await interaction.response.send_message("❌ Роли не найдены на сервере.", ephemeral=True)

    view = DeleteRoleSelect(user_roles, 5000)
    embed = discord.Embed(title="[ 🗑️ УДАЛЕНИЕ РОЛИ ]", description="Выберите роль, которую хотите полностью стереть с сервера:", color=0xE74C3C)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class CustomRoleModal(discord.ui.Modal, title="Создание уникальной кастомной роли"):
    role_name = discord.ui.TextInput(label="Название роли", placeholder="Темный Рыцарь", max_length=50)
    role_color = discord.ui.TextInput(label="HEX-цвет (без #)", placeholder="FF5733", max_length=6, min_length=6)

    def __init__(self, price: int):
        super().__init__()
        self.price = price

    async def on_submit(self, interaction: discord.Interaction):
        r_name = self.role_name.value.strip()

        exists = execute_db("SELECT role_id FROM custom_roles WHERE LOWER(role_name) = LOWER(?)", (r_name,), fetchone=True)
        if exists or discord.utils.get(interaction.guild.roles, name=r_name):
            return await interaction.response.send_message("❌ Ошибка: Роль с таким названием уже существует!", ephemeral=True)

        try:
            color_int = int(self.role_color.value.strip(), 16)
        except ValueError:
            return await interaction.response.send_message("❌ Неверный формат HEX-цвета!", ephemeral=True)

        execute_db("UPDATE users SET coins = coins - ? WHERE user_id = ?", (self.price, interaction.user.id), commit=True)
        
        try:
            new_role = await interaction.guild.create_role(name=r_name, color=discord.Color(color_int))
            await interaction.user.add_roles(new_role)
            execute_db("INSERT INTO custom_roles (role_id, user_id, role_name) VALUES (?, ?, ?)", (new_role.id, interaction.user.id, r_name), commit=True)
            await interaction.response.send_message(f"✅ Уникальная кастомная роль **{r_name}** создана и выдана!", ephemeral=True)
        except Exception:
            execute_db("UPDATE users SET coins = coins + ? WHERE user_id = ?", (self.price, interaction.user.id), commit=True)
            await interaction.response.send_message("❌ Ошибка создания. Средства возвращены.", ephemeral=True)

class CustomTitleModal(discord.ui.Modal, title="Покупка кастомного титула"):
    title_text = discord.ui.TextInput(label="Текст вашего титула", placeholder="Черный Мечник", max_length=30)

    def __init__(self, price: int):
        super().__init__()
        self.price = price

    async def on_submit(self, interaction: discord.Interaction):
        t_text = self.title_text.value.strip()
        execute_db("UPDATE users SET coins = coins - ?, special_title = ? WHERE user_id = ?", (self.price, t_text, interaction.user.id), commit=True)
        execute_db("INSERT INTO user_titles (user_id, title_name) VALUES (?, ?)", (interaction.user.id, t_text), commit=True)
        await interaction.response.send_message(f"👑 Поздравляем! Вы приобрели кастомный титул **{t_text}**!", ephemeral=True)

# --- ИНТЕРАКТИВНОЕ МЕНЮ МАГАЗИНА (/shop) ---

class ShopButtonsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Неприкасаемый (15k)", style=discord.ButtonStyle.blurple, emoji="🛡️")
    async def buy_untouchable(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = discord.utils.get(interaction.guild.roles, name="Неприкасаемый")
        if role and role in interaction.user.roles:
            return await interaction.response.send_message("❌ У вас уже есть эта роль!", ephemeral=True)
        
        price = 15000
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < price: return await interaction.response.send_message("❌ Нужно 15 000 Колов!", ephemeral=True)
        
        execute_db("UPDATE users SET coins = coins - ? WHERE user_id = ?", (price, interaction.user.id), commit=True)
        if role: await interaction.user.add_roles(role)
        await interaction.response.send_message(f"🎉 Вы успешно приобрели статус **Неприкасаемый**!", ephemeral=True)

    @discord.ui.button(label="Кастомная роль (10k)", style=discord.ButtonStyle.green, emoji="✨")
    async def buy_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        count_res = execute_db("SELECT COUNT(*) FROM custom_roles WHERE user_id = ?", (interaction.user.id,), fetchone=True)
        count = count_res[0] if count_res else 0
        if count >= 2:
            return await interaction.response.send_message("❌ У вас максимум кастомных ролей (2/2)!", ephemeral=True)
        
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < 10000: return await interaction.response.send_message("❌ Нужно 10 000 Колов!", ephemeral=True)
        await interaction.response.send_modal(CustomRoleModal(10000))

    @discord.ui.button(label="Кастомный титул (5k)", style=discord.ButtonStyle.grey, emoji="👑")
    async def buy_title(self, interaction: discord.Interaction, button: discord.ui.Button):
        price = 5000
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < price: return await interaction.response.send_message("❌ Нужно 5 000 Колов!", ephemeral=True)
        await interaction.response.send_modal(CustomTitleModal(5000))

@bot.tree.command(name="shop", description="Интерактивный магазин Айнкрада")
async def shop(interaction: discord.Interaction):
    embed = discord.Embed(title="🛒 СИСТЕМНЫЙ МАГАЗИН АЙНКРАДА", description="Нажмите на кнопку ниже для покупки:", color=0x00BFFF)
    embed.add_field(name="🛡️ Неприкасаемый", value="**15,000 Колов** (Иммунитет к грабежам)", inline=False)
    embed.add_field(name="✨ Кастомная роль", value="**10,000 Колов** (Свое имя и цвет)", inline=False)
    embed.add_field(name="👑 Кастомный титул", value="**5,000 Колов** (Титул в профиль)", inline=False)
    await interaction.response.send_message(embed=embed, view=ShopButtonsView())

# --- МЕНЮ ГИЛЬДИЙ (/guild) ---

class GuildCreateModal(discord.ui.Modal, title="Создание новой гильдии"):
    guild_name = discord.ui.TextInput(label="Название гильдии", placeholder="KoB", max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        uid = interaction.user.id
        name = self.guild_name.value.strip()
        user_g = execute_db("SELECT guild_id FROM users WHERE user_id = ?", (uid,), fetchone=True)
        if user_g and user_g[0]: return await interaction.response.send_message("❌ Вы уже состоите в гильдии!", ephemeral=True)

        price = 25000
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(uid)
        if coins < price: return await interaction.response.send_message("❌ Нужно 25 000 Колов!", ephemeral=True)
        
        if execute_db("SELECT * FROM guilds WHERE guild_name = ?", (name,), fetchone=True):
            return await interaction.response.send_message("❌ Название занято!", ephemeral=True)

        execute_db("UPDATE users SET coins = coins - ?, guild_id = ? WHERE user_id = ?", (price, name, uid), commit=True)
        execute_db("INSERT INTO guilds (guild_name, leader_id) VALUES (?, ?)", (name, uid), commit=True)
        await interaction.response.send_message(f"🏰 Гильдия **{name}** создана!", ephemeral=True)

class GuildButtonsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Создать (25k)", style=discord.ButtonStyle.green, emoji="🏰")
    async def btn_create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuildCreateModal())

    @discord.ui.button(label="Инфо", style=discord.ButtonStyle.blurple, emoji="🛡️")
    async def btn_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        row = execute_db("SELECT guild_id FROM users WHERE user_id = ?", (uid,), fetchone=True)
        if not row or not row[0]: return await interaction.response.send_message("❌ Вы не в гильдии!", ephemeral=True)
        
        g_name = row[0]
        g_data = execute_db("SELECT leader_id, bank FROM guilds WHERE guild_name = ?", (g_name,), fetchone=True)
        members = execute_db("SELECT user_id FROM users WHERE guild_id = ?", (g_name,), fetchall=True)
        members_str = ", ".join([f"<@{m[0]}>" for m in members])

        embed = discord.Embed(title=f"🏰 ГИЛЬДИЯ: {g_name}", color=0x9B59B6)
        embed.add_field(name="👑 Лидер", value=f"<@{g_data[0]}>", inline=True)
        embed.add_field(name="💰 Казна", value=f"{g_data[1]:,} Колов", inline=True)
        embed.add_field(name=f"👥 Участники ({len(members)})", value=members_str, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Покинуть", style=discord.ButtonStyle.red, emoji="🚪")
    async def btn_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        row = execute_db("SELECT guild_id FROM users WHERE user_id = ?", (uid,), fetchone=True)
        if not row or not row[0]: return await interaction.response.send_message("❌ Вы не в гильдии.", ephemeral=True)
        
        g_name = row[0]
        leader_id = execute_db("SELECT leader_id FROM guilds WHERE guild_name = ?", (g_name,), fetchone=True)[0]
        execute_db("UPDATE users SET guild_id = NULL WHERE user_id = ?", (uid,), commit=True)
        
        if leader_id == uid:
            new_l = execute_db("SELECT user_id FROM users WHERE guild_id = ? LIMIT 1", (g_name,), fetchone=True)
            if new_l:
                execute_db("UPDATE guilds SET leader_id = ? WHERE guild_name = ?", (new_l[0], g_name), commit=True)
                await interaction.response.send_message(f"🚪 Вы вышли. Лидер передан <@{new_l[0]}>.", ephemeral=True)
            else:
                execute_db("DELETE FROM guilds WHERE guild_name = ?", (g_name,), commit=True)
                await interaction.response.send_message("🚪 Вы вышли. Гильдия распущена.", ephemeral=True)
        else:
            await interaction.response.send_message("🚪 Вы покинули гильдию.", ephemeral=True)

@bot.tree.command(name="guild", description="Панель гильдий")
async def guild_menu(interaction: discord.Interaction):
    embed = discord.Embed(title="🏰 ПАНЕЛЬ УПРАВЛЕНИЯ ГИЛЬДИЯМИ", description="Используйте кнопки ниже:", color=0x9B59B6)
    await interaction.response.send_message(embed=embed, view=GuildButtonsView())

class SetTitleSelect(discord.ui.View):
    def __init__(self, titles_list):
        super().__init__(timeout=30)
        options = [discord.SelectOption(label=t, value=t) for t in titles_list]
        self.select = discord.ui.Select(placeholder="Выберите титул...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        chosen_title = self.select.values[0]
        execute_db("UPDATE users SET special_title = ? WHERE user_id = ?", (chosen_title, interaction.user.id), commit=True)
        await interaction.response.send_message(f"✅ Титул изменен на: **{chosen_title}**!", ephemeral=True)

@bot.tree.command(name="settitle", description="Выбрать активный титул")
async def settitle(interaction: discord.Interaction):
    rows = execute_db("SELECT title_name FROM user_titles WHERE user_id = ?", (interaction.user.id,), fetchall=True)
    if not rows:
        return await interaction.response.send_message("❌ У вас нет купленных титулов!", ephemeral=True)
    titles = [r[0] for r in rows]
    await interaction.response.send_message(embed=discord.Embed(title="[ 👑 ВЫБОР ТИТУЛА ]", color=0xFFD700), view=SetTitleSelect(titles), ephemeral=True)

# --- АУКЦИОН РОЛЕЙ ---

class SellRoleModal(discord.ui.Modal, title="Выставить роль на аукцион"):
    price_input = discord.ui.TextInput(label="Цена в Колах", placeholder="5000", max_length=10)

    def __init__(self, role_id: int, role_name: str):
        super().__init__()
        self.role_id = role_id
        self.role_name = role_name

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price = int(self.price_input.value)
        except ValueError:
            return await interaction.response.send_message("❌ Неверная цена!", ephemeral=True)

        if price <= 0: return await interaction.response.send_message("❌ Цена > 0!", ephemeral=True)

        execute_db("INSERT INTO auction_roles (role_id, seller_id, price, role_name) VALUES (?, ?, ?, ?)", (self.role_id, interaction.user.id, price, self.role_name), commit=True)
        role = interaction.guild.get_role(self.role_id)
        if role: 
            try: await interaction.user.remove_roles(role)
            except: pass
        await interaction.response.send_message(f"✅ Роль **{self.role_name}** выставлена за **{price:,}** Колов!", ephemeral=True)

class SellRoleSelect(discord.ui.View):
    def __init__(self, roles_list):
        super().__init__(timeout=30)
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles_list]
        self.select = discord.ui.Select(placeholder="Выберите роль для продажи...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        role_id = int(self.select.values[0])
        role = interaction.guild.get_role(role_id)
        if not role: return await interaction.response.send_message("❌ Роль не найдена.", ephemeral=True)
        await interaction.response.send_modal(SellRoleModal(role_id, role.name))

class BuyAuctionSelect(discord.ui.View):
    def __init__(self, items_list):
        super().__init__(timeout=60)
        options = [discord.SelectOption(label=role_name, description=f"Цена: {price:,} | Продавец: ID {seller_id}", value=str(sale_id)) for sale_id, role_id, seller_id, price, role_name in items_list]
        self.select = discord.ui.Select(placeholder="Выберите роль для покупки...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        sale_id = int(self.select.values[0])
        item = execute_db("SELECT role_id, seller_id, price, role_name FROM auction_roles WHERE sale_id = ?", (sale_id,), fetchone=True)
        if not item: return await interaction.response.send_message("❌ Лот уже продан!", ephemeral=True)

        role_id, seller_id, price, role_name = item
        if interaction.user.id == seller_id: return await interaction.response.send_message("❌ Нельзя покупать свое!", ephemeral=True)

        buyer_coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if buyer_coins < price: return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)

        execute_db("UPDATE users SET coins = coins - ? WHERE user_id = ?", (price, interaction.user.id), commit=True)
        execute_db("UPDATE users SET coins = coins + ? WHERE user_id = ?", (price, seller_id), commit=True)
        execute_db("UPDATE custom_roles SET user_id = ? WHERE role_id = ?", (interaction.user.id, role_id), commit=True)
        execute_db("DELETE FROM auction_roles WHERE sale_id = ?", (sale_id,), commit=True)

        role = interaction.guild.get_role(role_id)
        if role:
            try: await interaction.user.add_roles(role)
            except: pass

        await interaction.response.edit_message(content=f"🎉 Вы купили роль **{role_name}** за **{price:,}** Колов!", embed=None, view=None)

@bot.tree.command(name="auction", description="Аукцион кастомных ролей")
@app_commands.choices(action=[
    app_commands.Choice(name="Купить роль", value="list"),
    app_commands.Choice(name="Продать роль", value="sell")
])
async def auction(interaction: discord.Interaction, action: str):
    uid = interaction.user.id
    if action == "list":
        items = execute_db("SELECT sale_id, role_id, seller_id, price, role_name FROM auction_roles", fetchall=True)
        if not items: return await interaction.response.send_message("📦 На аукционе пусто.", ephemeral=True)
        embed = discord.Embed(title="[ 🏛️ АУКЦИОН РОЛЕЙ ]", color=0xFFD700)
        await interaction.response.send_message(embed=embed, view=BuyAuctionSelect(items), ephemeral=True)
    elif action == "sell":
        rows = execute_db("SELECT role_id FROM custom_roles WHERE user_id = ?", (uid,), fetchall=True)
        if not rows: return await interaction.response.send_message("❌ У вас нет кастомных ролей!", ephemeral=True)
        user_roles = [interaction.guild.get_role(r[0]) for r in rows if interaction.guild.get_role(r[0])]
        if not user_roles: return await interaction.response.send_message("❌ Роли не найдены.", ephemeral=True)
        await interaction.response.send_message(embed=discord.Embed(title="[ 🏷️ ПРОДАЖА РОЛИ ]", color=0x2ECC71), view=SellRoleSelect(user_roles), ephemeral=True)

@bot.tree.command(name="leaderboard", description="Топ-10 игроков")
async def leaderboard(interaction: discord.Interaction):
  top = execute_db("SELECT user_id, level, coins FROM users ORDER BY level DESC, coins DESC LIMIT 10", fetchall=True)
  embed = discord.Embed(title="[ 🏆 ТОП-10 ИГРОКОВ АЙНКРАДА ]", color=0xFFD700)
  embed.description = "\n".join([f"`#{i}` <@{uid}> — **{lvl} этаж** | {cns:,} <:col:1530575386457542817>" for i, (uid, lvl, cns) in enumerate(top, 1)]) if top else "Пусто"
  await interaction.response.send_message(embed=embed)

# --- АДМИНСКИЕ КОМАНДЫ ---

@bot.tree.command(name="setlevel", description="[АДМИН] Установить этаж")
@app_commands.default_permissions(administrator=True)
async def setlevel(interaction: discord.Interaction, member: discord.Member, level: int):
  get_or_create_user(member.id)
  execute_db("UPDATE users SET level = ?, xp = 0 WHERE user_id = ?", (level, member.id), commit=True)
  await check_level_roles(member, level)
  await interaction.response.send_message(f"✅ Установлен {level} этаж для {member.mention}.", ephemeral=True)

@bot.tree.command(name="givecoins", description="[АДМИН] Выдать Колы")
@app_commands.default_permissions(administrator=True)
async def givecoins(interaction: discord.Interaction, member: discord.Member, amount: int):
  get_or_create_user(member.id)
  execute_db("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, member.id), commit=True)
  await interaction.response.send_message(f"✅ Выдано {amount:,} Колов {member.mention}.", ephemeral=True)

@bot.tree.command(name="resetdb", description="[АДМИН] Очистить базу данных")
@app_commands.default_permissions(administrator=True)
async def resetdb(interaction: discord.Interaction):
  execute_db("DROP TABLE IF EXISTS users", commit=True)
  execute_db("DROP TABLE IF EXISTS guilds", commit=True)
  execute_db("DROP TABLE IF EXISTS custom_roles", commit=True)
  execute_db("DROP TABLE IF EXISTS user_titles", commit=True)
  init_db()
  await interaction.response.send_message("☢️ База очищена!", ephemeral=True)

bot.run("MTUzMDkyMjgxODkzNzk3MDcyOQ.GLjRjm.LmMWbJA8Dq7BkJIcbotnKjNaxa-4lo9BqgZ-tM")