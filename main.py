import sys
if sys.version_info >= (3, 13):
    try:
        import audioop
    except ImportError:
        import types
        audioop = types.ModuleType('audioop')
        def dummy_func(*args, **kwargs):
            raise RuntimeError("Audio operations not supported in Python 3.13+")
        for func_name in ['lin2lin', 'ratecv', 'tomono', 'tostereo', 'mul', 'add', 'bias', 'reverse', 'minmax', 'max', 'maxpp', 'avg', 'avgpp', 'rms', 'findfit', 'findmax', 'cross', 'findfactor', 'getsample', 'adpcm2lin', 'lin2adpcm', 'lin2alaw', 'alaw2lin', 'lin2ulaw', 'ulaw2lin']:
            setattr(audioop, func_name, dummy_func)
        sys.modules['audioop'] = audioop

import discord
from discord.ext import commands, tasks
from discord import app_commands, Embed
import aiohttp
import asyncio
import os
import json
import logging
from datetime import datetime, UTC, timedelta
import threading
from aiohttp import web
import pytz
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
import time
import signal
import urllib.parse

region_mapping = {
    "euw": "euw1",
    "eune": "eun1", 
    "na": "na1",
    "kr": "kr",
    "jp": "jp1",
    "br": "br1",
    "lan": "la1",
    "las": "la2",
    "oce": "oc1",
    "tr": "tr1",
    "ru": "ru"
}

queue_mapping = {
    420: "Ranked Solo",
    440: "Ranked Flex",
    400: "Normal Draft",
    430: "Normal Blind",
    450: "ARAM",
    900: "URF",
    1020: "One for All",
    1700: "Arena",
    1400: "Ultimate Spellbook"
}

reverse_region_mapping = {v: k for k, v in region_mapping.items()}

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents)

TIMEZONE = pytz.timezone('Europe/Paris')
def get_current_time(): return datetime.now(TIMEZONE)
def parse_date(date_str):
    try:
        naive = datetime.strptime(date_str, "%d/%m/%Y %H:%M")
        return TIMEZONE.localize(naive)
    except: return None

def format_date(date):
    months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    days = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    return f"{days[date.weekday()]} {date.day} {months[date.month - 1]} {date.year} à {date.strftime('%H:%M')}"

bot_start_time = time.time()

web_runner = None
web_site = None

async def start_web_server():
    global web_runner, web_site
    
    try:
        async def health_check(request):
            bot_status = "CONNECTÉ" if bot.is_ready() else "DÉCONNECTÉ"
            notif_status = "ACTIF" if notification_system.is_running() else "ARRÊTÉ"
            twitch_status = "ACTIF" if check_streams.is_running() else "ARRÊTÉ"
            watch_status = "ACTIF" if game_watcher.is_running() else "ARRÊTÉ"
            
            return web.Response(
                text=(
                    f"Bot Discord Unifié - Status: {bot_status}\n"
                    f"Notifications: {notif_status}\n"
                    f"Twitch: {twitch_status}\n"
                    f"LoL Watcher: {watch_status}\n"
                    f"Storage: {stream_manager.backend()}\n"
                    f"Uptime: {int(time.time() - bot_start_time)}s\n"
                    f"{datetime.now(TIMEZONE).strftime('%d/%m/%Y %H:%M:%S')} (Paris)\n"
                    f"{len(events)} événements, {sum(len(s) for s in streamers.values())} streamers, {sum(len(p) for p in watched_players.values())} joueurs LoL"
                ),
                status=200,
                headers={'Content-Type': 'text/plain; charset=utf-8'}
            )
        
        async def health_json(request):
            health_data = {
                "uptime_seconds": int(time.time() - bot_start_time),
                "storage": stream_manager.backend(),
                "status": "healthy" if bot.is_ready() else "degraded",
                "timestamp": datetime.now(TIMEZONE).isoformat(),
                "bot": {
                    "connected": bot.is_ready(),
                    "latency_ms": round(bot.latency * 1000) if bot.is_ready() else None,
                    "guilds": len(bot.guilds) if bot.is_ready() else 0
                },
                "services": {
                    "notifications_running": notification_system.is_running(),
                    "twitch_running": check_streams.is_running(),
                    "lol_watcher_running": game_watcher.is_running(),
                    "events_count": len(events),
                    "streamers_count": sum(len(s) for s in streamers.values()),
                    "lol_players_count": sum(len(p) for p in watched_players.values())
                }
            }
            return web.json_response(health_data)
        
        app = web.Application()
        app.router.add_get('/', health_check)
        app.router.add_get('/health', health_check)
        app.router.add_get('/health.json', health_json)
        app.router.add_get('/ping', lambda request: web.Response(text="pong"))
        
        port = int(os.getenv('PORT', 8080))
        host = '0.0.0.0'
        
        web_runner = web.AppRunner(app)
        await web_runner.setup()
        web_site = web.TCPSite(web_runner, host, port)
        await web_site.start()
        
        print(f"Serveur web démarré: http://{host}:{port}")
        return True
        
    except Exception as e:
        print(f"Erreur dans notification_system: {e}")
        import traceback
        traceback.print_exc()

async def send_event_notification(event, minutes_before):
    try:
        channel = bot.get_channel(event.channel_id)
        if not channel: 
            return None
        
        embed = create_notification_embed(event, minutes_before)
        
        content = ""
        if event.role_id:
            role = channel.guild.get_role(event.role_id)
            if role:
                content = role.mention
        
        sent_message = await channel.send(content=content, embed=embed)
        return sent_message
    except Exception as e:
        print(f"Erreur lors de l'envoi de notification: {e}")
        return None

async def delete_event_message(event_id):
    try:
        if event_id in events:
            del events[event_id]
        if event_id in notifications_sent:
            del notifications_sent[event_id]
        if event_id in notification_messages:
            del notification_messages[event_id]
    except Exception as e:
        print(f"Erreur lors du nettoyage: {e}")

@notification_system.before_loop
async def before_notification_system():
    await bot.wait_until_ready()

watched_players = {}
champion_cache = {}
latest_version = None

ADMIN_IDS = [123456789012345678, 987654321098765432]
WATCH_PING_ROLE_ID = None

def admin_only():
    def predicate(ctx):
        return ctx.author.id in ADMIN_IDS
    return commands.check(predicate)

