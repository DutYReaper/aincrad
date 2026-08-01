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

bot = commands.Bot(
    command_prefix="!", 
    intents=intents,
    chunk_guilds_at_startup=False,
    max_messages=10
)

# Глобальный флаг режима техобслуживания
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
        await interaction.response.send_message("🛠️ **[ SYSTEM ]** В данный момент Кардинал проводит техническое обслуживание. Попробуйте позже.", ephemeral=True)
        return False
    return True

async def check_level_roles(member: discord.Member, current_level: int):
    highest_role_name = None
    for req_level, data in sorted(ROLES_MAPPING.items(), reverse=True):
        if current_level >= req_level:
            highest_role_name = data["name"]
            break
            
    if not highest_role_name: return

    highest_role = discord.utils.get(member.guild.roles, name=highest_role_name)
    roles_to_remove = []
    for req_level, data in ROLES_MAPPING.items():
        r_name = data["name"]
        if r_name != highest_role_name:
            old_role = discord.utils.get(member.guild.roles, name=r_name)
            if old_role and old_role in member.roles:
                roles_to_remove.append(old_role)
                
    if roles_to_remove:
        try: await member.remove_roles(*roles_to_remove)
        except: pass

    if highest_role and highest_role not in member.roles:
        try:
            await member.add_roles(highest_role)
            await member.send(f"**[ SYSTEM ]** Поздравляем! Вы достигли **{current_level} этажа** и получили статус **{highest_role_name}**.")
        except: pass

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
            lvl_embed = discord.Embed(title="[ ⚡ LEVEL UP ]", description=f"Прорыв на **{level} этаж**!", color=0x00BFFF)
            channel = getattr(interaction, 'channel', None)
            if channel:
                await channel.send(content=member.mention, embed=lvl_embed)

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

@bot.tree.command(name="maintenance", description="[АДМИН] Включить/выключить тех. работы")
async def maintenance(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user):
        return await interaction.response.send_message("❌ У вас нет прав!", ephemeral=True)
        
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    status = "🔴 ВКЛЮЧЕН (доступ закрыт)" if MAINTENANCE_MODE else "🟢 ВЫКЛЮЧЕН (бот работает)"
    embed = discord.Embed(title="🛠️ РЕЖИМ ТЕХОБСЛУЖИВАНИЯ", description=f"Статус: **{status}**", color=0xE74C3C if MAINTENANCE_MODE else 0x2ECC71)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- БАЗОВЫЕ КОМАНДЫ (MINIMAL) ---

@bot.tree.command(name="balance", description="Посмотреть баланс Колов")
async def balance(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(target.id)
    embed = discord.Embed(description=f"**Игрок:** {target.mention}\n**Баланс:** `{coins:,}` Колов <:col:1530575386457542817>", color=0x2B2D31)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="profile", description="Посмотреть игровой профиль")
async def profile(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    coins, xp, level, _, _, _, _, streak, guild_id, special_title = get_or_create_user(target.id)
    next_level_xp = int(35 * (level ** 1.85) + 80 * level + 40)
    
    embed = discord.Embed(title=f"Учетная запись: {target.display_name}", color=0x2B2D31)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Уровень", value=f"`Этаж {level}`", inline=True)
    embed.add_field(name="Баланс", value=f"`{coins:,}` Колов", inline=True)
    embed.add_field(name="Гильдия", value=f"`{guild_id if guild_id else 'Нет'}`", inline=True)
    embed.add_field(name="Титул", value=f"`{special_title}`", inline=True)
    embed.add_field(name="Стрик", value=f"`{streak} дн.`", inline=True)
    embed.add_field(name="Опыт", value=f"`{xp} / {next_level_xp} XP`", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="pay", description="Перевести Колы игроку")
async def pay(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0 or member.id == interaction.user.id: return await interaction.response.send_message("❌ Некорректная операция.", ephemeral=True)
    sender_coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    if sender_coins < amount: return await interaction.response.send_message("❌ Недостаточно средств.", ephemeral=True)

    get_or_create_user(member.id)
    users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -amount}})
    users_collection.update_one({"_id": member.id}, {"$inc": {"coins": amount}})

    embed = discord.Embed(description=f"✅ Переведено **{amount:,}** Колов игроку {member.mention}.", color=0x2ECC71)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="daily", description="Ежедневная награда")
async def daily(interaction: discord.Interaction):
    user_id = interaction.user.id
    current_time = time.time()
    coins, _, level, last_daily, _, _, _, streak, _, _ = get_or_create_user(user_id)

    if current_time - last_daily < 86400:
        left = int(86400 - (current_time - last_daily))
        return await interaction.response.send_message(f"⏳ Ожидайте {left // 3600}ч {(left % 3600) // 60}м.", ephemeral=True)

    if last_daily > 0 and current_time - last_daily > 172800: streak = 0
    streak += 1
    
    multiplier = 1.0 + (streak * 0.15)
    reward_coins = int((40 + (level * 8)) * multiplier)
    reward_xp = int((15 + (level * 3)) * multiplier)

    users_collection.update_one({"_id": user_id}, {"$inc": {"coins": reward_coins}, "$set": {"streak": streak, "last_daily": current_time}})
    await add_xp(interaction, user_id, reward_xp)

    embed = discord.Embed(description=f"🎁 Получено **{reward_coins:,}** Колов и **{reward_xp} XP**.\n🔥 Серия входов: `{streak} дн.`", color=0x2B2D31)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="work", description="Работа на этаже")
