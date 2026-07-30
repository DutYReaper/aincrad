import os
import certifi
import random
import time
import asyncio
import discord
from pymongo import MongoClient
from discord import app_commands
from discord.ext import commands
from keep_alive import keep_alive

# Достаем ссылку на базу из переменных окружения Render
MONGO_URI = os.getenv('MONGO_URI')

# Подключаемся с поддержкой сертификатов certifi
cluster = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = cluster.aincrad_data
users_collection = db.users
custom_roles_collection = db.custom_roles
auction_collection = db.auction_roles
titles_collection = db.user_titles
guilds_collection = db.guilds

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
            "special_title": "Отсутствует"
        }
        users_collection.insert_one(new_user)
        return 100, 0, 1, 0.0, 0.0, 0.0, 0.0, 0, None, 'Отсутствует'
    
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
        user.get("special_title", "Отсутствует")
    )

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

    users_collection.update_one({"_id": user_id}, {"$set": {"xp": xp, "level": level}})

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
  users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -amount}})
  users_collection.update_one({"_id": member.id}, {"$inc": {"coins": amount}})

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

  users_collection.update_one({"_id": user_id}, {"$inc": {"coins": reward_coins}, "$set": {"streak": streak, "last_daily": current_time}})
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
  
  users_collection.update_one({"_id": user_id}, {"$inc": {"coins": earned}, "$set": {"last_work": current_time}})
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
      users_collection.update_one({"_id": user_id}, {"$inc": {"coins": reward}, "$set": {"last_crime": current_time}})
      await add_xp(interaction, user_id, 15)
      embed = discord.Embed(title="[ 🥷 КРИМИНАЛЬНЫЙ УСПЕХ ]", color=0x2ECC71)
      embed.description = f"Куш сорван! Получено **{reward}** Колов <:col:1530575386457542817>!"
  else:
      fine = random.randint(30, 70)
      new_coins = max(0, coins - fine)
      users_collection.update_one({"_id": user_id}, {"$set": {"coins": new_coins, "last_crime": current_time}})
      embed = discord.Embed(title="[ ❌ ПОЙМАН СТРАЖЕЙ ]", color=0xE74C3C)
      embed.description = f"Вас поймали! Штраф: **{fine}** Колов <:col:1530575386457542817>."
  await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rob", description="Попытаться ограбить другого игрока (Сбалансировано)")
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
    
    if target_coins < 500:
        return await interaction.response.send_message(f"❌ У игрока {member.mention} слишком мало средств (меньше 500 Колов). Его грабить бессмысленно!", ephemeral=True)

    # ЗАЩИТА СЛАБЫХ: Если твой баланс больше баланса жертвы в 5 раз и у тебя больше 50 000 Колов — грабить нельзя!
    if att_coins > 50000 and att_coins > target_coins * 5:
        return await interaction.response.send_message(f"❌ У тебя слишком много денег ({att_coins:,} Колов). Тебе стыдно грабить бедняка с балансом {target_coins:,} Колов! Ищи соперника побогаче.", ephemeral=True)

    # Рассчитываем сумму кражи (от 5% до 10% от баланса жертвы)
    potential_amount = random.randint(int(target_coins * 0.05), int(target_coins * 0.10))
    if potential_amount < 20:
        potential_amount = 20

    if att_coins < potential_amount:
        return await interaction.response.send_message(f"❌ На вашем балансе должно быть минимум **{potential_amount:,}** Колов для покрытия возможного штрафа!", ephemeral=True)

    users_collection.update_one({"_id": attacker.id}, {"$set": {"last_rob": current_time}})

    # Отправляем интригующее сообщение БЕЗ гифки
    embed_loading = discord.Embed(title="[ 🕵️ ОГРАБЛЕНИЕ ]", description=f"Вы тихо подкрадываетесь к {member.mention}...", color=0x2C3E50)
    await interaction.response.send_message(embed=embed_loading)
    await asyncio.sleep(3.0)

    success = random.choice([True, False]) # 50/50 честный шанс
    embed_res = discord.Embed(title="[ 🕵️ ИТОГ ОГРАБЛЕНИЯ ]")

    if success:
        success_gif = "https://i.pinimg.com/originals/58/23/81/582381e4e65d4f6a027116695445d649.gif"
        embed_res.set_image(url=success_gif)
        
        users_collection.update_one({"_id": attacker.id}, {"$inc": {"coins": potential_amount}})
        users_collection.update_one({"_id": member.id}, {"$inc": {"coins": -potential_amount}})
        await add_xp(interaction, attacker.id, 20)
        
        embed_res.description = f"🎉 Успех! Вы незаметно вытащили **{potential_amount:,}** Колов у {member.mention}!"
        embed_res.color = 0x2ECC71
    else:
        fail_gif = "https://media.tenor.com/LjXd-V-BrwIAAAAd/kazuma-run-kazuma-scared.gif"
        embed_res.set_image(url=fail_gif)
        
        users_collection.update_one({"_id": attacker.id}, {"$inc": {"coins": -potential_amount}})
        
        embed_res.description = f"🚨 Вас поймали за руку! Вы с криками убегаете и выплачиваете штраф **{potential_amount:,}** Колов."
        embed_res.color = 0xE74C3C

    await interaction.edit_original_response(embed=embed_res)
    