class RiotAPI:
    def __init__(self, api_key):
        self.api_key = api_key

    async def request(self, url):
        try:
            if '\n' in url or '\r' in url:
                print(f"URL dangereuse détectée: {repr(url)}")
                return {"status": {"status_code": 400, "message": "URL invalide"}}
            
            headers = {
                "X-Riot-Token": str(self.api_key).strip(),
                "User-Agent": "Mozilla/5.0 (Discord Bot)"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    print(f"API Request: {url}")
                    print(f"Status Code: {response.status}")
                    
                    if response.status == 403:
                        print("Clé API invalide ou expirée!")
                        return {"status": {"status_code": 403, "message": "Clé API invalide"}}
                    elif response.status == 404:
                        print("Ressource non trouvée")
                        return {"status": {"status_code": 404, "message": "Joueur non trouvé"}}
                    elif response.status == 429:
                        print("Limite de taux dépassée")
                        return {"status": {"status_code": 429, "message": "Trop de requêtes"}}
                    elif response.status != 200:
                        print(f"Erreur HTTP {response.status}")
                        return {"status": {"status_code": response.status, "message": f"Erreur HTTP {response.status}"}}
                    
                    data = await response.json()
                    return data
        except Exception as e:
            print(f"Erreur de requête: {e}")
            return {"status": {"status_code": 500, "message": f"Erreur réseau: {str(e)}"}}

    async def get_latest_version(self):
        global latest_version
        if latest_version:
            return latest_version
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://ddragon.leagueoflegends.com/api/versions.json") as response:
                    if response.status == 200:
                        versions = await response.json()
                        latest_version = versions[0]
                        print(f"Version LoL détectée: {latest_version}")
                        return latest_version
                    return "14.24.1"
        except Exception as e:
            print(f"Error getting latest version: {e}")
            return "14.24.1"

    async def get_champion_data(self):
        global champion_cache
        
        if champion_cache:
            return champion_cache
        
        try:
            version = await self.get_latest_version()
            url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json"
            print(f"Récupération champions version {version}")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        champion_cache = data.get("data", {})
                        print(f"{len(champion_cache)} champions chargés en cache")
                        return champion_cache
                    return {}
        except Exception as e:
            print(f"Error getting champion data: {e}")
            return {}

    async def find_champion_by_id(self, champion_id):
        if champion_id == 0:
            return {
                "name": "Inconnu",
                "id": "Unknown",
                "icon_url": "https://ddragon.leagueoflegends.com/cdn/14.24.1/img/profileicon/29.png"
            }
        
        champion_data = await self.get_champion_data()
        version = await self.get_latest_version()
        
        for champion_key, champion_info in champion_data.items():
            if int(champion_info.get("key", 0)) == champion_id:
                return {
                    "name": champion_info.get("name", "Champion Inconnu"),
                    "id": champion_info.get("id", "Unknown"),
                    "icon_url": f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champion_info.get('id', 'Unknown')}.png"
                }
        
        static_champions = {
            887: {"name": "Briar", "id": "Briar"},
            895: {"name": "Naafiri", "id": "Naafiri"},
            950: {"name": "Smolder", "id": "Smolder"},
            901: {"name": "Aurora", "id": "Aurora"}
        }
        
        if champion_id in static_champions:
            champ_info = static_champions[champion_id]
            return {
                "name": champ_info["name"],
                "id": champ_info["id"],
                "icon_url": f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champ_info['id']}.png"
            }
        
        return {
            "name": f"Champion #{champion_id}",
            "id": "Unknown",
            "icon_url": f"https://ddragon.leagueoflegends.com/cdn/{version}/img/profileicon/29.png"
        }
        
    def validate_player_input(self, gamename_with_tag):
        if not gamename_with_tag or "#" not in gamename_with_tag:
            return None, None, "Format incorrect. Utilisez: NomJoueur#TAG"
        
        try:
            parts = gamename_with_tag.strip().split("#")
            if len(parts) != 2:
                return None, None, "Format incorrect. Un seul # autorisé."
            
            gamename = parts[0].strip()
            tagline = parts[1].strip()
            
            if not gamename or not tagline:
                return None, None, "Le nom et le tag ne peuvent pas être vides."
            
            if len(gamename) > 16 or len(tagline) > 5:
                return None, None, "Nom trop long (max 16 caractères) ou tag trop long (max 5)."
            
            forbidden_chars = ['\n', '\r', '\t', '\0']
            for char in forbidden_chars:
                if char in gamename or char in tagline:
                    return None, None, "Caractères interdits détectés."
            
            return gamename, tagline, None
            
        except Exception as e:
            return None, None, f"Erreur de validation: {str(e)}"

    async def get_summoner_by_riot_id(self, gameName, tagLine):
        gameName = urllib.parse.quote(str(gameName).strip().replace('\n', '').replace('\r', ''), safe='')
        tagLine = urllib.parse.quote(str(tagLine).strip().replace('\n', '').replace('\r', ''), safe='')
        
        if not gameName or not tagLine:
            return {"status": {"status_code": 400, "message": "Nom de joueur ou tag invalide"}}
        
        url = f"https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}"
        return await self.request(url)

    async def get_summoner_by_puuid(self, encryptedPUUID, region):
        url = f"https://{region}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{encryptedPUUID}"
        return await self.request(url)

    async def get_league_by_summoner(self, encryptedSummonerId, region):
        url = f"https://{region}.api.riotgames.com/lol/league/v4/entries/by-summoner/{encryptedSummonerId}"
        return await self.request(url)

    async def get_match_history(self, encryptedPUUID, start=0, count=10):
        url = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{encryptedPUUID}/ids?start={start}&count={count}&queue=420"
        return await self.request(url)
    
    async def get_match_history_all_queues(self, encryptedPUUID, start=0, count=10):
        url = f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{encryptedPUUID}/ids?start={start}&count={count}"
        return await self.request(url)

    async def get_match(self, matchId):
        url = f"https://europe.api.riotgames.com/lol/match/v5/matches/{matchId}"
        return await self.request(url)

    async def get_live_game(self, encryptedPUUID, region):
        url = f"https://{region}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{encryptedPUUID}"
        return await self.request(url)

    async def get_free_champion_rotation(self):
        url = f"https://euw1.api.riotgames.com/lol/platform/v3/champion-rotations"
        return await self.request(url)

getSummoner = RiotAPI(os.getenv("RIOT_API_KEY"))

async def delete_messages_after_delay(ctx, bot_message, delay_minutes=3):
    await asyncio.sleep(delay_minutes * 60)
    try:
        if ctx:
            await ctx.message.delete()
        if bot_message:
            await bot_message.delete()
    except discord.NotFound:
        pass
    except discord.Forbidden:
        pass