async def work(interaction: discord.Interaction):
    user_id = interaction.user.id
    current_time = time.time()
    _, _, level, _, last_work, _, _, _, _, _ = get_or_create_user(user_id)

    if current_time - last_work < 7200:
        left = int(7200 - (current_time - last_work))
        return await interaction.response.send_message(f"⏳ Отдых: {left // 3600}ч {(left % 3600) // 60}м.", ephemeral=True)

    earned = random.randint(40, 120) + (level * 2)
    users_collection.update_one({"_id": user_id}, {"$inc": {"coins": earned}, "$set": {"last_work": current_time}})
    await add_xp(interaction, user_id, random.randint(10, 20))

    embed = discord.Embed(description=f"🛠️ Работа принесла **{earned}** Колов.", color=0x2B2D31)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="crime", description="Криминальная авантюра")
async def crime(interaction: discord.Interaction):
  user_id = interaction.user.id
  current_time = time.time()
  coins, _, level, _, _, last_crime, _, _, _, _ = get_or_create_user(user_id)

  if current_time - last_crime < 14400:
      left = int(14400 - (current_time - last_crime))
      return await interaction.response.send_message(f"⏳ Опасно. Ожидайте {left // 3600}ч {(left % 3600) // 60}м.", ephemeral=True)

  success = random.choice([True, False])
  embed = discord.Embed(color=0x2B2D31)
  
  if success:
      reward = random.randint(50, 130) + (level * 2)
      users_collection.update_one({"_id": user_id}, {"$inc": {"coins": reward}, "$set": {"last_crime": current_time}})
      await add_xp(interaction, user_id, 15)
      embed.description = f"🥷 Успех! Добыто **{reward}** Колов."
  else:
      fine = random.randint(30, 70)
      new_coins = max(0, coins - fine)
      users_collection.update_one({"_id": user_id}, {"$set": {"coins": new_coins, "last_crime": current_time}})
      embed.description = f"🚨 Вас поймали. Штраф: **{fine}** Колов."
  await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rob", description="Ограбить игрока")
async def rob(interaction: discord.Interaction, member: discord.Member):
    attacker = interaction.user
    if member.id == attacker.id or member.bot: return await interaction.response.send_message("❌ Невозможно.", ephemeral=True)

    target_role_names = [r.name.lower() for r in member.roles]
    if "неприкасаемый" in target_role_names or "модератор" in target_role_names:
        return await interaction.response.send_message(f"🛡️ Цель под защитой.", ephemeral=True)

    current_time = time.time()
    att_coins, _, _, _, _, _, att_last_rob, _, _, _ = get_or_create_user(attacker.id)
    if current_time - att_last_rob < 10800:
        left = int(10800 - (current_time - att_last_rob))
        return await interaction.response.send_message(f"⏳ Кулдаун: {left // 3600}ч {(left % 3600) // 60}м.", ephemeral=True)

    target_coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(member.id)
    if target_coins < 500: return await interaction.response.send_message(f"❌ Цель слишком бедна.", ephemeral=True)
    if att_coins > 50000 and att_coins > target_coins * 5: return await interaction.response.send_message(f"❌ Ищите соперника побогаче.", ephemeral=True)

    potential_amount = max(20, random.randint(int(target_coins * 0.05), int(target_coins * 0.10)))
    if att_coins < potential_amount: return await interaction.response.send_message(f"❌ Нужно минимум **{potential_amount:,}** Колов для залога.", ephemeral=True)

    users_collection.update_one({"_id": attacker.id}, {"$set": {"last_rob": current_time}})

    embed_loading = discord.Embed(description=f"🕵️ Вы подкрадываетесь к {member.mention}...", color=0x2B2D31)
    await interaction.response.send_message(embed=embed_loading)
    await asyncio.sleep(3.0)

    success = random.choice([True, False])
    embed_res = discord.Embed(color=0x2B2D31)

    if success:
        embed_res.set_image(url="https://i.pinimg.com/originals/58/23/81/582381e4e65d4f6a027116695445d649.gif")
        users_collection.update_one({"_id": attacker.id}, {"$inc": {"coins": potential_amount}})
        users_collection.update_one({"_id": member.id}, {"$inc": {"coins": -potential_amount}})
        await add_xp(interaction, attacker.id, 20)
        embed_res.description = f"🎉 Успех! Украдено **{potential_amount:,}** Колов у {member.mention}."
    else:
        embed_res.set_image(url="https://media.tenor.com/LjXd-V-BrwIAAAAd/kazuma-run-kazuma-scared.gif")
        users_collection.update_one({"_id": attacker.id}, {"$inc": {"coins": -potential_amount}})
        embed_res.description = f"🚨 Вас поймали! Выплачен штраф **{potential_amount:,}** Колов."

    await interaction.edit_original_response(embed=embed_res)

# --- АЗАРТНЫЕ ИГРЫ (ДУЭЛЬ, КОСТИ, ОРЕЛ/РЕШКА, РУЛЕТКА) ---

class DuelAcceptView(discord.ui.View):
    def __init__(self, challenger: discord.Member, target: discord.Member, amount: int):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.target = target
        self.amount = amount

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.green, emoji="⚔️")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("❌ Это вызов не для вас!", ephemeral=True)

        for child in self.children: child.disabled = True
        await interaction.response.edit_message(view=self)

        c_coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(self.challenger.id)
        t_coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(self.target.id)

        if c_coins < self.amount or t_coins < self.amount:
            return await interaction.followup.send("❌ У одного из игроков не хватает средств на балансе!", ephemeral=True)

        embed_loading = discord.Embed(description=f"⚔️ **ДУЭЛЬ НАЧАЛАСЬ**\n{self.challenger.mention} vs {self.target.mention}\nСтавка: `{self.amount:,}`", color=0x2B2D31)
        embed_loading.set_image(url="https://media.tenor.com/fA7mD8B8O0QAAAAC/sword-art-online-kirito.gif")
        msg = await interaction.followup.send(embed=embed_loading)
        await asyncio.sleep(3.0)

        winner = random.choice([self.challenger, self.target])
        loser = self.target if winner == self.challenger else self.challenger

        users_collection.update_one({"_id": winner.id}, {"$inc": {"coins": self.amount}})
        users_collection.update_one({"_id": loser.id}, {"$inc": {"coins": -self.amount}})

        embed_res = discord.Embed(description=f"🏆 Победитель: {winner.mention}\nЗабирает куш: **{self.amount:,}** Колов.", color=0x2ECC71)
        await msg.edit(embed=embed_res)
        await add_xp(interaction, winner.id, 20)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="🏃")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("❌ Это вызов не для вас!", ephemeral=True)
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(embed=discord.Embed(description=f"❌ {self.target.mention} отказался от дуэли.", color=0x2B2D31), view=self)

@bot.tree.command(name="duel", description="Бросить вызов игроку (Мин. 50 Колов)")
async def duel(interaction: discord.Interaction, target: discord.Member, amount: int):
    if target.id == interaction.user.id or target.bot: return await interaction.response.send_message("❌ Ошибка цели.", ephemeral=True)
    if amount < 50: return await interaction.response.send_message("❌ Мин. ставка 50 Колов.", ephemeral=True)

    my_coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    target_coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(target.id)

    if my_coins < amount: return await interaction.response.send_message("❌ Недостаточно средств.", ephemeral=True)
    if target_coins < amount: return await interaction.response.send_message("❌ У противника нет таких денег.", ephemeral=True)

    embed = discord.Embed(description=f"⚔️ {interaction.user.mention} вызывает {target.mention} на дуэль!\n**Ставка:** `{amount:,}` Колов.", color=0x2B2D31)
    await interaction.response.send_message(content=target.mention, embed=embed, view=DuelAcceptView(interaction.user, target, amount))

@bot.tree.command(name="dice", description="Бросить кости против системы (Мин. 50)")
async def dice(interaction: discord.Interaction, amount: int):
  if not is_admin_or_mod(interaction.user) and amount < 50: return await interaction.response.send_message("❌ Мин. ставка 50 Колов.", ephemeral=True)
  coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
  if coins < amount: return await interaction.response.send_message("❌ Недостаточно средств.", ephemeral=True)

  users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -amount}})

  embed_loading = discord.Embed(description="🎲 Бросаем кости...", color=0x2B2D31)
  embed_loading.set_image(url="https://i.pinimg.com/originals/80/9f/ba/809fba531ccbb8e24010696ffa1503e2.gif")
  await interaction.response.send_message(embed=embed_loading)
  await asyncio.sleep(3.0)

  p_roll, b_roll = random.randint(1, 6), random.randint(1, 6)
  embed_res = discord.Embed(color=0x2B2D31)

  if p_roll > b_roll:
      users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": amount * 2}})
      embed_res.description = f"🎉 **Победа!** Вы: `🎲 {p_roll}`, Бот: `🤖 {b_roll}`.\nВыиграно: **{amount:,}** Колов."
  elif p_roll < b_roll:
      embed_res.description = f"💀 **Поражение.** Вы: `🎲 {p_roll}`, Бот: `🤖 {b_roll}`.\nПотеряно: **{amount:,}** Колов."
  else:
      users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": amount}})
      embed_res.description = f"🤝 **Ничья.** (`{p_roll}:{b_roll}`). Ставка возвращена."
  
  await interaction.edit_original_response(embed=embed_res)
  await add_xp(interaction, interaction.user.id, random.randint(5, 10))

@bot.tree.command(name="coinflip", description="Орел и решка (Мин. 50)")
@app_commands.choices(choice=[app_commands.Choice(name="Орел", value="орел"), app_commands.Choice(name="Решка", value="решка")])
async def coinflip(interaction: discord.Interaction, choice: str, amount: int):
  if not is_admin_or_mod(interaction.user) and amount < 50: return await interaction.response.send_message("❌ Мин. ставка 50 Колов.", ephemeral=True)
  coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
  if coins < amount: return await interaction.response.send_message("❌ Недостаточно средств.", ephemeral=True)

  users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -amount}})

  embed_loading = discord.Embed(description="🪙 Монетка подброшена...", color=0x2B2D31)
  embed_loading.set_image(url="https://media.tenor.com/9PALsSO_XpsAAAAC/misaka-mikoto.gif")
  await interaction.response.send_message(embed=embed_loading)
  await asyncio.sleep(3.0)

  result = random.choice(["орел", "решка"])
  embed_res = discord.Embed(color=0x2B2D31)
  
  if choice == result:
      users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": amount * 2}})
      embed_res.description = f"🎉 Выпал **{result.upper()}**! Вы выиграли **{amount:,}** Колов."
  else:
      embed_res.description = f"❌ Выпал **{result.upper()}**. Вы проиграли **{amount:,}** Колов."
      
  await interaction.edit_original_response(embed=embed_res)
  await add_xp(interaction, interaction.user.id, random.randint(5, 15))

@bot.tree.command(name="roulette", description="Русская рулетка (Мин. 50)")
async def roulette(interaction: discord.Interaction, amount: int):
  if not is_admin_or_mod(interaction.user) and amount < 50: return await interaction.response.send_message("❌ Мин. ставка 50 Колов.", ephemeral=True)
  coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
  if coins < amount: return await interaction.response.send_message("❌ Недостаточно средств.", ephemeral=True)

  users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -amount}})

  embed_loading = discord.Embed(description="🎯 Барабан вращается...", color=0x2B2D31)
  embed_loading.set_image(url="https://i.pinimg.com/originals/ac/56/c5/ac56c5c7e6037a698e22c9a30a8dccda.gif")
  await interaction.response.send_message(embed=embed_loading)
  await asyncio.sleep(3.0)

  shot = random.choice([True, False, False, False, False, False])
  embed_res = discord.Embed(color=0x2B2D31)
  
  if not shot:
      users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": amount * 2}})
      embed_res.description = f"💥 *ЩЕЛК!* Пусто. Вы выиграли **{amount:,}** Колов."
  else:
      embed_res.description = f"💀 *БАХ!* Вы проиграли **{amount:,}** Колов."
      
  await interaction.edit_original_response(embed=embed_res)
  await add_xp(interaction, interaction.user.id, random.randint(10, 20))


# --- МАГАЗИН И РОЛИ (MINIMAL) ---

class CustomRoleModal(discord.ui.Modal, title="Создание кастомной роли"):
    role_name = discord.ui.TextInput(label="Название роли", max_length=50)
    role_color = discord.ui.TextInput(label="HEX-цвет (без #)", max_length=6, min_length=6)
    def __init__(self, price: int):
        super().__init__()
        self.price = price
    async def on_submit(self, interaction: discord.Interaction):
        r_name = self.role_name.value.strip()
        if custom_roles_collection.find_one({"role_name": {"$regex": f"^{r_name}$", "$options": "i"}}) or discord.utils.get(interaction.guild.roles, name=r_name):
            return await interaction.response.send_message("❌ Роль уже существует!", ephemeral=True)
        try: color_int = int(self.role_color.value.strip(), 16)
        except: return await interaction.response.send_message("❌ Неверный HEX!", ephemeral=True)
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}})
        try:
            new_role = await interaction.guild.create_role(name=r_name, color=discord.Color(color_int))
            await interaction.user.add_roles(new_role)
            custom_roles_collection.insert_one({"role_id": new_role.id, "user_id": interaction.user.id, "role_name": r_name})
            await interaction.response.send_message(f"✅ Роль **{r_name}** создана!", ephemeral=True)
        except:
            users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": self.price}})
            await interaction.response.send_message("❌ Ошибка.", ephemeral=True)

class CustomTitleModal(discord.ui.Modal, title="Покупка титула"):
    title_text = discord.ui.TextInput(label="Текст титула", max_length=30)
    def __init__(self, price: int):
        super().__init__()
        self.price = price
    async def on_submit(self, interaction: discord.Interaction):
        t_text = self.title_text.value.strip()
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}, "$set": {"special_title": t_text}})
        titles_collection.insert_one({"user_id": interaction.user.id, "title_name": t_text})
        await interaction.response.send_message(f"👑 Титул **{t_text}** куплен!", ephemeral=True)

class ShopButtonsView(discord.ui.View):
    def __init__(self): super().__init__(timeout=60)
    @discord.ui.button(label="Неприкасаемый (15k)", style=discord.ButtonStyle.blurple)
    async def buy_untouchable(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = discord.utils.get(interaction.guild.roles, name="Неприкасаемый")
        if role and role in interaction.user.roles: return await interaction.response.send_message("❌ Уже куплено.", ephemeral=True)
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < 15000: return await interaction.response.send_message("❌ Нет денег.", ephemeral=True)
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -15000}})
        if role: await interaction.user.add_roles(role)
        await interaction.response.send_message("✅ Куплен статус 'Неприкасаемый'.", ephemeral=True)
    @discord.ui.button(label="Своя Роль (10k)", style=discord.ButtonStyle.green)
    async def buy_custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        if custom_roles_collection.count_documents({"user_id": interaction.user.id}) >= 2: return await interaction.response.send_message("❌ Максимум ролей.", ephemeral=True)
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < 10000: return await interaction.response.send_message("❌ Нет денег.", ephemeral=True)
        await interaction.response.send_modal(CustomRoleModal(10000))
    @discord.ui.button(label="Титул (5k)", style=discord.ButtonStyle.grey)
    async def buy_title(self, interaction: discord.Interaction, button: discord.ui.Button):
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < 5000: return await interaction.response.send_message("❌ Нет денег.", ephemeral=True)
        await interaction.response.send_modal(CustomTitleModal(5000))

@bot.tree.command(name="shop", description="Игровой магазин")
async def shop(interaction: discord.Interaction):
    embed = discord.Embed(title="[ 🛒 MARKET ]", description="**Товары Системы:**\n\n🛡️ **Неприкасаемый** — `15,000` (Иммунитет к грабежам)\n✨ **Кастомная роль** — `10,000` (Ваш цвет и имя)\n👑 **Титул** — `5,000` (Отображение в профиле)", color=0x2B2D31)
    await interaction.response.send_message(embed=embed, view=ShopButtonsView())

class EditRoleModal(discord.ui.Modal, title="Изменение роли"):
    role_name = discord.ui.TextInput(label="Новое название", max_length=50)
    role_color = discord.ui.TextInput(label="Новый HEX (без #)", max_length=6, min_length=6)
    def __init__(self, role, price):
        super().__init__()
        self.role = role
        self.price = price
    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.role_name.value.strip()
        if custom_roles_collection.find_one({"role_name": {"$regex": f"^{new_name}$", "$options": "i"}, "role_id": {"$ne": self.role.id}}) or discord.utils.get(interaction.guild.roles, name=new_name):
            return await interaction.response.send_message("❌ Имя занято!", ephemeral=True)
        try: color_int = int(self.role_color.value.strip(), 16)
        except: return await interaction.response.send_message("❌ Неверный HEX!", ephemeral=True)
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}})
        await self.role.edit(name=new_name, color=discord.Color(color_int))
        custom_roles_collection.update_one({"role_id": self.role.id}, {"$set": {"role_name": new_name}})
        auction_collection.update_one({"role_id": self.role.id}, {"$set": {"role_name": new_name}})
        await interaction.response.send_message("✅ Роль изменена!", ephemeral=True)

class EditRoleSelect(discord.ui.View):
    def __init__(self, roles_list, price):
        super().__init__(timeout=30)
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles_list]
        self.select = discord.ui.Select(placeholder="Выберите роль...", options=options)
        self.select.callback = self.cb
        self.add_item(self.select)
        self.price = price
    async def cb(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(int(self.select.values[0]))
        if not role: return await interaction.response.send_message("❌ Ошибка.", ephemeral=True)
        await interaction.response.send_modal(EditRoleModal(role, self.price))

@bot.tree.command(name="editrole", description="Изменить роль (3k Колов)")
async def editrole(interaction: discord.Interaction):
    coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    if coins < 3000: return await interaction.response.send_message("❌ Нужно 3,000.", ephemeral=True)
    rows = list(custom_roles_collection.find({"user_id": interaction.user.id}))
    user_roles = [interaction.guild.get_role(r["role_id"]) for r in rows if interaction.guild.get_role(r["role_id"])]
    if not user_roles: return await interaction.response.send_message("❌ Нет ролей.", ephemeral=True)
    await interaction.response.send_message(embed=discord.Embed(description="Выберите роль:", color=0x2B2D31), view=EditRoleSelect(user_roles, 3000), ephemeral=True)

class DeleteRoleSelect(discord.ui.View):
    def __init__(self, roles_list, price):
        super().__init__(timeout=30)
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles_list]
        self.select = discord.ui.Select(placeholder="Удалить роль...", options=options)
        self.select.callback = self.cb
        self.add_item(self.select)
        self.price = price
    async def cb(self, interaction: discord.Interaction):
        role_id = int(self.select.values[0])
        role = interaction.guild.get_role(role_id)
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -self.price}})
        custom_roles_collection.delete_one({"role_id": role_id})
        auction_collection.delete_one({"role_id": role_id})
        if role: 
            try: await role.delete()
            except: pass
        await interaction.response.send_message("🗑️ Роль удалена.", ephemeral=True)

@bot.tree.command(name="deleterole", description="Удалить роль (5k Колов)")
async def deleterole(interaction: discord.Interaction):
    coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
    if coins < 5000: return await interaction.response.send_message("❌ Нужно 5,000.", ephemeral=True)
    rows = list(custom_roles_collection.find({"user_id": interaction.user.id}))
    user_roles = [interaction.guild.get_role(r["role_id"]) for r in rows if interaction.guild.get_role(r["role_id"])]
    if not user_roles: return await interaction.response.send_message("❌ Нет ролей.", ephemeral=True)
    await interaction.response.send_message(embed=discord.Embed(description="Выберите роль:", color=0x2B2D31), view=DeleteRoleSelect(user_roles, 5000), ephemeral=True)

class SetTitleSelect(discord.ui.View):
    def __init__(self, titles_list):
        super().__init__(timeout=30)
        options = [discord.SelectOption(label=t, value=t) for t in titles_list]
        self.select = discord.ui.Select(placeholder="Выберите титул...", options=options)
        self.select.callback = self.cb
        self.add_item(self.select)
    async def cb(self, interaction: discord.Interaction):
        t = self.select.values[0]
        users_collection.update_one({"_id": interaction.user.id}, {"$set": {"special_title": t}})
        await interaction.response.send_message(f"✅ Титул изменен на: **{t}**", ephemeral=True)

@bot.tree.command(name="settitle", description="Выбрать активный титул")
async def settitle(interaction: discord.Interaction):
    rows = list(titles_collection.find({"user_id": interaction.user.id}))
    if not rows: return await interaction.response.send_message("❌ Нет титулов.", ephemeral=True)
    titles = [r["title_name"] for r in rows]
    await interaction.response.send_message(embed=discord.Embed(description="Выбор титула:", color=0x2B2D31), view=SetTitleSelect(titles), ephemeral=True)

@bot.tree.command(name="leaderboard", description="Топ-10 игроков")
async def leaderboard(interaction: discord.Interaction):
    top = list(users_collection.find().sort([("level", -1), ("coins", -1)]).limit(10))
    embed = discord.Embed(title="[ 🏆 ТОП-10 ]", color=0x2B2D31)
    embed.description = "\n".join([f"`#{i}` <@{u['_id']}> — **{u.get('level', 1)} этаж** | {u.get('coins', 0):,} Колов" for i, u in enumerate(top, 1)]) if top else "Пусто"
    await interaction.response.send_message(embed=embed)


# --- ГИЛЬДИИ С УПРАВЛЕНИЕМ ---

class GuildDepositModal(discord.ui.Modal, title="Пополнение казны"):
    amount = discord.ui.TextInput(label="Сумма (Колы)", placeholder="1000")
    def __init__(self, guild_name):
        super().__init__()
        self.guild_name = guild_name
    async def on_submit(self, interaction: discord.Interaction):
        try: val = int(self.amount.value)
        except: return await interaction.response.send_message("❌ Ошибка ввода.", ephemeral=True)
        if val <= 0: return await interaction.response.send_message("❌ Сумма > 0.", ephemeral=True)
        
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if coins < val: return await interaction.response.send_message("❌ Нет средств.", ephemeral=True)
        
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -val}})
        guilds_collection.update_one({"guild_name": self.guild_name}, {"$inc": {"bank": val}})
        await interaction.response.send_message(f"✅ Внесено **{val:,}** в казну гильдии.", ephemeral=True)

class GuildLeaderView(discord.ui.View):
    def __init__(self, guild_name):
        super().__init__(timeout=60)
        self.guild_name = guild_name
    @discord.ui.button(label="Закрыть/Открыть набор", style=discord.ButtonStyle.blurple)
    async def toggle_private(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = guilds_collection.find_one({"guild_name": self.guild_name})
        new_status = not g.get("is_private", False)
        guilds_collection.update_one({"guild_name": self.guild_name}, {"$set": {"is_private": new_status}})
        status_str = "🔒 ЗАКРЫТА" if new_status else "🔓 ОТКРЫТА"
        await interaction.response.send_message(f"Гильдия теперь {status_str}.", ephemeral=True)

class GuildCreateModal(discord.ui.Modal, title="Создание гильдии"):
    guild_name = discord.ui.TextInput(label="Название", max_length=30)
    async def on_submit(self, interaction: discord.Interaction):
        uid = interaction.user.id
        name = self.guild_name.value.strip()
        _, _, _, _, _, _, _, _, user_g, _ = get_or_create_user(uid)
        if user_g: return await interaction.response.send_message("❌ Вы в гильдии.", ephemeral=True)
        coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(uid)
        if coins < 25000: return await interaction.response.send_message("❌ Нужно 25,000 Колов.", ephemeral=True)
        if guilds_collection.find_one({"guild_name": name}): return await interaction.response.send_message("❌ Имя занято.", ephemeral=True)
        users_collection.update_one({"_id": uid}, {"$inc": {"coins": -25000}, "$set": {"guild_id": name}})
        guilds_collection.insert_one({"guild_name": name, "leader_id": uid, "bank": 0, "level": 1, "is_private": False})
        await interaction.response.send_message(f"✅ Гильдия **{name}** создана.", ephemeral=True)

class GuildMainView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.user_id = user_id
        _, _, _, _, _, _, _, _, self.g_name, _ = get_or_create_user(user_id)

    @discord.ui.button(label="Создать (25k)", style=discord.ButtonStyle.green, emoji="🏰")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuildCreateModal())

    @discord.ui.button(label="Информация", style=discord.ButtonStyle.blurple, emoji="ℹ️")
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.g_name: return await interaction.response.send_message("❌ Вы не в гильдии.", ephemeral=True)
        g_data = guilds_collection.find_one({"guild_name": self.g_name})
        members = list(users_collection.find({"guild_id": self.g_name}))
        embed = discord.Embed(title=f"Гильдия: {self.g_name}", color=0x2B2D31)
        embed.add_field(name="Лидер", value=f"<@{g_data['leader_id']}>", inline=True)
        embed.add_field(name="Казна", value=f"`{g_data.get('bank', 0):,}` Колов", inline=True)
        embed.add_field(name="Статус", value="🔒 Закрытая" if g_data.get("is_private") else "🔓 Открытая", inline=True)
        embed.add_field(name=f"Участники ({len(members)})", value=", ".join([f"<@{m['_id']}>" for m in members]) or "Пусто", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Пополнить казну", style=discord.ButtonStyle.grey, emoji="💰")
    async def deposit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.g_name: return await interaction.response.send_message("❌ Вы не в гильдии.", ephemeral=True)
        await interaction.response.send_modal(GuildDepositModal(self.g_name))

    @discord.ui.button(label="Управление (Лидер)", style=discord.ButtonStyle.grey, emoji="⚙️")
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.g_name: return await interaction.response.send_message("❌ Вы не в гильдии.", ephemeral=True)
        g_data = guilds_collection.find_one({"guild_name": self.g_name})
        if g_data['leader_id'] != interaction.user.id: return await interaction.response.send_message("❌ Только для лидера.", ephemeral=True)
        await interaction.response.send_message("Панель лидера:", view=GuildLeaderView(self.g_name), ephemeral=True)

    @discord.ui.button(label="Покинуть", style=discord.ButtonStyle.red, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.g_name: return await interaction.response.send_message("❌ Вы не в гильдии.", ephemeral=True)
        g_data = guilds_collection.find_one({"guild_name": self.g_name})
        users_collection.update_one({"_id": interaction.user.id}, {"$set": {"guild_id": None}})
        if g_data['leader_id'] == interaction.user.id:
            new_member = users_collection.find_one({"guild_id": self.g_name})
            if new_member: guilds_collection.update_one({"guild_name": self.g_name}, {"$set": {"leader_id": new_member["_id"]}})
            else: guilds_collection.delete_one({"guild_name": self.g_name})
        await interaction.response.send_message("🚪 Вы вышли из гильдии.", ephemeral=True)

@bot.tree.command(name="guild", description="Меню гильдии")
async def guild_menu(interaction: discord.Interaction):
    embed = discord.Embed(description="**[ СИСТЕМА ГИЛЬДИЙ ]**\nУправление вашим кланом.", color=0x2B2D31)
    await interaction.response.send_message(embed=embed, view=GuildMainView(interaction.user.id))

# --- АУКЦИОН (ЕДИНОЕ МЕНЮ) ---

class SellRoleModal(discord.ui.Modal, title="Продать роль"):
    price_input = discord.ui.TextInput(label="Цена в Колах", placeholder="5000", max_length=10)
    def __init__(self, role_id, role_name):
        super().__init__()
        self.role_id = role_id
        self.role_name = role_name
    async def on_submit(self, interaction: discord.Interaction):
        try: price = int(self.price_input.value)
        except: return await interaction.response.send_message("❌ Ошибка формата.", ephemeral=True)
        if price <= 0: return await interaction.response.send_message("❌ Сумма > 0.", ephemeral=True)
        last_item = auction_collection.find_one(sort=[("sale_id", -1)])
        next_id = (last_item["sale_id"] + 1) if last_item else 1
        auction_collection.insert_one({"sale_id": next_id, "role_id": self.role_id, "seller_id": interaction.user.id, "price": price, "role_name": self.role_name})
        role = interaction.guild.get_role(self.role_id)
        if role: 
            try: await interaction.user.remove_roles(role)
            except: pass
        await interaction.response.send_message(f"✅ Роль выставлена за **{price:,}** Колов.", ephemeral=True)

class SellRoleSelect(discord.ui.View):
    def __init__(self, roles_list):
        super().__init__(timeout=30)
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles_list]
        self.select = discord.ui.Select(placeholder="Выберите роль...", options=options)
        self.select.callback = self.cb
        self.add_item(self.select)
    async def cb(self, interaction: discord.Interaction):
        r_id = int(self.select.values[0])
        r = interaction.guild.get_role(r_id)
        if not r: return await interaction.response.send_message("❌ Ошибка.", ephemeral=True)
        await interaction.response.send_modal(SellRoleModal(r_id, r.name))

class AuctionBuySelect(discord.ui.View):
    def __init__(self, items_slice):
        super().__init__(timeout=60)
        options = [discord.SelectOption(label=i["role_name"], description=f"{i['price']} Колов", value=str(i["sale_id"])) for i in items_slice]
        self.select = discord.ui.Select(placeholder="Купить лот с этой страницы...", options=options)
        self.select.callback = self.buy_callback
        self.add_item(self.select)
    async def buy_callback(self, interaction: discord.Interaction):
        sale_id = int(self.select.values[0])
        item = auction_collection.find_one({"sale_id": sale_id})
        if not item: return await interaction.response.send_message("❌ Лот продан.", ephemeral=True)
        if interaction.user.id == item["seller_id"]: return await interaction.response.send_message("❌ Это ваш лот.", ephemeral=True)
        buyer_coins, _, _, _, _, _, _, _, _, _ = get_or_create_user(interaction.user.id)
        if buyer_coins < item["price"]: return await interaction.response.send_message("❌ Нет средств.", ephemeral=True)
        users_collection.update_one({"_id": interaction.user.id}, {"$inc": {"coins": -item["price"]}})
        users_collection.update_one({"_id": item["seller_id"]}, {"$inc": {"coins": item["price"]}})
        custom_roles_collection.update_one({"role_id": item["role_id"]}, {"$set": {"user_id": interaction.user.id}})
        auction_collection.delete_one({"sale_id": sale_id})
        role = interaction.guild.get_role(item["role_id"])
        if role: 
            try: await interaction.user.add_roles(role)
            except: pass
        await interaction.response.send_message(f"✅ Вы купили **{item['role_name']}**!", ephemeral=True)

class AuctionPagingView(discord.ui.View):
    def __init__(self, items):
        super().__init__(timeout=60)
        self.items = items
        self.page = 0
        self.per_page = 5  
        self.update_buttons()

    def update_buttons(self):
        max_pages = max(0, (len(self.items) - 1) // self.per_page)
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= max_pages

    def get_current_embed(self):
        embed = discord.Embed(title="[ 🏛️ АУКЦИОН ]", color=0x2B2D31)
        start = self.page * self.per_page
        current_slice = self.items[start:start + self.per_page]
        for item in current_slice:
            embed.add_field(name=f"🏷️ {item['role_name']}", value=f"`{item['price']:,}` Колов | Продавец: <@{item['seller_id']}>", inline=False)
        embed.set_footer(text=f"Страница {self.page + 1}")
        return embed, current_slice

    async def update_view(self, interaction):
        self.update_buttons()
        self.clear_items()
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        embed, slice = self.get_current_embed()
        if slice:
            buy_select = AuctionBuySelect(slice).select
            self.add_item(buy_select)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.grey, emoji="⬅️")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        await self.update_view(interaction)

    @discord.ui.button(label="Вперед", style=discord.ButtonStyle.grey, emoji="➡️")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        await self.update_view(interaction)

class AuctionMainView(discord.ui.View):
    def __init__(self): super().__init__(timeout=60)
    @discord.ui.button(label="Смотреть лоты", style=discord.ButtonStyle.blurple, emoji="🛒")
    async def browse(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = list(auction_collection.find())
        if not items: return await interaction.response.send_message("📦 Пусто.", ephemeral=True)
        view = AuctionPagingView(items)
        embed, slice = view.get_current_embed()
        if slice: view.add_item(AuctionBuySelect(slice).select)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    @discord.ui.button(label="Продать роль", style=discord.ButtonStyle.green, emoji="🏷️")
    async def sell(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = list(custom_roles_collection.find({"user_id": interaction.user.id}))
        user_roles = [interaction.guild.get_role(r["role_id"]) for r in rows if interaction.guild.get_role(r["role_id"])]
        if not user_roles: return await interaction.response.send_message("❌ У вас нет ролей.", ephemeral=True)
        await interaction.response.send_message("Выберите роль для продажи:", view=SellRoleSelect(user_roles), ephemeral=True)

@bot.tree.command(name="auction", description="Рынок кастомных ролей")
async def auction(interaction: discord.Interaction):
    embed = discord.Embed(description="**[ ТОРГОВАЯ ПЛОЩАДКА ]**\nЗдесь можно купить или продать кастомные роли.", color=0x2B2D31)
    await interaction.response.send_message(embed=embed, view=AuctionMainView())

# --- АДМИНСКИЕ КОМАНДЫ (ЧИТЫ) ---

@bot.tree.command(name="setlevel", description="[АДМИН] Установить этаж игроку")
async def setlevel(interaction: discord.Interaction, member: discord.Member, level: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_or_create_user(member.id)
    users_collection.update_one({"_id": member.id}, {"$set": {"level": level, "xp": 0}})
    await check_level_roles(member, level)
    await interaction.response.send_message(f"✅ Установлен {level} этаж для {member.mention}.", ephemeral=True)

@bot.tree.command(name="setxp", description="[АДМИН] Установить точное количество опыта (XP)")
async def setxp(interaction: discord.Interaction, member: discord.Member, xp: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_or_create_user(member.id)
    users_collection.update_one({"_id": member.id}, {"$set": {"xp": xp}})
    await interaction.response.send_message(f"✅ Установлено `{xp:,} XP` для {member.mention}.", ephemeral=True)

@bot.tree.command(name="givexp", description="[АДМИН] Выдать опыт (XP) игроку")
async def givexp(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_or_create_user(member.id)
    await add_xp(interaction, member.id, amount)
    await interaction.response.send_message(f"✅ Выдано `{amount:,} XP` для {member.mention}.", ephemeral=True)

@bot.tree.command(name="setcoins", description="[АДМИН] Установить точный баланс Колов")
async def setcoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_or_create_user(member.id)
    users_collection.update_one({"_id": member.id}, {"$set": {"coins": amount}})
    await interaction.response.send_message(f"✅ Баланс {member.mention} изменен на `{amount:,}`.", ephemeral=True)

@bot.tree.command(name="givecoins", description="[АДМИН] Выдать Колы игроку")
async def givecoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_or_create_user(member.id)
    users_collection.update_one({"_id": member.id}, {"$inc": {"coins": amount}})
    await interaction.response.send_message(f"✅ Выдано `{amount:,}` Колов для {member.mention}.", ephemeral=True)

@bot.tree.command(name="takecoins", description="[АДМИН] Забрать Колы у игрока")
async def takecoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_or_create_user(member.id)
    users_collection.update_one({"_id": member.id}, {"$inc": {"coins": -amount}})
    await interaction.response.send_message(f"🔻 Списано `{amount:,}` Колов у {member.mention}.", ephemeral=True)

@bot.tree.command(name="setstreak", description="[АДМИН] Установить текущий стрик входов")
async def setstreak(interaction: discord.Interaction, member: discord.Member, days: int):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    get_or_create_user(member.id)
    users_collection.update_one({"_id": member.id}, {"$set": {"streak": days}})
    await interaction.response.send_message(f"✅ Установлен стрик `{days} дн.` для {member.mention}.", ephemeral=True)

@bot.tree.command(name="resetcd", description="[АДМИН] Сбросить все кулдауны")
async def resetcd(interaction: discord.Interaction, member: discord.Member = None):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    target = member or interaction.user
    users_collection.update_one({"_id": target.id}, {"$set": {"last_daily": 0.0, "last_work": 0.0, "last_crime": 0.0, "last_rob": 0.0}})
    await interaction.response.send_message(f"⚡ Кулдауны для {target.mention} сброшены!", ephemeral=True)

@bot.tree.command(name="resetuser", description="[АДМИН] Полностью сбросить профиль игрока")
async def resetuser(interaction: discord.Interaction, member: discord.Member):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    users_collection.delete_one({"_id": member.id})
    get_or_create_user(member.id)
    await interaction.response.send_message(f"☢️ Профиль {member.mention} сброшен!", ephemeral=True)

@bot.tree.command(name="resetdb", description="[АДМИН] Полная глобальная очистка БД")
async def resetdb(interaction: discord.Interaction):
    if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
    users_collection.delete_many({})
    guilds_collection.delete_many({})
    custom_roles_collection.delete_many({})
    titles_collection.delete_many({})
    auction_collection.delete_many({})
    await interaction.response.send_message("☢️ База данных полностью очищена!", ephemeral=True)

# --- СИСТЕМА ИДЕЙ И ПРЕДЛОЖЕНИЙ ---
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
        public_embed = discord.Embed(description=f"**Идея:**\n{self.idea_text}\n\n**Прислал:**\n{self.author.mention}", color=0x2B2D31)
        public_embed.set_thumbnail(url=self.author.display_avatar.url)
        msg = await public_channel.send(embed=public_embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
        
        admin_embed = interaction.message.embeds[0]
        admin_embed.color = 0x2ECC71
        admin_embed.title = "✅ ОДОБРЕНО"
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(embed=admin_embed, view=self)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin_or_mod(interaction.user): return await interaction.response.send_message("❌ Нет прав!", ephemeral=True)
        admin_embed = interaction.message.embeds[0]
        admin_embed.color = 0xE74C3C
        admin_embed.title = "❌ ОТКЛОНЕНО"
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(embed=admin_embed, view=self)

class IdeaModal(discord.ui.Modal, title="Предложить идею"):
    idea_text = discord.ui.TextInput(label="Суть идеи", style=discord.TextStyle.paragraph, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        admin_channel = interaction.guild.get_channel(ADMIN_IDEA_CHANNEL_ID)
        embed = discord.Embed(title="⏳ Проверка", description=f"От: {interaction.user.mention}\n\n{self.idea_text.value}", color=0xF1C40F)
        await admin_channel.send(embed=embed, view=AdminIdeaView(interaction.user, self.idea_text.value))
        await interaction.response.send_message("✅ Идея отправлена модераторам.", ephemeral=True)

@bot.tree.command(name="idea", description="Предложить идею")
async def idea(interaction: discord.Interaction):
    await interaction.response.send_modal(IdeaModal())

keep_alive()
bot.run(os.getenv("TOKEN"))