# --- АЗАРТНЫЕ ИГРЫ ---

@bot.tree.command(name="dice", description="Бросить кости против системы (Мин. ставка: 50)")
async def dice(interaction: discord.Interaction, amount: int):
  if not is_admin_or_mod(interaction.user) and amount < 50:
      return await interaction.response.send_message("❌ Минимальная ставка для азартных игр — **50 Колов**!", ephemeral=True)
      
  coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
  if coins < amount: return await interaction.response.send_message("❌ Недостаточно средств!", ephemeral=True)

  users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -amount}})

  embed_loading = discord.Embed(title="[ 🎲 КОСТИ АЙНКРАДА ]", description="Бросаем кости...", color=0x9B59B6)
  embed_loading.set_image(url="https://i.pinimg.com/originals/80/9f/ba/809fba531ccbb8e24010696ffa1503e2.gif")
  await interaction.response.send_message(embed=embed_loading)
  await asyncio.sleep(3.0)

  p_roll, b_roll = random.randint(1, 6), random.randint(1, 6)
  embed_res = discord.Embed(title="[ 🎲 КОСТИ АЙНКРАДА ]", color=0x9B59B6)
  # Гифка удалена из итогов

  if p_roll > b_roll:
      users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": amount * 2}})
      embed_res.description = f"🎉 **Победа!** Вы выбросили `🎲 {p_roll}`, бот — `🤖 {b_roll}`.\nВыиграно: **{amount:,}** Колов!"
      embed_res.color = 0x2ECC71
  elif p_roll < b_roll:
      embed_res.description = f"💀 **Поражение.** Вы выбросили `🎲 {p_roll}`, бот — `🤖 {b_roll}`.\nПотеряно: **{amount:,}** Колов."
      embed_res.color = 0xE74C3C
  else:
      users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": amount}})
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

  users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -amount}})

  gif_url = "https://media.tenor.com/9PALsSO_XpsAAAAC/misaka-mikoto.gif"
  embed_loading = discord.Embed(title="[ 🪙 ОРЕЛ И РЕШКА ]", description="Монетка подброшена в воздух...", color=0xF1C40F)
  embed_loading.set_image(url=gif_url)
  await interaction.response.send_message(embed=embed_loading)
  await asyncio.sleep(3.0)

  result = random.choice(["орел", "решка"])
  embed_res = discord.Embed(title="[ 🪙 ИТОГ ПОДБРОСА ]", color=0x2ECC71)
  # Гифка удалена из итогов
  
  if choice == result:
      users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": amount * 2}})
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

  users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -amount}})

  embed_loading = discord.Embed(title="[ 🎯 РУССКАЯ РУЛЕТКА ]", description="Барабан вращается...", color=0xE74C3C)
  embed_loading.set_image(url="https://i.pinimg.com/originals/ac/56/c5/ac56c5c7e6037a698e22c9a30a8dccda.gif")
  await interaction.response.send_message(embed=embed_loading)
  await asyncio.sleep(3.0)

  shot = random.choice([True, False, False, False, False, False])
  embed_res = discord.Embed(title="[ 🎯 РУССКАЯ РУЛЕТКА ]", color=0xE74C3C)
  # Гифка удалена из итогов
  
  if not shot:
      users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": amount * 2}})
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
        exists = custom_roles_collection.find_one({"role_name": {"$regex": f"^{new_name}$", "$options": "i"}, "role_id": {"$ne": self.role.id}})
        if exists or discord.utils.get(interaction.guild.roles, name=new_name):
            return await interaction.response.send_message("❌ Роль с таким названием уже существует на сервере!", ephemeral=True)

        try:
            color_int = int(self.role_color.value.strip(), 16)
        except ValueError:
            return await interaction.response.send_message("❌ Неверный формат HEX-цвета!", ephemeral=True)

        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}})
        await self.role.edit(name=new_name, color=discord.Color(color_int))
        custom_roles_collection.update_one({"role_id": self.role.id}, {"$set": {"role_name": new_name}})
        auction_collection.update_one({"role_id": self.role.id}, {"$set": {"role_name": new_name}})
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

    rows = list(custom_roles_collection.find({"user_id": interaction.user.id}))
    if not rows:
        return await interaction.response.send_message("❌ У вас нет купленных кастомных ролей!", ephemeral=True)

    user_roles = [interaction.guild.get_role(r["role_id"]) for r in rows if interaction.guild.get_role(r["role_id"])]
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
        
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}})
        custom_roles_collection.delete_one({"role_id": role_id})
        auction_collection.delete_one({"role_id": role_id})

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

    rows = list(custom_roles_collection.find({"user_id": interaction.user.id}))
    if not rows:
        return await interaction.response.send_message("❌ У вас нет кастомных ролей для удаления!", ephemeral=True)

    user_roles = [interaction.guild.get_role(r["role_id"]) for r in rows if interaction.guild.get_role(r["role_id"])]
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

        exists = custom_roles_collection.find_one({"role_name": {"$regex": f"^{r_name}$", "$options": "i"}})
        if exists or discord.utils.get(interaction.guild.roles, name=r_name):
            return await interaction.response.send_message("❌ Ошибка: Роль с таким названием уже существует!", ephemeral=True)

        try:
            color_int = int(self.role_color.value.strip(), 16)
        except ValueError:
            return await interaction.response.send_message("❌ Неверный формат HEX-цвета!", ephemeral=True)

        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}})
        
        try:
            new_role = await interaction.guild.create_role(name=r_name, color=discord.Color(color_int))
            await interaction.user.add_roles(new_role)
            custom_roles_collection.insert_one({"role_id": new_role.id, "user_id": interaction.user.id, "role_name": r_name})
            await interaction.response.send_message(f"✅ Уникальная кастомная роль **{r_name}** создана и выдана!", ephemeral=True)
        except Exception:
            users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": self.price}})
            await interaction.response.send_message("❌ Ошибка создания. Средства возвращены.", ephemeral=True)

class CustomTitleModal(discord.ui.Modal, title="Покупка кастомного титула"):
    title_text = discord.ui.TextInput(label="Текст вашего титула", placeholder="Черный Мечник", max_length=30)

    def __init__(self, price: int):
        super().__init__()
        self.price = price

    async def on_submit(self, interaction: discord.Interaction):
        t_text = self.title_text.value.strip()
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}, "$set": {"special_title": t_text}})
        titles_collection.insert_one({"user_id": interaction.user.id, "title_name": t_text})
        await interaction.response.send_message(f"👑 Поздравляем! Вы приобрели кастомный титул **{t_text}**!", ephemeral=True)

# --- ИНТЕРАКТИВНОЕ МЕНЮ МАГАЗИНА (/shop) — ОБНОВЛЕННЫЙ ДИЗАЙН ---

class ShopButtonsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Неприкасаемый", style=discord.ButtonStyle.blurple, emoji="🛡️")
    async def buy_untouchable(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = discord.utils.get(interaction.guild.roles, name="Неприкасаемый")
        if role and role in interaction.user.roles:
            return await interaction.response.send_message("❌ У вас уже есть эта роль!", ephemeral=True)
        
        price = 15000
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < price: return await interaction.response.send_message("❌ Нужно **15,000** Колов!", ephemeral=True)
        
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -price}})
        if role: await interaction.user.add_roles(role)
        await interaction.response.send_message(f"🎉 Вы успешно приобрели статус **Неприкасаемый**!", ephemeral=True)

    @discord.ui.button(label="Кастомная роль", style=discord.ButtonStyle.green, emoji="✨")
    async def buy_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        count = custom_roles_collection.count_documents({"user_id": interaction.user.id})
        if count >= 2:
            return await interaction.response.send_message("❌ У вас максимум кастомных ролей (2/2)!", ephemeral=True)
        
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < 10000: return await interaction.response.send_message("❌ Нужно **10,000** Колов!", ephemeral=True)
        await interaction.response.send_modal(CustomRoleModal(10000))

    @discord.ui.button(label="Кастомный титул", style=discord.ButtonStyle.grey, emoji="👑")
    async def buy_title(self, interaction: discord.Interaction, button: discord.ui.Button):
        price = 5000
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < price: return await interaction.response.send_message("❌ Нужно **5,000** Колов!", ephemeral=True)
        await interaction.response.send_modal(CustomTitleModal(5000))

@bot.tree.command(name="shop", description="Интерактивный магазин Айнкрада")
async def shop(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛒 ИГРОВОЙ МАГАЗИН АЙНКРАДА", 
        description="Приобретайте элитные статусы и уникальные предметы для кастомизации.", 
        color=0x00BFFF
    )
    embed.add_field(
        name="🛡️ Элитный статус «Неприкасаемый»", 
        value="• **Цена:** `15,000` Колов\n• **Описание:** Надежный иммунитет от грабежей другими игроками.", 
        inline=False
    )
    embed.add_field(
        name="✨ Персональная Кастомная Роль", 
        value="• **Цена:** `10,000` Колов\n• **Описание:** Личное название и уникальный цвет роли на сервере.", 
        inline=False
    )
    embed.add_field(
        name="👑 Уникальный Кастомный Титул", 
        value="• **Цена:** `5,000` Колов\n• **Описание:** Красивый статус, отображаемый в вашем `/profile`.", 
        inline=False
    )
    embed.set_footer(text="Aincrad Economy • Выберите товар кнопкой ниже")
    await interaction.response.send_message(embed=embed, view=ShopButtonsView())

# --- МЕНЮ ГИЛЬДИЙ (/guild) — ОБНОВЛЕННЫЙ ДИЗАЙН С ЛИДЕРОМ ---

class GuildCreateModal(discord.ui.Modal, title="Создание новой гильдии"):
    guild_name = discord.ui.TextInput(label="Название гильдии", placeholder="KoB", max_length=30)

    async def on_submit(self, interaction: discord.Interaction):
        uid = interaction.user.id
        name = self.guild_name.value.strip()
        _, _, _, _, _, _, _, _, user_g, _ = get_or_create_user(uid)
        if user_g: return await interaction.response.send_message("❌ Вы уже состоите в гильдии!", ephemeral=True)

        price = 25000
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(uid)
        if coins < price: return await interaction.response.send_message("❌ Для создания гильдии нужно **25,000** Колов!", ephemeral=True)
        
        if guilds_collection.find_one({"guild_name": name}):
            return await interaction.response.send_message("❌ Гильдия с таким названием уже существует!", ephemeral=True)

        users_collection.update_one({"_id": uid}, {"$inc": {"coins": -price}, "$set": {"guild_id": name}})
        guilds_collection.insert_one({"guild_name": name, "leader_id": uid, "bank": 0, "level": 1})
        await interaction.response.send_message(f"🏰 Гильдия **{name}** успешно создана! Вы назначены лидером.", ephemeral=True)

class GuildButtonsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Создать гильдию", style=discord.ButtonStyle.green, emoji="🏰")
    async def btn_create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuildCreateModal())

    @discord.ui.button(label="Информация", style=discord.ButtonStyle.blurple, emoji="🛡️")
    async def btn_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        _, _, _, _, _, _, _, _, g_name, _ = get_or_create_user(uid)
        if not g_name: return await interaction.response.send_message("❌ Вы не состоите ни в одной гильдии!", ephemeral=True)
        
        g_data = guilds_collection.find_one({"guild_name": g_name})
        members = list(users_collection.find({"guild_id": g_name}))
        members_str = ", ".join([f"<@{m['_id']}>" for m in members]) if members else "Пусто"

        embed = discord.Embed(title=f"🛡️ СТАТУС ГИЛЬДИИ: {g_name}", description="Официальные данные объединения игроков", color=0x9B59B6)
        embed.add_field(name="👑 Лидер команды", value=f"<@{g_data['leader_id']}>", inline=False)
        embed.add_field(name="💰 Казна гильдии", value=f"**{g_data.get('bank', 0):,}** Колов", inline=True)
        embed.add_field(name="⭐ Уровень", value=f"**{g_data.get('level', 1)}**", inline=True)
        embed.add_field(name=f"👥 Участники ({len(members)})", value=members_str, inline=False)
        embed.set_footer(text="Aincrad Guild System")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Покинуть", style=discord.ButtonStyle.red, emoji="🚪")
    async def btn_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        _, _, _, _, _, _, _, _, g_name, _ = get_or_create_user(uid)
        if not g_name: return await interaction.response.send_message("❌ Вы не состоите в гильдии.", ephemeral=True)
        
        g_data = guilds_collection.find_one({"guild_name": g_name})
        leader_id = g_data["leader_id"] if g_data else None
        
        users_collection.update_one({"_id": uid}, {"$set": {"guild_id": None}})
        
        if leader_id == uid:
            new_member = users_collection.find_one({"guild_id": g_name})
            if new_member:
                new_l_id = new_member["_id"]
                guilds_collection.update_one({"guild_name": g_name}, {"$set": {"leader_id": new_l_id}})
                await interaction.response.send_message(f"🚪 Вы покинули гильдию. Новым лидером назначен <@{new_l_id}>.", ephemeral=True)
            else:
                guilds_collection.delete_one({"guild_name": g_name})
                await interaction.response.send_message("🚪 Вы вышли. В гильдии не осталось участников, она распущена.", ephemeral=True)
        else:
            await interaction.response.send_message("🚪 Вы успешно покинули гильдию.", ephemeral=True)

@bot.tree.command(name="guild", description="Панель управления гильдиями")
async def guild_menu(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏰 УПРАВЛЕНИЕ ГИЛЬДИЯМИ АЙНКРАДА", 
        description="Объединяйте усилия с другими игроками, создавайте кланы и копите общую казну.\n\nИспользуйте кнопки ниже для управления:", 
        color=0x9B59B6
    )
    embed.add_field(name="💰 Стоимость создания", value="`25,000` Колов", inline=True)
    embed.add_field(name="⭐ Возможности", value="Общий банк и статус", inline=True)
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
        users_collection.update_one({"_id": interaction.user.id}, {"$set": {"special_title": chosen_title}})
        await interaction.response.send_message(f"✅ Активный титул изменен на: **{chosen_title}**!", ephemeral=True)

@bot.tree.command(name="settitle", description="Выбрать активный титул в профиль")
async def settitle(interaction: discord.Interaction):
    rows = list(titles_collection.find({"user_id": interaction.user.id}))
    if not rows:
        return await interaction.response.send_message("❌ У вас пока нет купленных титулов!", ephemeral=True)
    titles = [r["title_name"] for r in rows]
    await interaction.response.send_message(embed=discord.Embed(title="[ 👑 ВЫБОР ТИТУЛА ]", color=0xFFD700), view=SetTitleSelect(titles), ephemeral=True)

# --- АУКЦИОН РОЛЕЙ (/auction) — ОБНОВЛЕННЫЙ ДИЗАЙН ---

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
            return await interaction.response.send_message("❌ Неверный формат цены!", ephemeral=True)

        if price <= 0: return await interaction.response.send_message("❌ Цена должна быть больше 0!", ephemeral=True)

        last_item = auction_collection.find_one(sort=[("sale_id", -1)])
        next_sale_id = (last_item["sale_id"] + 1) if last_item and "sale_id" in last_item else 1

        auction_collection.insert_one({
            "sale_id": next_sale_id,
            "role_id": self.role_id,
            "seller_id": interaction.user.id,
            "price": price,
            "role_name": self.role_name
        })

        role = interaction.guild.get_role(self.role_id)
        if role: 
            try: await interaction.user.remove_roles(role)
            except: pass
        await interaction.response.send_message(f"✅ Роль **{self.role_name}** успешно выставлена на аукцион за **{price:,}** Колов!", ephemeral=True)

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
        options = [discord.SelectOption(label=item["role_name"], description=f"Цена: {item['price']:,} Колов | Продавец ID: {item['seller_id']}", value=str(item["sale_id"])) for item in items_list]
        self.select = discord.ui.Select(placeholder="Выберите лот для покупки...", options=options)
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        sale_id = int(self.select.values[0])
        item = auction_collection.find_one({"sale_id": sale_id})
        if not item: return await interaction.response.send_message("❌ Этот лот уже продан!", ephemeral=True)

        role_id, seller_id, price, role_name = item["role_id"], item["seller_id"], item["price"], item["role_name"]
        if interaction.user.id == seller_id: return await interaction.response.send_message("❌ Нельзя покупать собственные лоты!", ephemeral=True)

        buyer_coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if buyer_coins < price: return await interaction.response.send_message("❌ Недостаточно средств для покупки!", ephemeral=True)

        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -price}})
        users_collection.update_one({"_id": seller_id}, {"$inc": {"coins": price}})
        custom_roles_collection.update_one({"role_id": role_id}, {"$set": {"user_id": interaction.user.id}})
        auction_collection.delete_one({"sale_id": sale_id})

        role = interaction.guild.get_role(role_id)
        if role:
            try: await interaction.user.add_roles(role)
            except: pass

        await interaction.response.edit_message(content=f"🎉 Вы успешно приобрели уникальную роль **{role_name}** за **{price:,}** Колов!", embed=None, view=None)

# --- АКТИВНЫЙ АУКЦИОН РОЛЕЙ С ПАГИНАЦИЕЙ ---

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
            return await interaction.response.send_message("❌ Неверный формат цены!", ephemeral=True)

        if price <= 0: return await interaction.response.send_message("❌ Цена должна быть больше 0!", ephemeral=True)

        last_item = auction_collection.find_one(sort=[("sale_id", -1)])
        next_sale_id = (last_item["sale_id"] + 1) if last_item and "sale_id" in last_item else 1

        auction_collection.insert_one({
            "sale_id": next_sale_id,
            "role_id": self.role_id,
            "seller_id": interaction.user.id,
            "price": price,
            "role_name": self.role_name
        })

        role = interaction.guild.get_role(self.role_id)
        if role: 
            try: await interaction.user.remove_roles(role)
            except: pass
        await interaction.response.send_message(f"✅ Роль **{self.role_name}** успешно выставлена на аукцион за **{price:,}** Колов!", ephemeral=True)

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

class AuctionPagingView(discord.ui.View):
    def __init__(self, items):
        super().__init__(timeout=60)
        self.items = items
        self.page = 0
        self.per_page = 5  # Количество лотов на одной странице
        self.update_buttons()

    def update_buttons(self):
        max_pages = (len(self.items) - 1) // self.per_page
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= max_pages

    def get_current_embed(self):
        embed = discord.Embed(
            title="[ 🏛️ ГЛОБАЛЬНЫЙ АУКЦИОН РОЛЕЙ ]", 
            description="Здесь игроки выставляют на продажу свои уникальные кастомные роли.", 
            color=0xFFD700
        )
        
        start = self.page * self.per_page
        end = start + self.per_page
        current_slice = self.items[start:end]
        
        max_pages = max(1, (len(self.items) + self.per_page - 1) // self.per_page)

        for idx, item in enumerate(current_slice, start=start + 1):
            embed.add_field(
                name=f"📦 Лот #{idx}: {item['role_name']}",
                value=f"• **Цена:** `{item['price']:,}` Колов\n• **Продавец:** <@{item['seller_id']}>\n• **Команда для покупки:** `/buyitem {item['sale_id']}` (или выберите в меню)",
                inline=False
            )
            
        embed.set_footer(text=f"Страница {self.page + 1} из {max_pages} • Aincrad Trading System")
        return embed

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.grey, emoji="⬅️")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

    @discord.ui.button(label="Вперед", style=discord.ButtonStyle.grey, emoji="➡️")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_pages = (len(self.items) - 1) // self.per_page
        if self.page < max_pages:
            self.page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_current_embed(), view=self)

@bot.tree.command(name="auction", description="Глобальный аукцион кастомных ролей с листанием")
@app_commands.choices(action=[
    app_commands.Choice(name="Посмотреть лоты", value="list"),
    app_commands.Choice(name="Продать роль", value="sell")
])
async def auction(interaction: discord.Interaction, action: str):
    uid = interaction.user.id
    if action == "list":
        items = list(auction_collection.find())
        if not items: 
            return await interaction.response.send_message("📦 На текущий момент торговая площадка пуста.", ephemeral=True)
        
        view = AuctionPagingView(items)
        await interaction.response.send_message(embed=view.get_current_embed(), view=view, ephemeral=True)
        
    elif action == "sell":
        rows = list(custom_roles_collection.find({"user_id": uid}))
        if not rows: 
            return await interaction.response.send_message("❌ У вас нет кастомных ролей для продажи!", ephemeral=True)
            
        user_roles = [interaction.guild.get_role(r["role_id"]) for r in rows if interaction.guild.get_role(r["role_id"])]
        if not user_roles: 
            return await interaction.response.send_message("❌ Ваши роли не найдены на сервере.", ephemeral=True)
            
        embed = discord.Embed(
            title="[ 🏷️ ВЫСТАВЛЕНИЕ РОЛИ НА АУКЦИОН ]", 
            description="Выберите роль из списка, которую хотите продать другим игрокам:", 
            color=0x2ECC71
        )
        await interaction.response.send_message(embed=embed, view=SellRoleSelect(user_roles), ephemeral=True)

@bot.tree.command(name="leaderboard", description="Топ-10 игроков Айнкрада")
async def leaderboard(interaction: discord.Interaction):
  top = list(users_collection.find().sort([("level", -1), ("coins", -1)]).limit(10))
  embed = discord.Embed(title="[ 🏆 ТОП-10 ИГРОКОВ АЙНКРАДА ]", color=0xFFD700)
  embed.description = "\n".join([f"`#{i}` <@{u['_id']}> — **{u.get('level', 1)} этаж** | {u.get('coins', 0):,} <:col:1530575386457542817>" for i, u in enumerate(top, 1)]) if top else "Пусто"
  embed.set_footer(text="Рейтинг сильнейших игроков башни")
  await interaction.response.send_message(embed=embed)

# --- ВСЕ АДМИНСКИЕ КОМАНДЫ И ЧИТЫ (ПОЛНЫЙ КОМПЛЕКТ) ---

@bot.tree.command(name="setlevel", description="[АДМИН] Установить этаж игроку")
@app_commands.default_permissions(administrator=True)
async def setlevel(interaction: discord.Interaction, member: discord.Member, level: int):
  get_or_create_user(member.id)
  users_collection.update_one({"_id": member.id}, {"$set": {"level": level, "xp": 0}})
  await check_level_roles(member, level)
  await interaction.response.send_message(f"✅ Установлен {level} этаж для {member.mention}.", ephemeral=True)

@bot.tree.command(name="setxp", description="[АДМИН] Установить точное количество опыта (XP)")
@app_commands.default_permissions(administrator=True)
async def setxp(interaction: discord.Interaction, member: discord.Member, xp: int):
  get_or_create_user(member.id)
  users_collection.update_one({"_id": member.id}, {"$set": {"xp": xp}})
  await interaction.response.send_message(f"✅ Установлено `{xp:,} XP` для пользователя {member.mention}.", ephemeral=True)

@bot.tree.command(name="givexp", description="[АДМИН] Выдать опыт (XP) игроку")
@app_commands.default_permissions(administrator=True)
async def givexp(interaction: discord.Interaction, member: discord.Member, amount: int):
  get_or_create_user(member.id)
  await add_xp(interaction, member.id, amount)
  await interaction.response.send_message(f"✅ Выдано `{amount:,} XP` пользователю {member.mention}.", ephemeral=True)

@bot.tree.command(name="setcoins", description="[АДМИН] Установить точный баланс Колов")
@app_commands.default_permissions(administrator=True)
async def setcoins(interaction: discord.Interaction, member: discord.Member, amount: int):
  get_or_create_user(member.id)
  users_collection.update_one({"_id": member.id}, {"$set": {"coins": amount}})
  await interaction.response.send_message(f"✅ Баланс {member.mention} изменен на ровно `{amount:,}` Колов.", ephemeral=True)

@bot.tree.command(name="givecoins", description="[АДМИН] Выдать Колы игроку")
@app_commands.default_permissions(administrator=True)
async def givecoins(interaction: discord.Interaction, member: discord.Member, amount: int):
  get_or_create_user(member.id)
  users_collection.update_one({"_id": member.id}, {"$inc": {"coins": amount}})
  await interaction.response.send_message(f"✅ Выдано `{amount:,}` Колов пользователю {member.mention}.", ephemeral=True)

@bot.tree.command(name="takecoins", description="[АДМИН] Забрать Колы у игрока")
@app_commands.default_permissions(administrator=True)
async def takecoins(interaction: discord.Interaction, member: discord.Member, amount: int):
  get_or_create_user(member.id)
  users_collection.update_one({"_id": member.id}, {"$inc": {"coins": -amount}})
  await interaction.response.send_message(f"🔻 Списано `{amount:,}` Колов у пользователя {member.mention}.", ephemeral=True)

@bot.tree.command(name="setstreak", description="[АДМИН] Установить текущий стрик входов")
@app_commands.default_permissions(administrator=True)
async def setstreak(interaction: discord.Interaction, member: discord.Member, days: int):
  get_or_create_user(member.id)
  users_collection.update_one({"_id": member.id}, {"$set": {"streak": days}})
  await interaction.response.send_message(f"✅ Установлен стрик в `{days} дн.` для {member.mention}.", ephemeral=True)

@bot.tree.command(name="resetcd", description="[АДМИН] Сбросить все кулдауны (daily, work, crime, rob) игроку")
@app_commands.default_permissions(administrator=True)
async def resetcd(interaction: discord.Interaction, member: discord.Member = None):
  target = member or interaction.user
  get_or_create_user(target.id)
  users_collection.update_one(
      {"_id": target.id}, 
      {"$set": {"last_daily": 0.0, "last_work": 0.0, "last_crime": 0.0, "last_rob": 0.0}}
  )
  await interaction.response.send_message(f"⚡ Все кулдауны для {target.mention} успешно сброшены!", ephemeral=True)

@bot.tree.command(name="resetuser", description="[АДМИН] Полностью сбросить профиль конкретного игрока")
@app_commands.default_permissions(administrator=True)
async def resetuser(interaction: discord.Interaction, member: discord.Member):
  users_collection.delete_one({"_id": member.id})
  get_or_create_user(member.id)
  await interaction.response.send_message(f"☢️ Профиль игрока {member.mention} полностью сброшен к заводским настройкам!", ephemeral=True)

@bot.tree.command(name="resetdb", description="[АДМИН] Полная глобальная очистка базы данных")
@app_commands.default_permissions(administrator=True)
async def resetdb(interaction: discord.Interaction):        
  users_collection.delete_many({})
  guilds_collection.delete_many({})
  custom_roles_collection.delete_many({})
  titles_collection.delete_many({})
  auction_collection.delete_many({})
  await interaction.response.send_message("☢️ Облачная база данных полностью очищена!", ephemeral=True)

keep_alive()
bot.run(os.getenv("TOKEN"))