@bot.tree.command(name="event-create", description="Créer un événement")
@app_commands.describe(
    nom="Nom de l'événement",
    date="Date et heure (format: DD/MM/YYYY HH:MM)",
    category="Catégorie de l'événement",
    role="Rôle à mentionner pour les notifications (optionnel - auto si catégorie configurée)",
    stream="Lien du stream (optionnel)",
    lieu="Lieu de l'événement (optionnel)",
    image="URL complète de l'image (doit commencer par http:// ou https://)",
    description="Description de l'événement (optionnel)"
)
@app_commands.choices(category=[
    app_commands.Choice(name="LEC", value="lec"),
    app_commands.Choice(name="LFL", value="lfl"),
    app_commands.Choice(name="Rocket League", value="rl"),
    app_commands.Choice(name="Rainbow Six", value="r6"),
    app_commands.Choice(name="Échecs", value="chess"),
    app_commands.Choice(name="Autre", value="autre")
])
async def create_event(
    interaction: discord.Interaction,
    nom: str,
    date: str,
    category: str,
    role: Optional[discord.Role] = None,
    stream: Optional[str] = None,
    lieu: Optional[str] = None,
    image: Optional[str] = None,
    description: Optional[str] = None
):
    global event_id_counter
    
    try:
        dt = parse_date(date)
        if not dt:
            await interaction.response.send_message(
                "Format de date invalide! Utilisez le format: DD/MM/YYYY HH:MM (ex: 25/12/2024 20:30)",
                ephemeral=True
            )
            return
        
        if dt < get_current_time():
            await interaction.response.send_message(
                "Vous ne pouvez pas créer un événement dans le passé!",
                ephemeral=True
            )
            return
        
        if image and not image.startswith(('http://', 'https://')):
            await interaction.response.send_message(
                "L'URL de l'image doit commencer par http:// ou https://",
                ephemeral=True
            )
            return
        
        await interaction.response.defer()
        
        if not role and category != 'autre':
            role = get_role_by_category(interaction.guild, category)
            if role:
                print(f"Rôle automatique trouvé pour {category}: {role.name}")
            else:
                print(f"Aucun rôle automatique trouvé pour {category}")
        
        event = Event(event_id_counter, nom, dt, interaction.user.display_name, interaction.guild_id, interaction.channel_id, 
                      role.id if role else None, category, stream, lieu, image, description)
        events[event_id_counter] = event
        embed = create_event_embed(event, detailed=True)
        
        message = await interaction.followup.send(embed=embed)
        
        try:
            event_messages[event_id_counter] = message
        except Exception as e:
            print(f"Erreur lors du stockage du message: {e}")
        
        notifications_sent[event_id_counter] = {"15min": False, "live": False}
        notification_messages[event_id_counter] = []
        event_id_counter += 1
        
    except Exception as e:
        print(f"Erreur dans create_event: {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"Erreur lors de la création de l'événement: {str(e)}",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(f"Erreur: {e}", ephemeral=True)
        except Exception as inner_e:
            print(f"Erreur lors de l'envoi du message d'erreur: {inner_e}")

@bot.tree.command(name="twitchadd", description="Ajouter un ou plusieurs streamers à suivre")
@app_commands.describe(usernames="Noms d'utilisateur Twitch séparés par des espaces (sans @)")
async def add_streamer(interaction: discord.Interaction, usernames: str):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("Vous n'avez pas les permissions pour gérer les streamers!", ephemeral=True)
        return

    username_list = [u.lower().replace('@', '').strip() for u in usernames.split() if u.strip()]
    if not username_list:
        await interaction.response.send_message("Veuillez fournir au moins un nom d'utilisateur valide!", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    channel_id = interaction.channel_id
    added = []
    already = []

    for username in username_list:
        ok, msg = stream_manager.add_stream(username, interaction.guild_id, channel_id)
        if ok:
            streamers.setdefault(channel_id, [])
            if username not in streamers[channel_id]:
                streamers[channel_id].append(username)
            added.append(username)
        else:
            if "déjà" in msg or "present" in msg.lower():
                already.append(username)

    parts = []
    if added: parts.append(f"Ajouté(s): {', '.join(added)}")
    if already: parts.append(f"Déjà suivi(s): {', '.join(already)}")
    if not parts: parts.append("Aucun streamer ajouté.")
    await interaction.followup.send('\n'.join(parts), ephemeral=True)

@bot.tree.command(name="pingrole", description="Associer un rôle à ping quand un stream est en live dans ce salon")
@app_commands.describe(role="Rôle à mentionner")
async def set_ping_role(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("Vous n'avez pas la permission!", ephemeral=True)
        return
    try:
        ping_roles[interaction.channel_id] = role.id
        await interaction.response.send_message(f"Le rôle {role.mention} sera ping lorsque quelqu'un sera en live dans ce salon.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Erreur : {e}", ephemeral=True)

@bot.command(name='profile')
async def profile(ctx, gamename: str, region: str = "euw"):
    print(f"Profile command called by {ctx.author} with gamename: {gamename}, region: {region}")
    
    loading_msg = await ctx.send("Recherche du profil en cours...")
    
    try:
        if "#" not in gamename:
            bot_message = await loading_msg.edit(content="Format incorrect. Utilisez: `!profile gamename#tagline` (ex: `!profile Faker#KR1`)")
            asyncio.create_task(delete_messages_after_delay(ctx, bot_message, 3))
            return

        tagline = gamename.split("#")[1]
        gamename_only = gamename.split("#")[0]
        region = region.lower()
        region = region_mapping.get(region, "euw1")

        summoner_account = await getSummoner.get_summoner_by_riot_id(gamename_only, tagline)
        
        if "status" in summoner_account:
            bot_message = await loading_msg.edit(content=f"Ce joueur n'existe pas: {summoner_account.get('status', {}).get('message', 'Erreur inconnue')}")
            asyncio.create_task(delete_messages_after_delay(ctx, bot_message, 3))
            return

        if "puuid" not in summoner_account:
            bot_message = await loading_msg.edit(content="PUUID manquant dans la réponse de l'API")
            asyncio.create_task(delete_messages_after_delay(ctx, bot_message, 3))
            return

        summoner_data = await getSummoner.get_summoner_by_puuid(summoner_account["puuid"], region)
        
        if "status" in summoner_data:
            status_code = summoner_data.get("status", {}).get("status_code", "unknown")
            if status_code == 404:
                bot_message = await loading_msg.edit(content=f"Ce joueur n'existe pas dans la région {region.upper()}. Essayez une autre région.")
            else:
                bot_message = await loading_msg.edit(content=f"Erreur API (code {status_code}). Vérifiez votre clé API Riot.")
            asyncio.create_task(delete_messages_after_delay(ctx, bot_message, 3))
            return
            
        if "puuid" not in summoner_data:
            bot_message = await loading_msg.edit(content=f"PUUID manquant dans les données summoner")
            asyncio.create_task(delete_messages_after_delay(ctx, bot_message, 3))
            return

        summoner_id = summoner_data.get("id")
        
        tier = "Unranked"
        rank = ""
        lp = 0
        wins = 0
        losses = 0
        
        if summoner_id:
            league_data = await getSummoner.get_league_by_summoner(summoner_id, region)
            if not ("status" in league_data or not league_data):
                solo_queue = None
                for queue in league_data:
                    if queue.get("queueType") == "RANKED_SOLO_5x5":
                        solo_queue = queue
                        break
                
                if solo_queue:
                    tier = solo_queue.get("tier", "Unranked")
                    rank = solo_queue.get("rank", "")
                    lp = solo_queue.get("leaguePoints", 0)
                    wins = solo_queue.get("wins", 0)
                    losses = solo_queue.get("losses", 0)

        if tier == "Unranked":
            description = "Non classé"
            rank_color = 0x808080
        else:
            description = f"{tier.title()} {rank} - {lp} LP"
            rank_colors = {
                "IRON": 0x8B4513,
                "BRONZE": 0xCD7F32,
                "SILVER": 0xC0C0C0,
                "GOLD": 0xFFD700,
                "PLATINUM": 0x40E0D0,
                "EMERALD": 0x50C878,
                "DIAMOND": 0xB9F2FF,
                "MASTER": 0x9932CC,
                "GRANDMASTER": 0xFF0000,
                "CHALLENGER": 0x00CED1
            }
            rank_color = rank_colors.get(tier.upper(), 0x5CDBF0)

        embed = Embed(
            title=f"{gamename_only}#{tagline}",
            description=description,
            color=rank_color,
        )
        
        profile_icon_id = summoner_data.get('profileIconId', 1)
        embed.set_thumbnail(
            url=f"https://ddragon.leagueoflegends.com/cdn/14.24.1/img/profileicon/{profile_icon_id}.png"
        )
        
        level = summoner_data.get('summonerLevel', 'N/A')
        embed.add_field(name="Niveau", value=str(level), inline=True)
        
        if wins > 0 or losses > 0:
            embed.add_field(name="Victoires", value=str(wins), inline=True)
            embed.add_field(name="Défaites", value=str(losses), inline=True)
            winrate = round((wins / (wins + losses)) * 100, 1) if (wins + losses) > 0 else 0
            embed.add_field(name="Winrate", value=f"{winrate}%", inline=True)
        
        footer_text = f"Région: {region.upper()}"
        if summoner_id:
            footer_text += f" | ID: {summoner_id[:8]}..."
        embed.set_footer(text=footer_text)
        
        bot_message = await loading_msg.edit(content="", embed=embed)
        asyncio.create_task(delete_messages_after_delay(ctx, bot_message, 3))
        
    except Exception as e:
        print(f"Error in profile command: {e}")
        bot_message = await loading_msg.edit(content=f"Une erreur est survenue: {str(e)}")
        asyncio.create_task(delete_messages_after_delay(ctx, bot_message, 3))

@bot.command(name='watch')
async def watch_player(ctx, *args):
    try:
        await ctx.message.delete()
    except discord.NotFound:
        pass
    except discord.Forbidden:
        pass
    
    if len(args) < 1:
        embed = Embed(
            title="FORMAT INCORRECT",
            description="Utilisez la commande correctement :",
            color=0xff0000
        )
        embed.add_field(
            name="Usage",
            value="`!watch pseudo#tag [region] [@role] [url_stream]`",
            inline=False
        )
        embed.add_field(
            name="Exemples",
            value="`!watch Faker#KR1 kr`\n`!watch Caps#EUW euw @Streamers`\n`!watch Doublelift#NA na @Pro https://twitch.tv/doublelift`",
            inline=False
        )
        bot_message = await ctx.send(embed=embed)
        asyncio.create_task(delete_messages_after_delay(None, bot_message, 2))
        return
    
    gamename = args[0]
    
    if "#" not in gamename:
        embed = Embed(
            title="FORMAT PSEUDO INCORRECT",
            description="Le pseudo doit contenir un #tag",
            color=0xff0000
        )
        embed.add_field(name="Correct", value="`Faker#KR1`", inline=True)
        embed.add_field(name="Incorrect", value="`Faker` (manque #tag)", inline=True)
        bot_message = await ctx.send(embed=embed)
        asyncio.create_task(delete_messages_after_delay(None, bot_message, 2))
        return

    try:
        user_id = ctx.author.id
        
        if user_id not in watched_players:
            watched_players[user_id] = []
        
        if len(watched_players[user_id]) >= 15:
            embed = Embed(
                title="LIMITE ATTEINTE",
                description="Vous surveillez déjà **15 joueurs** (maximum autorisé)",
                color=0xff9900
            )
            
            current_list = []
            for i, p in enumerate(watched_players[user_id], 1):
                region_short = reverse_region_mapping.get(p['region'], p['region']).upper()
                current_list.append(f"{i}. **{p['gamename']}** ({region_short})")
            
            embed.add_field(
                name="Vos joueurs surveillés",
                value="\n".join(current_list),
                inline=False
            )
            embed.add_field(
                name="Solutions",
                value="`!unwatch NomJoueur#TAG` - Retirer un joueur\n`!unwatch all` - Tout supprimer\n`!watchlist` - Voir la liste détaillée",
                inline=False
            )
            
            bot_message = await ctx.send(embed=embed)
            asyncio.create_task(delete_messages_after_delay(None, bot_message, 2))
            return
        
        region = "euw"
        ping_role = None
        stream_url = None
        
        for arg in args[1:]:
            if arg.startswith('<@&') and arg.endswith('>'):
                try:
                    role_id = int(arg[3:-1])
                    ping_role = ctx.guild.get_role(role_id)
                except ValueError:
                    pass
            elif arg.startswith('http'):
                stream_url = arg
            elif arg.lower() in region_mapping:
                region = arg.lower()
        
        tagline = gamename.split("#")[1]
        gamename_only = gamename.split("#")[0]
        region_mapped = region_mapping.get(region, "euw1")

        for watched in watched_players[user_id]:
            if watched['gamename'].lower() == gamename.lower():
                embed = Embed(
                    title="DÉJÀ SURVEILLÉ",
                    description=f"**{watched['gamename']}** est déjà dans votre liste",
                    color=0xff9900
                )
                embed.add_field(
                    name="Information",
                    value="Ce joueur reste surveillé avec les paramètres actuels",
                    inline=False
                )
                bot_message = await ctx.send(embed=embed)
                asyncio.create_task(delete_messages_after_delay(None, bot_message, 2))
                return

        loading_embed = Embed(
            title="VÉRIFICATION EN COURS",
            description=f"Recherche de **{gamename_only}#{tagline}** dans la région **{region.upper()}**...",
            color=0x5CDBF0
        )
        loading_msg = await ctx.send(embed=loading_embed)

        summoner_account = await getSummoner.get_summoner_by_riot_id(gamename_only, tagline)
        if "status" in summoner_account:
            embed = Embed(
                title="JOUEUR INTROUVABLE",
                description=f"Le joueur **{gamename_only}#{tagline}** n'existe pas",
                color=0xff0000
            )
            embed.add_field(
                name="Vérifications",
                value="• Le pseudo est-il correct ?\n• Le #tag est-il exact ?\n• Le joueur existe-t-il vraiment ?",
                inline=False
            )
            await loading_msg.edit(embed=embed)
            asyncio.create_task(delete_messages_after_delay(None, loading_msg, 2))
            return

        initial_live_game = await getSummoner.get_live_game(summoner_account["puuid"], region_mapped)
        initial_is_in_game = not ("status" in initial_live_game)
        
        initial_watched_game = False
        if initial_is_in_game:
            queue_id = initial_live_game.get("gameQueueConfigId", 0)
            watched_queues = [420, 440, 400, 430]
            initial_watched_game = queue_id in watched_queues

        player_data = {
            "gamename": f"{gamename_only}#{tagline}",
            "puuid": summoner_account["puuid"],
            "region": region_mapped,
            "channel_id": ctx.channel.id,
            "last_status": initial_watched_game,
            "ping_role_id": ping_role.id if ping_role else WATCH_PING_ROLE_ID,
            "stream_url": stream_url
        }
        
        watched_players[user_id].append(player_data)

        if not game_watcher.is_running():
            game_watcher.start()

        embed = Embed(
            title="SURVEILLANCE ACTIVÉE",
            color=0x00ff00
        )
        
        if initial_is_in_game:
            if initial_watched_game:
                status_emoji = "🟡"
                status_text = "En partie surveillée - Prochaine partie sera notifiée"
            else:
                status_emoji = "🔵"
                status_text = "En partie non-surveillée - En attente ranked/unranked"
        else:
            status_emoji = "🟢"
            status_text = "Hors ligne - Surveillance active"
        
        embed.description = f"{status_emoji} **{gamename_only}#{tagline}** • {status_text}"
        
        config_lines = []
        config_lines.append(f"Région : {region.upper()}")
        
        if ping_role:
            config_lines.append(f"Notifications : {ping_role.mention}")
        else:
            config_lines.append(f"Notifications : Vous uniquement")
        
        if stream_url:
            config_lines.append(f"Stream : [Lien fourni]({stream_url})")
        
        embed.add_field(
            name="Configuration",
            value="\n".join(config_lines),
            inline=True
        )
        
        watched_list = []
        for i, p in enumerate(watched_players[user_id], 1):
            region_short = reverse_region_mapping.get(p['region'], p['region']).upper()
            watched_list.append(f"{i}. **{p['gamename']}** ({region_short})")
        
        embed.add_field(
            name=f"Votre liste ({len(watched_players[user_id])}/15)",
            value="\n".join(watched_list),
            inline=True
        )
        
        embed.add_field(
            name="Commandes utiles",
            value="`!watchlist` - Voir détails\n`!unwatch @joueur` - Retirer\n`!unwatch all` - Tout supprimer",
            inline=False
        )
        
        embed.set_footer(text="Vérification toutes les 5 minutes • Modes: Ranked Solo/Flex + Normal")
        embed.set_thumbnail(url="https://ddragon.leagueoflegends.com/cdn/14.24.1/img/profileicon/4915.png")

        await loading_msg.edit(embed=embed)
        asyncio.create_task(delete_messages_after_delay(None, loading_msg, 2))

    except Exception as e:
        print(f"Erreur dans watch command: {e}")
        embed = Embed(
            title="ERREUR SYSTÈME",
            description="Une erreur est survenue lors de l'activation",
            color=0xff0000
        )
        embed.add_field(name="Détails", value=f"```{str(e)}```", inline=False)
        bot_message = await ctx.send(embed=embed)
        asyncio.create_task(delete_messages_after_delay(None, bot_message, 2))

@tasks.loop(minutes=5)
async def game_watcher():
    if not any(watched_players.values()):
        return
    
    total_players = sum(len(players) for players in watched_players.values())
    print(f"Vérification de {total_players} joueur(s) surveillé(s)...")
    
    for user_id, player_list in watched_players.items():
        for player_info in player_list:
            try:
                print(f"Vérification de {player_info['gamename']}...")
                
                live_game = await getSummoner.get_live_game(player_info["puuid"], player_info["region"])
                is_in_game = not ("status" in live_game)
                
                is_in_watched_game = False
                if is_in_game:
                    queue_id = live_game.get("gameQueueConfigId", 0)
                    watched_queues = [420, 440, 400, 430]
                    is_in_watched_game = queue_id in watched_queues
                
                previous_status = player_info["last_status"]
                
                if is_in_watched_game and not previous_status:
                    print(f"PARTIE DÉTECTÉE: {player_info['gamename']} entre en partie!")
                    
                    channel = bot.get_channel(player_info["channel_id"])
                    if channel:
                        await send_modern_notification(channel, player_info, live_game, user_id)
                
                player_info["last_status"] = is_in_watched_game
                
            except Exception as e:
                print(f"Erreur surveillance {player_info['gamename']}: {e}")

async def send_modern_notification(channel, player_info, live_game, user_id):
    try:
        participants = live_game.get("participants", [])
        blue_team = [p for p in participants if p["teamId"] == 100]
        red_team = [p for p in participants if p["teamId"] == 200]
        
        watched_player_data = None
        for participant in participants:
            if participant.get("puuid") == player_info["puuid"]:
                watched_player_data = participant
                break
        
        queue_id = live_game.get("gameQueueConfigId", 0)
        game_mode = queue_mapping.get(queue_id, "Mode inconnu")
        duration = round(live_game.get("gameLength", 0) / 60)
        
        player_champion = "Inconnu"
        player_champion_icon = ""
        if watched_player_data:
            player_champion_info = await getSummoner.find_champion_by_id(watched_player_data.get("championId", 0))
            player_champion = player_champion_info["name"]
            player_champion_icon = player_champion_info["icon_url"]
        
        embed = Embed(
            title="PARTIE DÉTECTÉE !",
            color=0x00ff41,
            timestamp=datetime.now()
        )
        
        region_short = reverse_region_mapping.get(player_info['region'], player_info['region']).upper()
        embed.description = f"**{player_info['gamename']}** joue **{player_champion}** en **{game_mode}**"
        
        info_lines = []
        info_lines.append(f"Durée: {duration} min")
        info_lines.append(f"Région: {region_short}")
        info_lines.append(f"Mode: {game_mode}")
        
        embed.add_field(name="Infos", value="\n".join(info_lines), inline=True)
        
        blue_champs = []
        red_champs = []
        
        for player in blue_team:
            champion_info = await getSummoner.find_champion_by_id(player.get("championId", 0))
            marker = " ⭐" if player.get("puuid") == player_info["puuid"] else ""
            blue_champs.append(f"**{champion_info['name']}**{marker}")
        
        for player in red_team:
            champion_info = await getSummoner.find_champion_by_id(player.get("championId", 0))
            marker = " ⭐" if player.get("puuid") == player_info["puuid"] else ""
            red_champs.append(f"**{champion_info['name']}**{marker}")
        
        teams_display = f"🔴 {' • '.join(red_champs)}\n\n⚡ **VS** ⚡\n\n🔵 {' • '.join(blue_champs)}"
        embed.add_field(name="Équipes", value=teams_display, inline=False)
        
        if player_info.get("stream_url"):
            embed.add_field(
                name="Stream",
                value=f"[Regarder maintenant]({player_info['stream_url']})",
                inline=True
            )
        
        if player_champion_icon:
            embed.set_thumbnail(url=player_champion_icon)
        
        embed.set_footer(text="GL HF ! • Message supprimé dans 25 min")
        
        ping_content = ""
        if player_info.get("ping_role_id"):
            try:
                role = channel.guild.get_role(player_info["ping_role_id"])
                if role:
                    ping_content = f"{role.mention}"
            except:
                pass
        
        if not ping_content:
            ping_content = f"<@{user_id}>"
        
        notification_msg = await channel.send(ping_content, embed=embed)
        print(f"Notification envoyée pour {player_info['gamename']}")
        
        asyncio.create_task(delete_message_after_delay(notification_msg, 25))
        
    except Exception as e:
        print(f"Erreur notification: {e}")

@bot.command(name='help_lol')
async def help_command(ctx):
    embed = Embed(
        title="COMMANDES LEAGUE OF LEGENDS",
        description="**Voici toutes les commandes disponibles :**",
        color=0x0099ff
    )
    
    embed.add_field(
        name="**!profile** `<pseudo#tag>` `[region]`",
        value="Affiche le profil complet d'un joueur\n**Exemple:** `!profile Faker#KR1 kr`",
        inline=False
    )
    
    embed.add_field(
        name="**!watch** `<pseudo#tag>` `[region]` `[@role]` `[url]`",
        value="Surveille un joueur (max 15) et notifie ses parties\n**Exemple:** `!watch Faker#KR1 kr @Streamers https://twitch.tv/faker`",
        inline=False
    )
    
    embed.add_field(
        name="**!watchlist**",
        value="Affiche tous vos joueurs surveillés avec détails",
        inline=False
    )
    
    embed.add_field(
        name="**!unwatch** `[joueur|all]`",
        value="Retire un joueur ou tous (`!unwatch Faker#KR1` ou `!unwatch all`)",
        inline=False
    )
    
    embed.add_field(
        name="RÉGIONS",
        value="`euw` • `eune` • `na` • `kr` • `jp` • `br` • `lan` • `las` • `oce` • `tr` • `ru`",
        inline=False
    )
    
    embed.add_field(
        name="IMPORTANT",
        value="• Format obligatoire : `Pseudo#TAG`\n• Messages auto-supprimés après 30 secondes\n• Surveillance : uniquement ranked/unranked",
        inline=False
    )
    
    embed.set_footer(text="Bot League of Legends • Données via Riot Games API")
    embed.set_thumbnail(url="https://ddragon.leagueoflegends.com/cdn/14.24.1/img/profileicon/4915.png")
    
    bot_message = await ctx.send(embed=embed)
    asyncio.create_task(delete_messages_after_delay(ctx, bot_message, 30))

@bot.tree.command(name="helpalpine", description="Afficher toutes les commandes disponibles")
async def help_command_alpine(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Guide des Commandes - Bot Alpine Unifié",
        description="Voici toutes les commandes disponibles organisées par catégorie :",
        color=0x00AE86,
        timestamp=get_current_time()
    )
    
    events_commands = """
`/event-create` - Créer un nouvel événement
`/event-list` - Afficher tous les événements
`/event-info <id>` - Détails d'un événement
`/event-delete <id>` - Supprimer un événement
    """
    embed.add_field(name="Événements", value=events_commands.strip(), inline=False)
    
    twitch_commands = """
`/twitchadd <streamers>` - Ajouter streamer(s) à suivre
`/twitchremove <streamers>` - Retirer streamer(s)
`/twitchlist` - Voir les streamers suivis (avec viewers)
`/twitchclear` - Vider la liste des streamers
`/pingrole <role>` - Configurer le rôle à ping pour les lives
    """
    embed.add_field(name="Twitch", value=twitch_commands.strip(), inline=False)
    
    lol_commands = """
`!profile <pseudo#tag> [region]` - Profil d'un joueur LoL
`!watch <pseudo#tag> [region] [@role] [url]` - Surveiller un joueur
`!watchlist` - Voir vos joueurs surveillés
`!unwatch [joueur|all]` - Arrêter de surveiller
`!help_lol` - Aide détaillée League of Legends
    """
    embed.add_field(name="League of Legends", value=lol_commands.strip(), inline=False)
    
    embed.add_field(
        name="Informations",
        value="• Format de date: **DD/MM/YYYY HH:MM**\n• Notifications automatiques: 15min avant + live\n• Timezone: **Europe/Paris**\n• **Surveillance Twitch: toutes les 2 minutes avec viewers**\n• **Surveillance LoL: toutes les 5 minutes**",
        inline=False
    )
    
    embed.set_footer(text="Bot Alpine Unifié • Développé pour votre serveur Discord")
    
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")
    print(f"ID du bot: {bot.user.id}")
    print(f"Connecté à {len(bot.guilds)} serveur(s)")
    
    try:
        all_streams = stream_manager.get_all_streams()
        global streamers
        streamers.clear()
        for s in all_streams:
            cid = int(s['channel_id'])
            streamers.setdefault(cid, [])
            if s['username'] not in streamers[cid]:
                streamers[cid].append(s['username'])
        print(f"Streams hydratés depuis {stream_manager.backend()}: {len(all_streams)} entrées")

        await asyncio.sleep(2)
        
        print(f"Commandes enregistrées dans le bot:")
        for cmd in bot.tree.get_commands():
            print(f"  - {cmd.name}: {cmd.description}")
        
        print(f"Synchronisation des commandes avec Discord...")
        
        synced_global = await bot.tree.sync()
        print(f'{len(synced_global)} commandes synchronisées globalement!')
        
        print(f"Commandes synchronisées:")
        for cmd in synced_global:
            print(f"  - /{cmd.name}")
        
        print("Initialisation de l'API Twitch...")
        await twitch_api.get_token()
        if twitch_api.token:
            print("Token Twitch obtenu avec succès!")
        else:
            print("Impossible d'obtenir le token Twitch")
        
        if not check_streams.is_running():
            check_streams.start()
            print("Système de vérification Twitch démarré! (toutes les 2 minutes)")
        else:
            print("Système Twitch déjà en cours d'exécution")
        
        if not notification_system.is_running():
            notification_system.start()
            print("Système de notifications démarré!")
        else:
            print("Système de notifications déjà en cours d'exécution")
        
        port_env = os.getenv("PORT")
        if port_env:
            print(f"Démarrage du serveur web sur le port {port_env}...")
            success = await start_web_server()
            if success:
                print("Serveur web démarré avec succès!")
            else:
                print("ÉCHEC du démarrage du serveur web!")
        else:
            print("Variable PORT non définie, serveur web non démarré")
            
        print("Bot complètement initialisé et prêt!")
        print("="*50)
        print("RÉSUMÉ DE L'INITIALISATION:")
        print(f"  Bot connecté: OK")
        print(f"  Commandes sync: OK ({len(synced_global)})")
        print(f"  Système Twitch: {'OK' if check_streams.is_running() else 'KO'}")
        print(f"  Notifications: {'OK' if notification_system.is_running() else 'KO'}")
        print(f"  Serveur web: {'OK' if web_runner is not None else 'KO'}")
        print(f"  Token Twitch: {'OK' if twitch_api.token else 'KO'}")
        print("="*50)
            
    except Exception as e:
        print(f"ERREUR CRITIQUE lors de l'initialisation: {e}")
        import traceback
        traceback.print_exc()
        print("Le bot peut ne pas fonctionner correctement!")

@bot.event
async def on_error(event, *args, **kwargs):
    print(f"Erreur dans l'event {event}: {args} {kwargs}")
    import traceback
    traceback.print_exc()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        bot_message = await ctx.send(f"Commande inconnue. Tapez `!help_lol` ou `/helpalpine` pour voir toutes les commandes disponibles.")
        asyncio.create_task(delete_messages_after_delay(ctx, bot_message, 3))
    elif isinstance(error, commands.MissingRequiredArgument):
        bot_message = await ctx.send(f"Argument manquant. Tapez `!help_lol` pour voir la syntaxe correcte.")
        asyncio.create_task(delete_messages_after_delay(ctx, bot_message, 3))
    elif isinstance(error, commands.CheckFailure):
        bot_message = await ctx.send(f"Vous n'avez pas les permissions pour utiliser cette commande.")
        asyncio.create_task(delete_messages_after_delay(ctx, bot_message, 3))
    else:
        print(f"Command error: {error}")
        bot_message = await ctx.send(f"Une erreur est survenue: {str(error)}")
        asyncio.create_task(delete_messages_after_delay(ctx, bot_message, 3))

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    print(f"Erreur de slash commande: {error}")
    import traceback
    traceback.print_exc()
    
    try:
        error_msg = "Une erreur est survenue lors de l'exécution de la commande."
        
        if "Missing Access" in str(error):
            error_msg = "Le bot n'a pas les permissions nécessaires pour cette action."
        elif "Unknown Channel" in str(error):
            error_msg = "Canal introuvable ou inaccessible."
        elif "Unknown Guild" in str(error):
            error_msg = "Serveur introuvable."
        elif "HTTPException" in str(error):
            error_msg = "Erreur de communication avec Discord. Réessayez dans quelques secondes."
        
        if not interaction.response.is_done():
            await interaction.response.send_message(error_msg, ephemeral=True)
        else:
            await interaction.followup.send(error_msg, ephemeral=True)
    except Exception as e:
        print(f"Erreur lors de l'envoi du message d'erreur: {e}")

def signal_handler(signum, frame):
    print(f"\nSignal {signum} reçu, arrêt du bot...")
    asyncio.create_task(shutdown_bot())

async def shutdown_bot():
    print("Arrêt en cours...")
    
    try:
        if check_streams.is_running():
            check_streams.cancel()
            print("Système Twitch arrêté")
        
        if notification_system.is_running():
            notification_system.cancel()
            print("Système de notifications arrêté")
        
        if game_watcher.is_running():
            game_watcher.cancel()
            print("Système LoL watcher arrêté")
        
        await stop_web_server()
        await bot.close()
        print("Bot fermé proprement")
        
    except Exception as e:
        print(f"Erreur lors de l'arrêt: {e}")
    
    finally:
        print("Goodbye!")

if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, signal_handler)
if hasattr(signal, 'SIGINT'):
    signal.signal(signal.SIGINT, signal_handler)

@bot.event
async def on_message(ctx):
    if ctx.author == bot.user:
        return
    await bot.process_commands(ctx)

if __name__ == '__main__':
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN manquant dans les variables d'environnement!")
        print("Assurez-vous que la variable DISCORD_TOKEN est définie")
        exit(1)
    
    print("Démarrage du Bot Alpine Unifié...")
    print(f"Variables d'environnement:")
    print(f"  - DISCORD_TOKEN: {'Défini' if token else 'Manquant'}")
    print(f"  - TWITCH_CLIENT_ID: {'Défini' if os.getenv('TWITCH_CLIENT_ID') else 'Manquant'}")
    print(f"  - TWITCH_CLIENT_SECRET: {'Défini' if os.getenv('TWITCH_CLIENT_SECRET') else 'Manquant'}")
    print(f"  - RIOT_API_KEY: {'Défini' if os.getenv('RIOT_API_KEY') else 'Manquant'}")
    print(f"  - PORT: {os.getenv('PORT') if os.getenv('PORT') else 'Non défini'}")
    print("-" * 50)
    
    try:
        bot.run(token)
    except KeyboardInterrupt:
        print("\nArrêt manuel détecté...")
    except Exception as e:
        print(f"ERREUR CRITIQUE lors du démarrage du bot: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Bot arrêté!")
        print(f"Erreur serveur web: {e}")
        return False

async def stop_web_server():
    global web_runner, web_site
    try:
        if web_site:
            await web_site.stop()
            web_site = None
        if web_runner:
            await web_runner.cleanup()
            web_runner = None
    except Exception as e:
        print(f"Erreur arrêt serveur: {e}")

class StreamManager:
    def __init__(self):
        self.db_url = os.environ.get('DATABASE_URL')
        if self.db_url:
            self.init_database()
        else:
            print("DATABASE_URL non trouvée, utilisation du fichier JSON")
            self.data_dir = 'data'
            os.makedirs(self.data_dir, exist_ok=True)
            self.streams_file = os.path.join(self.data_dir, 'streams.json')
            self._streams_cache = []
            self._load_streams_json()

    def init_database(self):
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS streams (
                    id SERIAL PRIMARY KEY,
                    username TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(username, guild_id, channel_id)
                )
            """)
            conn.commit()
            cur.close()
            conn.close()
            print("Base de données PostgreSQL initialisée")
        except Exception as e:
            print(f"Erreur base de données: {e}")

    def _db(self):
        return psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)

    def add_stream(self, username, guild_id, channel_id):
        username = username.lower().replace('@','').strip()
        if not username:
            return False, "Nom d'utilisateur invalide"
        if self.db_url:
            try:
                conn = self._db()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO streams (username, guild_id, channel_id) VALUES (%s, %s, %s)",
                    (username, str(guild_id), str(channel_id))
                )
                conn.commit()
                cur.close(); conn.close()
                return True, "Stream ajouté avec succès"
            except psycopg2.IntegrityError:
                return False, "Stream déjà présent"
            except Exception as e:
                print("Erreur ajout stream:", e)
                return False, "Erreur lors de l'ajout"
        else:
            return self._add_stream_json(username, guild_id, channel_id)

    def remove_stream(self, username, guild_id, channel_id):
        username = username.lower().replace('@','').strip()
        if self.db_url:
            try:
                conn = self._db()
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM streams WHERE username=%s AND guild_id=%s AND channel_id=%s",
                    (username, str(guild_id), str(channel_id))
                )
                deleted = cur.rowcount
                conn.commit()
                cur.close(); conn.close()
                if deleted:
                    return True, "Stream supprimé"
                return False, "Stream non trouvé"
            except Exception as e:
                print("Erreur suppression stream:", e)
                return False, "Erreur lors de la suppression"
        else:
            return self._remove_stream_json(username, guild_id, channel_id)

    def clear_channel(self, guild_id, channel_id):
        if self.db_url:
            try:
                conn = self._db()
                cur = conn.cursor()
                cur.execute("DELETE FROM streams WHERE guild_id=%s AND channel_id=%s",
                            (str(guild_id), str(channel_id)))
                count = cur.rowcount
                conn.commit()
                cur.close(); conn.close()
                return count
            except Exception as e:
                print("Erreur clear channel:", e)
                return 0
        else:
            before = len(self._streams_cache)
            self._streams_cache = [s for s in self._streams_cache if not (s['guild_id']==str(guild_id) and s['channel_id']==str(channel_id))]
            self._save_streams_json()
            return before - len(self._streams_cache)

    def get_streams_for_channel(self, guild_id, channel_id):
        if self.db_url:
            try:
                conn = self._db()
                cur = conn.cursor()
                cur.execute(
                    "SELECT username FROM streams WHERE guild_id=%s AND channel_id=%s ORDER BY added_at DESC",
                    (str(guild_id), str(channel_id))
                )
                rows = cur.fetchall()
                cur.close(); conn.close()
                return [r['username'] for r in rows]
            except Exception as e:
                print("Erreur get_streams_for_channel:", e)
                return []
        else:
            return [s['username'] for s in self._streams_cache if s['guild_id']==str(guild_id) and s['channel_id']==str(channel_id)]

    def get_all_streams(self):
        if self.db_url:
            try:
                conn = self._db()
                cur = conn.cursor()
                cur.execute("SELECT username, guild_id, channel_id FROM streams")
                rows = cur.fetchall()
                cur.close(); conn.close()
                return [dict(r) for r in rows]
            except Exception as e:
                print("Erreur get_all_streams:", e)
                return []
        else:
            return list(self._streams_cache)

    def get_total_count(self):
        if self.db_url:
            try:
                conn = self._db()
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) AS c FROM streams")
                c = cur.fetchone()['c']
                cur.close(); conn.close()
                return int(c)
            except Exception as e:
                print("Erreur count:", e)
                return 0
        else:
            return len(self._streams_cache)

    def backend(self):
        return "PostgreSQL" if self.db_url else "JSON"

    def _ensure_json_loaded(self):
        if not hasattr(self, "_streams_cache"):
            self._streams_cache = []
            self._load_streams_json()

    def _load_streams_json(self):
        self._ensure_json_loaded()
        try:
            if os.path.exists(self.streams_file):
                with open(self.streams_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._streams_cache = data.get('streams', [])
            else:
                self._streams_cache = []
        except Exception as e:
            print("Erreur chargement JSON:", e)
            self._streams_cache = []

    def _save_streams_json(self):
        self._ensure_json_loaded()
        try:
            data = {'streams': self._streams_cache, 'last_updated': datetime.now().isoformat()}
            with open(self.streams_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print('Erreur sauvegarde JSON:', e)
            return False

    def _add_stream_json(self, username, guild_id, channel_id):
        self._ensure_json_loaded()
        for s in self._streams_cache:
            if s['username']==username and s['guild_id']==str(guild_id) and s['channel_id']==str(channel_id):
                return False, "Stream déjà présent"
        self._streams_cache.append({
            'username': username,
            'guild_id': str(guild_id),
            'channel_id': str(channel_id),
            'added_at': datetime.now().isoformat()
        })
        self._save_streams_json()
        return True, "Stream ajouté avec succès"

    def _remove_stream_json(self, username, guild_id, channel_id):
        self._ensure_json_loaded()
        before = len(self._streams_cache)
        self._streams_cache = [s for s in self._streams_cache if not (s['username']==username and s['guild_id']==str(guild_id) and s['channel_id']==str(channel_id))]
        self._save_streams_json()
        if len(self._streams_cache) < before:
            return True, "Stream supprimé"
        return False, "Stream non trouvé"

stream_manager = StreamManager()

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
streamers = {}
stream_messages = {}
currently_live_streamers = {}
ping_roles = {}
reaction_role_messages = {}

class TwitchAPI:
    def __init__(self):
        self.token = None
        self.headers = {}
        self.token_expires_at = None

    async def get_token(self):
        if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
            print("Variables Twitch manquantes, fonctionnalités Twitch désactivées")
            return
        url = "https://id.twitch.tv/oauth2/token"
        params = {
            'client_id': TWITCH_CLIENT_ID,
            'client_secret': TWITCH_CLIENT_SECRET,
            'grant_type': 'client_credentials'
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, params=params) as resp:
                    data = await resp.json()
                    self.token = data['access_token']
                    self.token_expires_at = datetime.now(UTC).timestamp() + data['expires_in']
                    self.headers = {
                        'Client-ID': TWITCH_CLIENT_ID,
                        'Authorization': f'Bearer {self.token}'
                    }
        except Exception as e:
            print(f"Erreur Twitch API: {e}")

    async def ensure_valid_token(self):
        if not self.token or datetime.now(UTC).timestamp() >= self.token_expires_at - 300:
            await self.get_token()

    async def get_streams(self, usernames):
        if not self.token:
            return []
        await self.ensure_valid_token()
        url = "https://api.twitch.tv/helix/streams"
        all_streams = []
        for i in range(0, len(usernames), 100):
            batch = usernames[i:i+100]
            params = {'user_login': batch}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=self.headers, params=params) as response:
                        if response.status == 200:
                            data = await response.json()
                            all_streams.extend(data['data'])
            except Exception as e:
                print(f"Erreur lors de la récupération des streams: {e}")
        return all_streams

twitch_api = TwitchAPI()

def format_viewer_count(count):
    if count >= 1000:
        return f"{count//1000}k"
    return str(count)

@tasks.loop(minutes=2)
async def check_streams():
    print(f"Vérification des streams Twitch - {datetime.now(TIMEZONE).strftime('%H:%M:%S')}")
    
    for channel_id, streamer_list in streamers.items():
        if not streamer_list:
            continue
        channel = bot.get_channel(channel_id)
        if not channel:
            continue
        streams = await twitch_api.get_streams(streamer_list)
        live_now = {s['user_login']: s for s in streams}
        
        for username, stream in live_now.items():
            key = f"{channel_id}_{username}"
            if key in stream_messages:
                try:
                    stream = live_now[username]
                    stored_msg = stream_messages[key]
                    
                    if 'message_obj' in stored_msg:
                        message = stored_msg['message_obj']
                    else:
                        message = await channel.fetch_message(stored_msg['message_id'])
                        stream_messages[key]['message_obj'] = message
                    
                    updated_embed = discord.Embed(
                        title=f"🔴 {stream['user_name']} est en live !",
                        description=stream['title'],
                        url=f"https://twitch.tv/{username}",
                        color=0x9146ff
                    )
                    
                    viewer_count = stream.get('viewer_count', 0)
                    updated_embed.add_field(
                        name="👥 Viewers", 
                        value=f"**{format_viewer_count(viewer_count)}** spectateurs", 
                        inline=True
                    )
                    
                    if stream.get('game_name'):
                        updated_embed.add_field(
                            name="🎮 Jeu", 
                            value=stream['game_name'], 
                            inline=True
                        )
                    
                    thumbnail_url = stream.get('thumbnail_url', '').replace('{width}', '1280').replace('{height}', '720')
                    if thumbnail_url:
                        updated_embed.set_image(url=thumbnail_url)
                    
                    started_at = datetime.fromisoformat(stream['started_at'].replace('Z', '+00:00'))
                    started_at_paris = started_at.astimezone(TIMEZONE)
                    updated_embed.set_footer(
                        text=f"Stream commencé à {started_at_paris.strftime('%H:%M')} • Dernière MàJ: {datetime.now(TIMEZONE).strftime('%H:%M')}"
                    )
                    
                    await message.edit(embed=updated_embed)
                    stream_messages[key]['last_update'] = datetime.now(UTC).timestamp()
                    
                    print(f"Stream mis à jour: {stream['user_name']} ({viewer_count} viewers)")
                    
                except Exception as e:
                    print(f"Erreur mise à jour embed pour {username}: {e}")
                continue

            embed = discord.Embed(
                title=f"🔴 {stream['user_name']} est en live !",
                description=stream['title'],
                url=f"https://twitch.tv/{username}",
                color=0x9146ff
            )
            
            viewer_count = stream.get('viewer_count', 0)
            embed.add_field(
                name="👥 Viewers", 
                value=f"**{format_viewer_count(viewer_count)}** spectateurs", 
                inline=True
            )
            
            if stream.get('game_name'):
                embed.add_field(
                    name="🎮 Jeu", 
                    value=stream['game_name'], 
                    inline=True
                )
            
            thumbnail_url = stream.get('thumbnail_url', '').replace('{width}', '1280').replace('{height}', '720')
            if thumbnail_url:
                embed.set_image(url=thumbnail_url)
            
            started_at = datetime.fromisoformat(stream['started_at'].replace('Z', '+00:00'))
            started_at_paris = started_at.astimezone(TIMEZONE)
            embed.set_footer(
                text=f"Stream commencé à {started_at_paris.strftime('%H:%M')} • Mise à jour toutes les 2 min"
            )
            
            ping_content = f"<@&{ping_roles.get(channel_id)}>" if ping_roles.get(channel_id) else None
            msg = await channel.send(content=ping_content, embed=embed)
            stream_messages[key] = {
                'message_id': msg.id, 
                'last_update': datetime.now(UTC).timestamp(),
                'message_obj': msg
            }
            
            print(f"Nouveau stream détecté: {stream['user_name']} ({viewer_count} viewers)")

        for username in streamer_list:
            key = f"{channel_id}_{username}"
            if key in stream_messages and username not in live_now:
                try:
                    if 'message_obj' in stream_messages[key]:
                        await stream_messages[key]['message_obj'].delete()
                    else:
                        message = await channel.fetch_message(stream_messages[key]['message_id'])
                        await message.delete()
                    print(f"Stream terminé: {username}")
                except:
                    pass
                del stream_messages[key]

@check_streams.before_loop
async def before_check(): await bot.wait_until_ready()

events = {}
event_id_counter = 1
event_messages = {}
notifications_sent = {}
guild_role_configs = {}
notification_messages = {}

class Event:
    def __init__(self, id, name, date, creator, guild_id, channel_id, role_id=None, category=None, stream=None, lieu=None, image=None, description=None):
        self.id = id
        self.name = name
        self.date = date
        self.creator = creator
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.role_id = role_id
        self.category = category
        self.stream = stream
        self.lieu = lieu
        self.image = image
        self.description = description
        self.created_at = get_current_time()

def save_guild_config(guild_id, config):
    guild_role_configs[guild_id] = config

def get_guild_config(guild_id):
    return guild_role_configs.get(guild_id, {})

def get_role_by_category(guild, category):
    config = get_guild_config(guild.id)
    role_id = config.get(category)
    
    if role_id:
        return guild.get_role(role_id)
    return None

def create_event_embed(event, detailed=False):
    embed = discord.Embed(title=f"🎉 {event.name}", timestamp=event.created_at, color=0x00AE86)
    embed.add_field(name="📅 Date", value=format_date(event.date), inline=True)
    
    if hasattr(event, 'category') and event.category:
        category_names = {
            'lec': '🏆 LEC',
            'lfl': '🇫🇷 LFL', 
            'rl': '🚗 Rocket League',
            'r6': '🎯 Rainbow Six',
            'chess': '♟️ Échecs'
        }
        category_display = category_names.get(event.category, f"🎮 {event.category.upper()}")
        embed.add_field(name="🎮 Catégorie", value=category_display, inline=True)
    else:
        embed.add_field(name="\u200b", value="\u200b", inline=True)
    
    if event.lieu:
        embed.add_field(name="📍 Lieu", value=event.lieu, inline=True)
    if event.stream:
        embed.add_field(name="📺 Stream", value=event.stream, inline=False)
    if event.description and detailed:
        embed.add_field(name="📝 Description", value=event.description, inline=False)
    
    if event.image and event.image.strip():
        if event.image.startswith(('http://', 'https://')):
            embed.set_image(url=event.image)
        else:
            embed.add_field(name="⚠️ Image", value="URL d'image invalide", inline=False)
    
    embed.set_footer(text=f"Créé par {event.creator}")
    return embed

def create_notification_embed(event, minutes_before):
    if minutes_before == 0:
        title = f"🔴 LIVE MAINTENANT - {event.name}"
        color = 0xFF0000
        message = "L'événement commence maintenant !"
    else:
        title = f"⏰ {event.name} - Dans {minutes_before} minutes"
        color = 0xFFA500
        message = f"L'événement commence dans {minutes_before} minutes !"
    
    embed = discord.Embed(
        title=title,
        description=message,
        color=color,
        timestamp=get_current_time()
    )
    
    embed.add_field(name="📅 Heure de début", value=format_date(event.date), inline=True)
    
    if hasattr(event, 'category') and event.category:
        category_names = {
            'lec': '🏆 LEC',
            'lfl': '🇫🇷 LFL', 
            'rl': '🚗 Rocket League',
            'r6': '🎯 Rainbow Six',
            'chess': '♟️ Échecs'
        }
        category_display = category_names.get(event.category, event.category.upper())
        embed.add_field(name="🎮 Catégorie", value=category_display, inline=True)
    
    if event.lieu:
        embed.add_field(name="📍 Lieu", value=event.lieu, inline=True)
    
    if event.stream:
        embed.add_field(name="📺 Stream", value=event.stream, inline=False)
    
    if event.image and event.image.startswith(('http://', 'https://')):
        embed.set_image(url=event.image)
    
    return embed

async def delete_message_after_delay(message, delay_minutes):
    await asyncio.sleep(delay_minutes * 60)
    try:
        await message.delete()
    except:
        pass

@tasks.loop(minutes=1)
async def notification_system():
    try:
        now = get_current_time()
        print(f"Vérification des notifications à {now.strftime('%d/%m/%Y %H:%M:%S')} (heure française)")
        
        for event_id, event in list(events.items()):
            if event_id not in notifications_sent:
                continue
            
            delta = event.date - now
            minutes = int(delta.total_seconds() / 60)
            
            if minutes <= 15 and not notifications_sent[event_id]["15min"]:
                notification_msg = await send_event_notification(event, 15)
                if notification_msg:
                    notification_messages[event_id].append(notification_msg)
                    asyncio.create_task(delete_message_after_delay(notification_msg, 5))
                notifications_sent[event_id]["15min"] = True
            
            elif minutes <= 0 and not notifications_sent[event_id]["live"]:
                notification_msg = await send_event_notification(event, 0)
                if notification_msg:
                    notification_messages[event_id].append(notification_msg)
                    asyncio.create_task(delete_message_after_delay(notification_msg, 5))
                notifications_sent[event_id]["live"] = True
            
            elif delta.total_seconds() < -1800:
                if event_id in event_messages:
                    try: 
                        await event_messages[event_id].delete()
                    except: 
                        pass
                    del event_messages[event_id]
            
            elif delta.total_seconds() < -7200:
                await delete_event_message(event_id)
                
    except Exception as e:
