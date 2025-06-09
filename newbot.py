import discord
from discord.ext import commands, tasks
import random
import asyncio
import os
import time
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from dotenv import load_dotenv

# Configure comprehensive logging system
def setup_logging():
    """Set up logging with file rotation and multiple levels."""
    
    # Create logs directory if it doesn't exist
    os.makedirs('logs', exist_ok=True)
    
    # Create main logger
    logger = logging.getLogger('CoupBot')
    logger.setLevel(logging.DEBUG)
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # File handler with rotation (10MB files, keep 5 backups)
    file_handler = RotatingFileHandler(
        'logs/coup_bot.log',
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    
    # Console handler for immediate feedback
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Error file handler for errors only
    error_handler = RotatingFileHandler(
        'logs/coup_bot_errors.log',
        maxBytes=5*1024*1024,  # 5MB
        backupCount=3,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    
    # Detailed formatter for files
    file_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)8s | %(funcName)20s:%(lineno)3d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Simple formatter for console
    console_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Apply formatters
    file_handler.setFormatter(file_formatter)
    console_handler.setFormatter(console_formatter)
    error_handler.setFormatter(file_formatter)
    
    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.addHandler(error_handler)
    
    return logger

# Initialize logging
logger = setup_logging()

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Game variables - Now stored per guild (server) with activity tracking
games = {}  # {guild_id: game_state} - Each server gets its own game

def get_game_state(guild_id):
    """Get or create game state for a specific guild."""
    if guild_id not in games:
        games[guild_id] = {
            'players': {},
            'court_deck': ["Duke", "Assassin", "Contessa", "Captain", "Ambassador"] * 3,
            'discarded_cards': [],
            'game_started': False,
            'current_player': None,
            'join_message': None,
            'last_activity': time.time(),
            'created_at': time.time(),
            # NEW: Add basic statistics tracking
            'stats': {
                'games_started': 0,
                'games_completed': 0,
                'games_abandoned': 0,
                'player_games': {},  # {player_id: games_played}
                'player_wins': {},   # {player_id: games_won}
                'total_participants': set()  # unique players who have played
            }
        }
        # Shuffle the deck for new games
        random.shuffle(games[guild_id]['court_deck'])
        print(f"✅ Created new game state for guild {guild_id}")
    
    # NEW: Update last activity whenever game state is accessed
    games[guild_id]['last_activity'] = time.time()
    return games[guild_id]

def update_game_activity(guild_id):
    """Update the last activity time for a game."""
    if guild_id in games:
        games[guild_id]['last_activity'] = time.time()

def log_game_action(action_type, guild_id, player, target=None, details=None, success=True):
    """Log game actions with structured information."""
    guild_name = "Unknown"
    if guild := bot.get_guild(guild_id):
        guild_name = guild.name
    
    # Build log message with None checking
    player_name = getattr(player, 'name', 'Unknown Player') if player else 'Unknown Player'
    
    if target:
        target_name = getattr(target, 'name', 'Unknown Player') if target else 'Unknown Player'
        action_msg = f"{action_type.upper()}: {player_name} → {target_name}"
    else:
        action_msg = f"{action_type.upper()}: {player_name}"
    
    if details:
        action_msg += f" | {details}"
    
    log_msg = f"[{guild_name}] {action_msg}"
    
    # Log at appropriate level
    if success:
        if action_type in ['challenge', 'block_challenge', 'assassination', 'coup']:
            logger.warning(log_msg)  # Important dramatic actions
        else:
            logger.info(log_msg)  # Regular actions
    else:
        logger.warning(f"FAILED - {log_msg}")

def record_game_start(guild_id, players):
    """Record when a game starts."""
    game_state = get_game_state(guild_id)
    game_state['stats']['games_started'] += 1
    
    # Track player participation
    for player in players:
        player_id = player.id
        game_state['stats']['total_participants'].add(player_id)
        game_state['stats']['player_games'][player_id] = game_state['stats']['player_games'].get(player_id, 0) + 1
    
    logger.info(f"STATS: [{guild_id}] Game started with {len(players)} players")

def record_game_end(guild_id, winner):
    """Record when a game ends with a winner."""
    game_state = get_game_state(guild_id)
    game_state['stats']['games_completed'] += 1
    
    # Track winner
    winner_id = winner.id
    game_state['stats']['player_wins'][winner_id] = game_state['stats']['player_wins'].get(winner_id, 0) + 1
    
    logger.info(f"STATS: [{guild_id}] Game completed, winner: {winner.name}")

def record_game_abandoned(guild_id):
    """Record when a game is abandoned."""
    game_state = get_game_state(guild_id)
    if game_state['game_started']:  # Only count as abandoned if it actually started
        game_state['stats']['games_abandoned'] += 1
        logger.info(f"STATS: [{guild_id}] Game abandoned")

@tasks.loop(minutes=30)  # Run cleanup every 30 minutes
async def cleanup_inactive_games():
    """Remove games that haven't been active for a specified time."""
    current_time = time.time()
    inactive_threshold = 2 * 60 * 60  # 2 hours in seconds
    abandoned_threshold = 24 * 60 * 60  # 24 hours for completely abandoned games
    
    to_remove = []
    
    for guild_id, game_state in games.items():
        time_since_activity = current_time - game_state.get('last_activity', 0)
        time_since_creation = current_time - game_state.get('created_at', 0)
        
        # Remove if:
        # 1. Game hasn't started and was created more than 24 hours ago
        # 2. Game was active but hasn't been touched in 2+ hours
        should_remove = False
        reason = ""
        
        if not game_state['game_started'] and time_since_creation > abandoned_threshold:
            should_remove = True
            reason = "never started and created 24+ hours ago"
        elif game_state['game_started'] and time_since_activity > inactive_threshold:
            should_remove = True
            reason = "inactive for 2+ hours"
        elif not game_state['game_started'] and time_since_activity > inactive_threshold:
            should_remove = True
            reason = "lobby inactive for 2+ hours"
        
        if should_remove:
            to_remove.append((guild_id, reason))
    
    # Remove the identified games
    for guild_id, reason in to_remove:
        del games[guild_id]
        print(f"🧹 Cleaned up game for guild {guild_id}: {reason}")
        
        # Try to notify the guild if possible
        try:
            guild = bot.get_guild(guild_id)
            if guild:
                # Find a general channel to send cleanup notification
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages:
                        embed = discord.Embed(
                            title="🧹 Game Cleanup",
                            description=f"The Coup game in this server was automatically cleaned up due to inactivity.\n\nReason: {reason}",
                            color=discord.Color.orange()
                        )
                        embed.set_footer(text="Use !start to begin a new game")
                        await channel.send(embed=embed)
                        break
        except Exception as e:
            print(f"⚠️ Could not notify guild {guild_id} about cleanup: {e}")
    
    if to_remove:
        print(f"🧹 Cleanup complete: removed {len(to_remove)} inactive games")
        print(f"📊 Active games remaining: {len(games)}")

@cleanup_inactive_games.before_loop
async def before_cleanup():
    """Wait for the bot to be ready before starting cleanup."""
    await bot.wait_until_ready()
    print("🧹 Game cleanup task started - will run every 30 minutes")

# Start the cleanup task when the bot starts
@bot.event
async def on_ready():
    """Called when the bot is ready."""
    logger.info(f"SYSTEM: {bot.user} connected to Discord!")
    logger.info(f"SYSTEM: Bot active in {len(bot.guilds)} servers")
    
    # Log all connected guilds
    for guild in bot.guilds:
        logger.debug(f"SYSTEM: Connected to guild [{guild.name}] (ID: {guild.id})")
    
    # Start the cleanup task
    if not cleanup_inactive_games.is_running():
        cleanup_inactive_games.start()

@bot.event
async def on_guild_join(guild):
    """Log when bot joins a new guild."""
    logger.info(f"SYSTEM: Joined new guild [{guild.name}] (ID: {guild.id}) - Total guilds: {len(bot.guilds)}")

@bot.event
async def on_guild_remove(guild):
    """Log when bot is removed from a guild and clean up its game state."""
    logger.info(f"SYSTEM: Removed from guild [{guild.name}] (ID: {guild.id}) - Total guilds: {len(bot.guilds)}")
    
    # Clean up any game state for this guild
    if guild.id in games:
        del games[guild.id]
        logger.info(f"CLEANUP: Removed game state for [{guild.name}]")
        
# NEW: Admin command to manually trigger cleanup or check status
@bot.command(name="cleanup_status")
async def cleanup_status(ctx):
    """Show cleanup status and manually trigger if needed (bot admin only)."""
    # Simple check - you might want to make this more sophisticated
    if not await bot.is_owner(ctx.author):
        return
    
    current_time = time.time()
    total_games = len(games)
    
    if total_games == 0:
        await ctx.send("📊 No active games to clean up.")
        return
    
    inactive_info = []
    for guild_id, game_state in games.items():
        time_since_activity = current_time - game_state.get('last_activity', 0)
        hours_inactive = time_since_activity / 3600
        
        guild_name = "Unknown"
        if guild := bot.get_guild(guild_id):
            guild_name = guild.name
        
        status = "🟢 Active" if hours_inactive < 1 else "🟡 Inactive" if hours_inactive < 2 else "🔴 Very Inactive"
        inactive_info.append(f"{status} **{guild_name}**: {hours_inactive:.1f}h inactive")
    
    embed = discord.Embed(
        title="🧹 Game Cleanup Status",
        description=f"**Total Games:** {total_games}\n\n" + "\n".join(inactive_info),
        color=discord.Color.blue()
    )
    embed.set_footer(text="Games are cleaned after 2+ hours of inactivity")
    await ctx.send(embed=embed)

@bot.command(name="force_cleanup")
async def force_cleanup(ctx):
    """Manually trigger game cleanup (bot admin only)."""
    if not await bot.is_owner(ctx.author):
        return
    
    before_count = len(games)
    await cleanup_inactive_games()
    after_count = len(games)
    
    cleaned = before_count - after_count
    await ctx.send(f"🧹 Manual cleanup complete: removed {cleaned} games, {after_count} remaining.")

# Visual styling constants
COLORS = {
    'gain': discord.Color.gold(),
    'loss': discord.Color.red(), 
    'block': discord.Color.orange(),
    'success': discord.Color.green(),
    'special': discord.Color.purple(),
    'info': discord.Color.blue(),
    'warning': discord.Color.orange(),
    'turn': discord.Color.blue()
}

# Card-to-image mapping
card_images = {
    "Duke": "https://i.imgur.com/QCU6dxS.png",
    "Assassin": "https://i.imgur.com/LrUYiix.png",
    "Captain": "https://i.imgur.com/M2VbuYy.png",
    "Ambassador": "https://i.imgur.com/og1XpMZ.png",
    "Contessa": "https://i.imgur.com/IUdg094.png"
}

# [REST OF YOUR ORIGINAL CODE CONTINUES HERE - I'm showing just the cleanup additions]
# Visual helper functions
def create_separator(text):
    """Create a visual separator line."""
    return f"🎮 ═══ {text.upper()} ═══ 🎮"

def create_action_result(action, player, target=None, details=None):
    """Create visually appealing action result."""
    player_name = getattr(player, 'name', 'Unknown Player')
    
    if target:
        target_name = getattr(target, 'name', 'Unknown Player')
        return f"⚔️ **{player_name}** → **{target_name}** • {action.upper()}"
    else:
        return f"🎯 **{player_name}** • {action.upper()}"

# Helper functions
def shuffle_deck(guild_id):
    """Shuffle the deck for a specific guild."""
    game_state = get_game_state(guild_id)  # This updates activity
    random.shuffle(game_state['court_deck'])

def deal_cards(guild_id):
    """Deal cards to players in a specific guild."""
    game_state = get_game_state(guild_id)
    
    # Check if we have enough cards
    total_cards_needed = len(game_state['players']) * 2
    if len(game_state['court_deck']) < total_cards_needed:
        logger.error(f"DECK_ERROR: Not enough cards! Need {total_cards_needed}, have {len(game_state['court_deck'])}")
        return False
    
    for player in game_state['players']:
        if len(game_state['court_deck']) < 2:
            logger.error(f"DECK_ERROR: Ran out of cards while dealing to {getattr(player, 'name', 'Unknown')}")
            return False
        game_state['players'][player]["cards"] = [game_state['court_deck'].pop(), game_state['court_deck'].pop()]
    
    return True

async def lose_influence_with_reveal(ctx, player, reason="loses"):
    """Make a player lose one character card, add it to discarded pile, and show the card lost."""
    game_state = get_game_state(ctx.guild.id)
    
    if len(game_state['players'][player]["cards"]) == 0:
        return False  # Player is already out
    
    card_lost = game_state['players'][player]["cards"].pop()
    game_state['discarded_cards'].append(card_lost)  # Add to visible discard pile
    
    # Show the card that was lost with image
    embed = await create_embed(
        create_separator("💀 CARD LOST! 💀"),
        f"**{player.name}** {reason} their **{card_lost}**!",
        COLORS['loss'],
        image_url=card_images[card_lost]
    )
    await ctx.send(embed=embed)
    
    return card_lost

def is_player_alive(guild_id, player):
    """Check if a player is still in the game."""
    game_state = get_game_state(guild_id)
    
    # First check if player is in the game at all
    if player not in game_state['players']:
        return False
    
    # Then check if they have cards
    return len(game_state['players'][player]["cards"]) > 0



def check_win_condition(guild_id):
    """Check if only one player remains."""
    game_state = get_game_state(guild_id)
    alive_players = [player for player in game_state['players'] if is_player_alive(guild_id, player)]
    if len(alive_players) == 1:
        return alive_players[0]
    return None

def get_next_player(guild_id, current):
    """Get the next alive player in turn order."""
    game_state = get_game_state(guild_id)
    player_list = list(game_state['players'].keys())
    current_index = player_list.index(current)
    next_index = (current_index + 1) % len(player_list)
    while not is_player_alive(guild_id, player_list[next_index]):
        next_index = (next_index + 1) % len(player_list)
    return player_list[next_index]

def check_bot_permissions(ctx):
    """Check if bot has required permissions in this channel."""
    if not ctx.guild:
        return True  # DM channels always work
    
    permissions = ctx.channel.permissions_for(ctx.guild.me)
    required_permissions = {
        'send_messages': 'Send Messages',
        'embed_links': 'Embed Links', 
        'add_reactions': 'Add Reactions',
        'manage_messages': 'Manage Messages'
    }
    
    missing = []
    for perm_name, display_name in required_permissions.items():
        if not getattr(permissions, perm_name):
            missing.append(display_name)
    
    if missing:
        logger.error(f"PERMISSIONS: Missing {missing} in [{ctx.guild.name}] #{ctx.channel.name}")
        return False, missing
    
    return True, []

async def send_permission_error(ctx, missing_permissions):
    """Send permission error message (fallback method)."""
    try:
        # Try to send a simple message (no embeds in case embed_links is missing)
        message = f"❌ **Missing Permissions**\n\nI need these permissions to work properly:\n• " + "\n• ".join(missing_permissions)
        await ctx.send(message)
    except discord.Forbidden:
        # If we can't even send messages, there's nothing we can do
        logger.error(f"PERMISSIONS: Cannot send messages in {ctx.guild.name} #{ctx.channel.name}")

async def create_embed(title, description, color, fields=None, image_url=None):
    """Helper function to create embeds consistently with error handling."""
    try:
        embed = discord.Embed(title=title, description=description, color=color)
        if fields:
            for field in fields:
                embed.add_field(name=field["name"], value=field["value"], inline=field.get("inline", False))
        if image_url:
            embed.set_image(url=image_url)
        return embed
    except Exception as e:
        print(f"⚠️ Error creating embed: {e}")
        # Return a simple fallback embed
        return discord.Embed(title="Error", description="An error occurred creating this message.", color=discord.Color.red())

async def send_embed(ctx, title, description, color, fields=None, image_url=None):
    """Helper function to send embeds quickly with error handling and rate limit protection."""
    try:
        embed = await create_embed(title, description, color, fields, image_url)
        return await safe_send_with_retry(ctx, embed=embed)
    except discord.HTTPException as e:
        logger.error(f"[{ctx.guild.name if ctx.guild else 'DM'}] EMBED_ERROR: Error sending embed: {e}")
        # Fallback to simple text message
        try:
            return await safe_send_with_retry(ctx, content=f"**{title}**\n{description}")
        except discord.HTTPException:
            logger.error(f"[{ctx.guild.name if ctx.guild else 'DM'}] SEND_ERROR: Could not send fallback message either")
            return None

async def send_error(ctx, title, description, auto_delete=True):
    """Send error message with consistent styling and optional auto-delete."""
    message = await send_embed(ctx, title, description, discord.Color.red())
    if auto_delete and message:
        # Delete error messages after 20 seconds
        try:
            await message.delete(delay=20)
        except discord.HTTPException:
            pass  # Message might already be deleted
    return message

async def send_success(ctx, title, description):
    """Send success message with consistent styling."""
    return await send_embed(ctx, title, description, discord.Color.green())

async def send_info(ctx, title, description):
    """Send info message with consistent styling."""
    return await send_embed(ctx, title, description, discord.Color.blue())

async def safe_send_dm(user, embed_or_content, fallback_message=None):
    """Safely send DM with comprehensive error handling and retry logic."""
    max_retries = 3
    retry_delay = 1
    
    for attempt in range(max_retries):
        try:
            if isinstance(embed_or_content, discord.Embed):
                await user.send(embed=embed_or_content)
            else:
                await user.send(embed_or_content)
            logger.debug(f"DM_SUCCESS: Sent DM to {user.name}")
            return True, None
        except discord.Forbidden:
            logger.warning(f"DM_FAILED: {user.name} has DMs disabled or blocked")
            return False, "DMs are disabled or blocked"
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limited
                retry_after = getattr(e, 'retry_after', 1)
                logger.warning(f"DM_RATE_LIMITED: Waiting {retry_after}s before retry to {user.name}")
                await asyncio.sleep(retry_after)
                continue
            elif attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
                continue
            logger.error(f"DM_ERROR: Failed to send DM to {user.name}: {e}")
            return False, f"Discord API error: {e}"
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                continue
            return False, f"Unexpected error: {e}"
    
    return False, "Failed after multiple retries"
async def handle_dm_failure(ctx, user, error_reason, action_description="receive important information"):
    """Handle DM failures with user-friendly feedback and guidance."""
    error_embed = await create_embed(
        "📬 DM Error",
        f"**{user.name}**, I couldn't send you a DM to {action_description}.",
        discord.Color.red(),
        [
            {
                "name": "❌ Error",
                "value": f"Reason: {error_reason}",
                "inline": False
            },
            {
                "name": "🔧 How to Fix",
                "value": "1. **Enable DMs**: Go to User Settings → Privacy & Safety → Allow direct messages from server members\n"
                        "2. **Unblock the bot**: Make sure you haven't blocked this bot\n"
                        "3. **Try again**: Use the command again after fixing settings",
                "inline": False
            },
            {
                "name": "⚠️ Impact",
                "value": "You cannot play Coup without DMs enabled, as your cards must be kept secret!",
                "inline": False
            }
        ]
    )
    
    try:
        await ctx.send(embed=error_embed)
    except discord.HTTPException:
        # Fallback to simple message if embed fails
        await ctx.send(f"❌ {user.mention}, I couldn't DM you! Please enable DMs to play Coup.")

async def send_player_cards(player, cards, ctx=None):
    """Send cards to a player via DM with comprehensive error handling and fallbacks."""
    
    # Card descriptions with commands and strategic info
    card_descriptions = {
        "Duke": {
            "description": "👑 **The Duke** - Master of Taxation & Foreign Affairs",
            "abilities": "• Use `!tax` to take **3 coins** (can be challenged)\n• **Block foreign aid** attempts from other players\n• Great for building wealth quickly!",
            "strategy": "💡 **Strategy:** Perfect for accumulating coins fast. Claim Duke to block others' foreign aid even if you don't have it!"
        },
        "Assassin": {
            "description": "🗡️ **The Assassin** - Silent but Deadly",
            "abilities": "• Use `!assassinate <target>` to eliminate a player for **3 coins**\n• Can be blocked by Contessa (the only defense!)\n• Cheaper alternative to coup!",
            "strategy": "💡 **Strategy:** Eliminate threats early before they get 7 coins. Watch out for Contessa blocks!"
        },
        "Captain": {
            "description": "⚓ **The Captain** - Master of the Seas & Theft",
            "abilities": "• Use `!steal <target>` to take **2 coins** from another player\n• **Block steal attempts** against you\n• Aggressive coin acquisition!",
            "strategy": "💡 **Strategy:** Great for slowing down rich opponents while boosting your own wealth. Can both steal AND defend!"
        },
        "Ambassador": {
            "description": "🤝 **The Ambassador** - Diplomatic Exchange Specialist",
            "abilities": "• Use `!exchange` to swap cards with the deck\n• **Block steal attempts** (same as Captain)\n• Get better cards when needed!",
            "strategy": "💡 **Strategy:** Perfect for getting the cards you need. Also defends against stealing like Captain!"
        },
        "Contessa": {
            "description": "🛡️ **The Contessa** - Guardian Against Assassination",
            "abilities": "• **Block assassination attempts** - the only defense!\n• Cannot initiate actions, but invaluable for survival\n• Your life insurance policy!",
            "strategy": "💡 **Strategy:** Keep this secret! It's your only defense against assassinations. Bluff having it when targeted!"
        }
    }
    
    # Send a welcome header
    header_embed = await create_embed(
        "🎮 Your Starting Hand",
        f"Welcome to Coup, **{player.name}**! You've been dealt **{len(cards)}** powerful characters. Here's your hand:",
        discord.Color.purple()
    )
    
    success, error_reason = await safe_send_dm(player, header_embed)
    if not success:
        if ctx:
            await handle_dm_failure(ctx, player, error_reason, "receive your starting cards")
        raise discord.Forbidden(f"Failed to send DM to {player.name}: {error_reason}")
    
    # Send each card as a detailed embed with image
    for i, card in enumerate(cards, 1):
        card_info = card_descriptions[card]
        
        card_embed = await create_embed(
            f"Card {i}: {card_info['description']}",
            f"{card_info['abilities']}\n\n{card_info['strategy']}",
            discord.Color.green(),
            image_url=card_images[card]
        )
        
        success, error_reason = await safe_send_dm(player, card_embed)
        if not success:
            if ctx:
                await handle_dm_failure(ctx, player, error_reason, f"receive card {i} details")
            # Continue trying to send remaining cards
            continue
    
    # Send strategy footer with bluffing tips
    summary_cards = ", ".join(cards)
    footer_embed = await create_embed(
        "🎯 Advanced Strategy Tips",
        f"**Your hand:** {summary_cards}\n\n"
        "🎭 **Master of Deception:**\n"
        "• You can claim to have ANY card, even if you don't!\n"
        "• Mix truth with lies to confuse opponents\n"
        "• Watch for patterns in other players' claims\n\n"
        "⚔️ **Challenge Wisely:**\n"
        "• Challenge when you think someone is bluffing\n"
        "• Wrong challenges cost you a card!\n"
        "• Count discarded cards to improve your odds\n\n"
        "🛡️ **Defensive Play:**\n"
        "• Block actions even without the right card (risky!)\n"
        "• Keep at least one card for survival\n"
        "• Don't reveal your hand through your actions\n\n"
        "Good luck, and may the best liar win! 🍀",
        discord.Color.gold()
    )
    footer_embed.set_footer(text="🤫 Keep these cards secret! Your poker face starts now...")
    
    success, error_reason = await safe_send_dm(player, footer_embed)
    if not success and ctx:
        await handle_dm_failure(ctx, player, error_reason, "receive strategy tips")

async def send_cards_update(guild_id, player, ctx=None):
    """Send updated cards to player with comprehensive error handling."""
    game_state = get_game_state(guild_id)
    
    # Card descriptions with commands and strategic info (same as initial dealing)
    card_descriptions = {
        "Duke": {
            "description": "👑 **The Duke** - Master of Taxation & Foreign Affairs",
            "abilities": "• Use `!tax` to take **3 coins** (can be challenged)\n• **Block foreign aid** attempts from other players\n• Great for building wealth quickly!",
            "strategy": "💡 **Strategy:** Perfect for accumulating coins fast. Claim Duke to block others' foreign aid even if you don't have it!"
        },
        "Assassin": {
            "description": "🗡️ **The Assassin** - Silent but Deadly",
            "abilities": "• Use `!assassinate <target>` to eliminate a player for **3 coins**\n• Can be blocked by Contessa (the only defense!)\n• Cheaper alternative to coup!",
            "strategy": "💡 **Strategy:** Eliminate threats early before they get 7 coins. Watch out for Contessa blocks!"
        },
        "Captain": {
            "description": "⚓ **The Captain** - Master of the Seas & Theft",
            "abilities": "• Use `!steal <target>` to take **2 coins** from another player\n• **Block steal attempts** against you\n• Aggressive coin acquisition!",
            "strategy": "💡 **Strategy:** Great for slowing down rich opponents while boosting your own wealth. Can both steal AND defend!"
        },
        "Ambassador": {
            "description": "🤝 **The Ambassador** - Diplomatic Exchange Specialist",
            "abilities": "• Use `!exchange` to swap cards with the deck\n• **Block steal attempts** (same as Captain)\n• Get better cards when needed!",
            "strategy": "💡 **Strategy:** Perfect for getting the cards you need. Also defends against stealing like Captain!"
        },
        "Contessa": {
            "description": "🛡️ **The Contessa** - Guardian Against Assassination",
            "abilities": "• **Block assassination attempts** - the only defense!\n• Cannot initiate actions, but invaluable for survival\n• Your life insurance policy!",
            "strategy": "💡 **Strategy:** Keep this secret! It's your only defense against assassinations. Bluff having it when targeted!"
        }
    }
    
    # Send a welcome header
    header_embed = await create_embed(
        "🔄 Your Updated Hand",
        f"Your hand has been updated, **{player.name}**! You now have **{len(game_state['players'][player]['cards'])}** powerful characters. Here's your current hand:",
        discord.Color.purple()
    )
    
    success, error_reason = await safe_send_dm(player, header_embed)
    if not success:
        if ctx:
            await handle_dm_failure(ctx, player, error_reason, "receive your updated cards")
        return False
    
    # Send each card as a detailed embed with image
    for i, card in enumerate(game_state['players'][player]['cards'], 1):
        card_info = card_descriptions[card]
        
        card_embed = await create_embed(
            f"Card {i}: {card_info['description']}",
            f"{card_info['abilities']}\n\n{card_info['strategy']}",
            discord.Color.green(),
            image_url=card_images[card]
        )
        
        success, error_reason = await safe_send_dm(player, card_embed)
        if not success:
            if ctx:
                await handle_dm_failure(ctx, player, error_reason, f"receive updated card {i} details")
            continue
    
    # Send simple footer with new hand summary
    summary_cards = ", ".join(game_state['players'][player]['cards'])
    footer_embed = await create_embed(
        "🔄 Hand Updated",
        f"**Your new hand:** {summary_cards}\n\n"
        "Your cards have been updated! Continue playing with your new hand.",
        discord.Color.gold()
    )
    footer_embed.set_footer(text="🤫 Keep these cards secret!")
    
    success, error_reason = await safe_send_dm(player, footer_embed)
    if not success and ctx:
        await handle_dm_failure(ctx, player, error_reason, "receive hand summary")
    
    return True

async def safe_send_with_retry(ctx_or_channel, content=None, embed=None, max_retries=3):
    """Send message with automatic retry on rate limits."""
    for attempt in range(max_retries):
        try:
            if embed:
                return await ctx_or_channel.send(embed=embed)
            else:
                return await ctx_or_channel.send(content)
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limited
                retry_after = getattr(e, 'retry_after', 1)
                logger.warning(f"RATE_LIMIT: Hit rate limit, retrying in {retry_after}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(retry_after)
                if attempt == max_retries - 1:
                    logger.error(f"RATE_LIMIT: Failed after {max_retries} attempts")
                    raise
            else:
                # Not a rate limit error, re-raise immediately
                raise
        except Exception as e:
            logger.error(f"RATE_LIMIT: Unexpected error in safe_send: {e}")
            raise

async def safe_add_reactions(message, emojis, delay=0.25):
    """Add multiple reactions with rate limit protection."""
    for emoji in emojis:
        try:
            await message.add_reaction(emoji)
            if len(emojis) > 1:  # Only add delay if multiple reactions
                await asyncio.sleep(delay)
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = getattr(e, 'retry_after', 1)
                logger.warning(f"RATE_LIMIT: Reaction rate limited, waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                try:
                    await message.add_reaction(emoji)
                except discord.HTTPException:
                    logger.warning(f"RATE_LIMIT: Failed to add reaction {emoji} after retry")
            else:
                logger.warning(f"RATE_LIMIT: Failed to add reaction {emoji}: {e}")

async def safe_send_multiple_dms(players, send_function, delay=0.5):
    """Send DMs to multiple players with rate limit protection."""
    failed_players = []
    
    for i, player in enumerate(players):
        try:
            success = await send_function(player)
            if not success:
                failed_players.append(player)
            
            # Add delay between DMs (except for the last one)
            if i < len(players) - 1:
                await asyncio.sleep(delay)
                
        except Exception as e:
            logger.error(f"RATE_LIMIT: Failed to send DM to {player.name}: {e}")
            failed_players.append(player)
    
    return failed_players

async def dramatic_countdown(ctx, title, description, color, seconds=3):
    """Create a dramatic countdown effect for big moments."""
    embed = await create_embed(title, description, color)
    message = await ctx.send(embed=embed)
    
    for i in range(seconds, 0, -1):
        await asyncio.sleep(1)
        countdown_embed = await create_embed(
            f"{title} - {i}",
            f"{description}\n\n{'⏰' * i} **{i}** {'⏰' * i}",
            color
        )
        await message.edit(embed=countdown_embed)
    
    await asyncio.sleep(1)
    final_embed = await create_embed(
        title,
        f"{description}\n\n💥 **EXECUTE!** 💥",
        color
    )
    await message.edit(embed=final_embed)
    return message

async def reveal_card_with_image(ctx, player, card, reason="reveals"):
    """Show a card reveal with image and dramatic effect."""
    embed = await create_embed(
        create_separator("🃏 CARD REVEALED! 🃏"),
        f"**{player.name}** {reason} the **{card}**!",
        COLORS['special'],
        image_url=card_images[card]
    )
    await ctx.send(embed=embed)

async def enhanced_turn_announcement(ctx, player, is_forced_coup=False):
    """Enhanced turn announcement with better visibility and None checking."""
    if not player:
        logger.error("PLAYER_ERROR: Cannot announce turn for None player")
        await ctx.send("❌ **Game Error**: Cannot determine whose turn it is. Game may need to be restarted.")
        return
    
    game_state = get_game_state(ctx.guild.id)
    
    # Verify player still exists in Discord
    discord_user = ctx.guild.get_member(player.id) if hasattr(player, 'id') else None
    if not discord_user:
        logger.warning(f"PLAYER_ERROR: Turn announcement for user who left: {getattr(player, 'name', 'Unknown')}")
        await ctx.send(f"⚠️ **Player Left**: {getattr(player, 'name', 'Unknown Player')} has left the server.")
        return
    
    player_name = getattr(player, 'name', 'Unknown Player')
    player_mention = getattr(player, 'mention', player_name)
    player_coins = game_state['players'][player]["coins"] if player in game_state['players'] else 0
    player_cards = len(game_state['players'][player]["cards"]) if player in game_state['players'] else 0
    
    if is_forced_coup:
        # Special dramatic announcement for forced coup
        embed = await create_embed(
            create_separator("⚔️ FORCED COUP TURN ⚔️"),
            f"**{player_mention}** has {player_coins} coins and **MUST COUP!**",
            COLORS['warning'],
            [{"name": "💰 Coins", "value": f"{player_coins}", "inline": True},
             {"name": "🃏 Cards", "value": f"{player_cards}", "inline": True},
             {"name": "🎯 Required Action", "value": "`!coup <target>`", "inline": False}]
        )
    else:
        # Regular turn with enhanced styling
        embed = await create_embed(
            create_separator(f"👑 {player_name.upper()}'S TURN 👑"),
            f"It's **{player_mention}**'s turn to take action!",
            COLORS['turn'],
            [{"name": "💰 Coins", "value": f"{player_coins}", "inline": True},
             {"name": "🃏 Cards", "value": f"{player_cards}", "inline": True},
             {"name": "🎯 Actions", "value": "`!actions` for options", "inline": False}]
        )
    
    return await ctx.send(embed=embed)

async def wait_for_reaction(ctx, action_message, valid_emojis, timeout=5, action_initiator=None):
    """Wait for a reaction within specified seconds with teacup countdown. Race condition safe."""
    game_state = get_game_state(ctx.guild.id)
    
    # Create a lock to prevent race conditions
    reaction_lock = asyncio.Lock()
    processed_reactions = set()  # Track message+emoji+user combinations to prevent duplicates
    
    # Add reactions first with rate limit protection
    await safe_add_reactions(action_message, valid_emojis)
    
    # Small delay to ensure reactions are added before we start listening
    await asyncio.sleep(0.3)

    async def check_existing_reactions():
        """Check for existing reactions on the message."""
        try:
            # Always fetch fresh message to get current reactions
            fresh_message = await ctx.channel.fetch_message(action_message.id)
            
            for reaction in fresh_message.reactions:
                if str(reaction.emoji) in valid_emojis:
                    # Get all users who reacted (excluding bots)
                    async for user in reaction.users():
                        if (user in game_state['players'] and 
                            is_player_alive(ctx.guild.id, user) and  # Must be alive
                            user != action_initiator and  # Cannot react to your own actions
                            not user.bot and 
                            (fresh_message.id, str(reaction.emoji), user.id) not in processed_reactions):
                            
                            # Mark this reaction as processed
                            processed_reactions.add((fresh_message.id, str(reaction.emoji), user.id))
                            logger.info(f"[{ctx.guild.name}] REACTION_DETECTED: {user.name} reacted {reaction.emoji}")
                            return str(reaction.emoji), user
            
            return None, None
        except discord.NotFound:
            # Message was deleted
            return None, None
        except Exception as e:
            print(f"⚠️ Error checking existing reactions: {e}")
            return None, None

    def check_new_reaction(reaction, user):
        """Check function for new reactions."""
        reaction_key = (reaction.message.id, str(reaction.emoji), user.id)
        return (
            user in game_state['players']  # Must be in the game
            and is_player_alive(ctx.guild.id, user)  # Must be alive
            and user != action_initiator  # Cannot react to your own actions
            and reaction.message.id == action_message.id
            and str(reaction.emoji) in valid_emojis
            and not user.bot
            and reaction_key not in processed_reactions
        )

    # Countdown timer with teacups
    teacups_full = "🍵" * timeout
    teacups_empty = "⚫" * 0
    countdown_message = await ctx.send(f"Time remaining: {teacups_full}{teacups_empty}")

    # Check for existing reactions first
    async with reaction_lock:
        emoji, user = await check_existing_reactions()
        if emoji and user:
            try:
                await countdown_message.delete()
            except discord.NotFound:
                pass
            return emoji, user

    # Now wait for new reactions with countdown
    for i in range(timeout, -1, -1):
        try:
            # Wait for new reaction with 1 second timeout
            reaction, user = await bot.wait_for("reaction_add", timeout=1.0, check=check_new_reaction)
            
            # Mark this reaction as processed
            async with reaction_lock:
                reaction_key = (reaction.message.id, str(reaction.emoji), user.id)
                if reaction_key not in processed_reactions:
                    processed_reactions.add(reaction_key)
                    try:
                        await countdown_message.delete()
                    except discord.NotFound:
                        pass
                    return str(reaction.emoji), user
                    
        except asyncio.TimeoutError:
            # During each countdown second, also check for existing reactions
            async with reaction_lock:
                emoji, user = await check_existing_reactions()
                if emoji and user:
                    try:
                        await countdown_message.delete()
                    except discord.NotFound:
                        pass
                    return emoji, user
            
            # Update countdown display
            if i == 0:
                teacups_full = ""
                teacups_empty = "⚫" * timeout
            else:
                teacups_full = "🍵" * i
                teacups_empty = "⚫" * (timeout - i)
            
            try:
                await countdown_message.edit(content=f"Time remaining: {teacups_full}{teacups_empty}")
            except discord.NotFound:
                # Countdown message was deleted, create a new one
                try:
                    countdown_message = await ctx.send(f"Time remaining: {teacups_full}{teacups_empty}")
                except Exception:
                    # If we can't create countdown message, continue without it
                    pass

    # Clean up countdown message
    try:
        await countdown_message.delete()
    except discord.NotFound:
        pass
        
    return None, None

async def handle_player_elimination(ctx, player):
    """Handle player elimination and check win condition. Returns (eliminated, game_ended, next_player)"""
    game_state = get_game_state(ctx.guild.id)
    
    if len(game_state['players'][player]["cards"]) == 0:
        await send_embed(ctx, "💀 Out of the Game", 
                        f"**{player.name}** has no more influence and is out of the game!",
                        discord.Color.red())
        await asyncio.sleep(1)
        
        # Get next player BEFORE deleting the current player
        next_player_candidate = get_next_player(ctx.guild.id, player)
        del game_state['players'][player]
        
        winner = check_win_condition(ctx.guild.id)
        if winner:
            await send_embed(ctx, "🏆 Game Over",
                           f"**{winner.name}** is the last player standing and wins the game!",
                           discord.Color.gold())
            
            record_game_end(ctx.guild.id, winner)
            
            # Reveal winner's hand for fun
            await reveal_winner_hand(ctx, winner)
            
            # End the game for this guild
            game_state['game_started'] = False
            game_state['players'] = {}
            game_state['current_player'] = None
            game_state['court_deck'] = ["Duke", "Assassin", "Contessa", "Captain", "Ambassador"] * 3
            game_state['discarded_cards'] = []
            game_state['game_history'] = []
            shuffle_deck(ctx.guild.id)
            return True, True, None  # eliminated, game_ended, next_player
        return True, False, next_player_candidate  # eliminated, not game_ended, next_player
    return False, False, None  # not eliminated, not game_ended, no next_player

async def handle_card_swap(ctx, player, card_name):
    """Handle swapping a specific card with deck - card goes to bottom, player gets top card."""
    game_state = get_game_state(ctx.guild.id)
    
    # Check if deck has cards before swapping
    if len(game_state['court_deck']) < 1:
        logger.error(f"DECK_ERROR: No cards in deck for swap! Player: {getattr(player, 'name', 'Unknown')}")
        await send_error(ctx, "🚫 Deck Error", 
                        "No cards remaining in deck for card swap! This is a serious bug.")
        return
    
    # Remove the revealed card from player's hand
    if card_name in game_state['players'][player]["cards"]:
        game_state['players'][player]["cards"].remove(card_name)
    else:
        logger.error(f"DECK_ERROR: Player {getattr(player, 'name', 'Unknown')} doesn't have {card_name} to swap")
        return
    
    # Put the revealed card at the bottom of the deck
    game_state['court_deck'].insert(0, card_name)
    
    # Player draws the top card from the deck
    game_state['players'][player]["cards"].append(game_state['court_deck'].pop())
    
    await send_cards_update(ctx.guild.id, player)

async def reveal_winner_hand(ctx, winner):
    """Reveal the winner's final hand to see if they were bluffing."""
    game_state = get_game_state(ctx.guild.id)
    winner_cards = game_state['players'][winner]['cards']
    
    # Create dramatic winner reveal
    await asyncio.sleep(2)  # Build suspense
    
    header_embed = await create_embed(
        "🏆 ═══ WINNER'S HAND REVEALED! ═══ 🏆",
        f"Let's see what **{winner.name}** was actually holding...\n"
        f"Did they lie? Did they tell the truth? The cards don't lie! 🃏",
        discord.Color.gold()
    )
    await ctx.send(embed=header_embed)
    
    await asyncio.sleep(1)
    
    # Send each card with image
    for i, card in enumerate(winner_cards, 1):
        card_embed = await create_embed(
            f"Card {i}: {card}",
            f"**{winner.name}** had the **{card}**!",
            discord.Color.purple(),
            image_url=card_images[card]
        )
        await ctx.send(embed=card_embed)
        await asyncio.sleep(1)
    
    # Fun summary
    cards_text = " & ".join(winner_cards)
    summary_embed = await create_embed(
        "🎭 The Truth Revealed!",
        f"**{winner.name}** won with: **{cards_text}**\n\n"
        "Were they masters of deception or did they play it straight? 🤔\n"
        "The game of lies has ended! 🎉",
        discord.Color.gold()
    )
    summary_embed.set_footer(text="Thanks for playing Coup! • The ultimate bluffing game")
    await ctx.send(embed=summary_embed)

async def handle_challenge(ctx, claimer, challenger, required_card):
    """Handle challenge logic and return (claim_legitimate, game_ended, eliminated_player, next_player_if_claimer_eliminated)."""
    game_state = get_game_state(ctx.guild.id)
    
    # Add dramatic challenge announcement
    await dramatic_countdown(
        ctx,
        "⚔️ CHALLENGE INITIATED ⚔️",
        f"**{challenger.name}** challenges **{claimer.name}**'s claim of being the **{required_card}**!",
        discord.Color.red(),
        3
    )
    
    log_game_action("challenge", ctx.guild.id, challenger, claimer, f"Challenged {required_card} claim")

    if required_card in game_state['players'][claimer]["cards"]:
        # Challenge failed - claimer has the card
        await reveal_card_with_image(ctx, claimer, required_card, "triumphantly reveals")
        await asyncio.sleep(1)
        
        await send_embed(ctx, f"✅ Challenge Failed",
                        f"**{claimer.name}** proves their claim! The {required_card} is shuffled back into the deck.",
                        discord.Color.green())
        await asyncio.sleep(1)
        
        # Swap the card
        await handle_card_swap(ctx, claimer, required_card)
        await send_embed(ctx, "🔄 Card Swapped",
                        f"**{claimer.name}** draws a new card from the deck. I've sent you a DM with your updated hand!",
                        discord.Color.blue())
        await asyncio.sleep(1)

        # Challenger loses a card for false challenge
        if len(game_state['players'][challenger]["cards"]) > 0:
            log_game_action("card_lost", ctx.guild.id, challenger, claimer, f"Lost card for false challenge")
            card_lost = await lose_influence_with_reveal(ctx, challenger, "loses a card for the false challenge and discards")
            await asyncio.sleep(1)
            
            eliminated, game_ended, next_player_result = await handle_player_elimination(ctx, challenger)
            return True, game_ended, challenger if eliminated else None, next_player_result
        
        return True, False, None, None  # claim_legitimate, not game_ended, no elimination, no next_player
    else:
        # Challenge succeeded - claimer was bluffing
        await send_embed(ctx, "🤥 Bluff Exposed!",
                        f"**{claimer.name}** was caught bluffing! They don't have the **{required_card}**!",
                        discord.Color.red())
        await asyncio.sleep(1)
        
        if len(game_state['players'][claimer]["cards"]) > 0:
            log_game_action("card_lost", ctx.guild.id, claimer, challenger, f"Lost card for failed bluff")
            card_lost = await lose_influence_with_reveal(ctx, claimer, "loses a card for bluffing and discards")
            await asyncio.sleep(1)
            
            eliminated, game_ended, next_player_result = await handle_player_elimination(ctx, claimer)
            return False, game_ended, claimer if eliminated else None, next_player_result
        
        return False, False, None, None  # claim_illegitimate, not game_ended, no elimination, no next_player

async def handle_block_challenge(ctx, blocker, challenger, valid_cards):
    """Handle challenge logic for blocks that can have multiple valid cards."""
    game_state = get_game_state(ctx.guild.id)
    
    # Add dramatic challenge announcement
    await dramatic_countdown(
        ctx,
        "⚔️ BLOCK CHALLENGE INITIATED ⚔️",
        f"**{challenger.name}** challenges **{blocker.name}**'s block claim!",
        discord.Color.red(),
        3
    )
    
    log_game_action("challenge", ctx.guild.id, challenger, blocker, f"Challenged block")

    # Check if blocker has any of the valid cards
    blocker_cards = game_state['players'][blocker]["cards"]
    valid_card_found = None
    for card in valid_cards:
        if card in blocker_cards:
            valid_card_found = card
            break

    if valid_card_found:
        # Challenge failed - blocker has a valid card
        await reveal_card_with_image(ctx, blocker, valid_card_found, "triumphantly reveals")
        await asyncio.sleep(1)
        
        await send_embed(ctx, f"✅ Challenge Failed",
                        f"**{blocker.name}** proves their claim! The {valid_card_found} is shuffled back into the deck.",
                        discord.Color.green())
        await asyncio.sleep(1)
        
        # Swap the card
        await handle_card_swap(ctx, blocker, valid_card_found)
        await send_embed(ctx, "🔄 Card Swapped",
                        f"**{blocker.name}** draws a new card from the deck.",
                        discord.Color.blue())
        await asyncio.sleep(1)

        # Challenger loses a card for false challenge
        if len(game_state['players'][challenger]["cards"]) > 0:
            log_game_action("card_lost", ctx.guild.id, challenger, blocker, f"Lost card for false challenge")
            card_lost = await lose_influence_with_reveal(ctx, challenger, "loses a card for the false challenge and discards")
            await asyncio.sleep(1)
            
            eliminated, game_ended, next_player_result = await handle_player_elimination(ctx, challenger)
            return True, game_ended, challenger if eliminated else None, next_player_result
        
        return True, False, None, None  # claim_legitimate, not game_ended, no elimination, no next_player
    else:
        # Challenge succeeded - blocker was bluffing
        await send_embed(ctx, "🤥 Block Bluff Exposed!",
                        f"**{blocker.name}** was caught bluffing! They don't have {' or '.join(valid_cards)}!",
                        discord.Color.red())
        await asyncio.sleep(1)
        
        if len(game_state['players'][blocker]["cards"]) > 0:
            log_game_action("card_lost", ctx.guild.id, blocker, challenger, f"Lost card for failed block bluff")
            card_lost = await lose_influence_with_reveal(ctx, blocker, "loses a card for bluffing and discards")
            await asyncio.sleep(1)
            
            eliminated, game_ended, next_player_result = await handle_player_elimination(ctx, blocker)
            return False, game_ended, blocker if eliminated else None, next_player_result
        
        return False, False, None, None  # claim_illegitimate, not game_ended, no elimination, no next_player

async def check_forced_coup(ctx, player):
    """Check if player has 10+ coins and send forced coup message."""
    game_state = get_game_state(ctx.guild.id)
    if game_state['players'][player]["coins"] >= 10:
        await enhanced_turn_announcement(ctx, player, is_forced_coup=True)
        return True
    return False

async def advance_turn(ctx, current_turn_player):
    """Advance to the next player's turn. Handles case where current player was eliminated."""
    game_state = get_game_state(ctx.guild.id)
    
    # Check if the current turn player is still in the game
    if current_turn_player not in game_state['players']:
        # Player was eliminated, we need to find who should be next
        player_list = list(game_state['players'].keys())
        if len(player_list) > 0:
            game_state['current_player'] = player_list[0]  # Start with first remaining player
        else:
            return  # No players left (shouldn't happen due to win condition checks)
    else:
        # Normal case - current player is still alive
        game_state['current_player'] = get_next_player(ctx.guild.id, current_turn_player)
    
    # SAFETY CHECK: Make sure current player still exists as a Discord user
    current_player = game_state['current_player']
    if not current_player:
        logger.error("PLAYER_ERROR: Current player is None after turn advance")
        await ctx.send("❌ **Game Error**: Current player not found. Game may need to be restarted.")
        return
    
    # Check if player still exists in Discord (not just the game)
    discord_user = ctx.guild.get_member(current_player.id) if hasattr(current_player, 'id') else None
    if not discord_user:
        logger.warning(f"PLAYER_ERROR: Current player {getattr(current_player, 'name', 'Unknown')} left the server")
        await ctx.send(f"⚠️ **Player Left**: {getattr(current_player, 'name', 'Unknown Player')} has left the server and will be eliminated.")
        
        # Remove the player and advance to next
        if current_player in game_state['players']:
            del game_state['players'][current_player]
            
        # Check win condition
        winner = check_win_condition(ctx.guild.id)
        if winner:
            await send_embed(ctx, "🏆 Game Over",
                           f"**{getattr(winner, 'name', 'Unknown Player')}** wins after other player left!",
                           discord.Color.gold())
            # Reset game
            game_state['game_started'] = False
            game_state['players'] = {}
            game_state['current_player'] = None
            return
        
        # Recursively advance to next player
        await advance_turn(ctx, current_player)
        return
    
    # Check if they're forced to coup first
    forced_coup = await check_forced_coup(ctx, game_state['current_player'])
    
    # If not forced coup, send enhanced turn announcement
    if not forced_coup:
        await enhanced_turn_announcement(ctx, game_state['current_player'], is_forced_coup=False)

async def validate_turn(ctx, player):
    """Check if it's the player's turn."""
    game_state = get_game_state(ctx.guild.id)
    if player != game_state['current_player']:
        await send_error(ctx, "🚫 Not Your Turn", "It's not your turn!")
        return False
    return True

async def validate_target(ctx, target):
    """Check if target is valid for actions."""
    game_state = get_game_state(ctx.guild.id)
    
    # First check if target is in the game at all
    if target not in game_state['players']:
        await send_error(ctx, "🚫 Not in Game", f"{target.name} is not part of the current game!")
        return False
    
    # Then check if they're still alive
    if not is_player_alive(ctx.guild.id, target):
        await send_error(ctx, "🚫 Target Eliminated", f"{target.name} has already been eliminated!")
        return False
    
    return True

async def validate_coins(ctx, player, required_coins, action_name):
    """Check if player has enough coins for an action."""
    game_state = get_game_state(ctx.guild.id)
    if game_state['players'][player]["coins"] < required_coins:
        await send_error(ctx, "🚫 Insufficient Coins", 
                        f"You need at least {required_coins} coins to {action_name}!")
        return False
    return True

async def validate_action_allowed(ctx, player, action_name):
    """Check if player can perform this action (10+ coins forces coup)."""
    game_state = get_game_state(ctx.guild.id)
    if game_state['players'][player]["coins"] >= 10 and action_name != "coup":
        await send_error(ctx, "🚫 Must Coup", 
                        f"You have {game_state['players'][player]['coins']} coins and must coup! Use `!coup <target>`")
        return False
    return True

async def validate_self_target(ctx, player, target, action_name):
    """Check if player is trying to target themselves."""
    if player == target:
        action_emojis = {
            "coup": "💥",
            "assassinate": "🗡️", 
            "steal": "💰"
        }
        emoji = action_emojis.get(action_name, "🚫")
        await send_error(ctx, f"{emoji} Cannot Target Yourself", 
                        f"You cannot {action_name} yourself! Choose a different target.")
        return False
    return True

# Commands
@bot.command(name="start")
async def start(ctx):
    # Check bot permissions first
    has_perms, missing = check_bot_permissions(ctx)
    if not has_perms:
        await send_permission_error(ctx, missing)
        return
    
    game_state = get_game_state(ctx.guild.id)

    if game_state['game_started']:
        await send_error(ctx, "🚫 Game Already in Progress", "A game is already in progress!")
        return

    # Reset game variables for this guild
    game_state['players'] = {}
    game_state['court_deck'] = ["Duke", "Assassin", "Contessa", "Captain", "Ambassador"] * 3
    game_state['discarded_cards'] = []  # Reset discarded cards
    game_state['game_history'] = []  # Reset game history
    shuffle_deck(ctx.guild.id)

    # Send join message
    fields = [{"name": "Join the Game", "value": "React with ✅ to join! You have 10 seconds to join."}]
    embed = await create_embed(
        "🎮 Coup - The Ultimate Bluffing Game!",
        "**Welcome to Coup!** 🎭\n\n"
        "Coup is an exciting game of **deception, strategy, and betrayal**! Here's how it works:\n\n"
        
        "🃏 **Your Secret Identity:**\n"
        "• You'll get 2 secret character cards (like Duke, Assassin, Captain)\n"
        "• Each character has special powers you can use on your turn\n"
        "• Keep your cards hidden from other players!\n\n"
        
        "💰 **Collect Coins & Take Actions:**\n"
        "• Gain coins each turn through income, foreign aid, or special abilities\n"
        "• Spend coins to eliminate other players (coup for 7 coins, assassinate for 3)\n"
        "• Use character powers like stealing coins or blocking other players\n\n"
        
        "🎭 **The Art of Bluffing:**\n"
        "• You can **claim to have ANY character** - even if you don't!\n"
        "• Other players can challenge your claims if they think you're lying\n"
        "• Get caught lying? Lose a card. Falsely accused? Your challenger loses a card!\n\n"
        
        "🏆 **How to Win:**\n"
        "• Eliminate other players by making them lose all their cards\n"
        "• Be the last player standing with at least 1 card remaining\n"
        "• Master the balance of truth, lies, and timing!\n\n"
        
        "🎯 **Perfect for:** Social deduction fans, poker players, and anyone who loves mind games!\n\n"
        "Ready to test your poker face? The game of lies begins now! 😈",
        discord.Color.blue(),
        fields,
        "https://m.media-amazon.com/images/I/71rycbSJlXL.jpg"
    )
    
    game_state['join_message'] = await ctx.send(embed=embed)
    await game_state['join_message'].add_reaction("✅")

    # Countdown timer
    countdown_message = await ctx.send("Time remaining: 🍵🍵🍵🍵🍵🍵🍵🍵🍵🍵")
    for i in range(10, -1, -1):
        await asyncio.sleep(1)
        teacups = "🍵" * i + "⚫" * (10 - i) if i > 0 else "⚫" * 10
        await countdown_message.edit(content=f"Time remaining: {teacups}")

    # Check who reacted
    join_message = await ctx.channel.fetch_message(game_state['join_message'].id)
    reactors = set()
    for reaction in join_message.reactions:
        if reaction.emoji == "✅":
            async for user in reaction.users():
                if not user.bot:
                    reactors.add(user)

    # Add reactors to the game
    for user in reactors:
        game_state['players'][user] = {"cards": [], "coins": 2}

    if len(game_state['players']) < 2:
        await send_error(ctx, "🚫 Not Enough Players", 
                        "Not enough players joined. The game requires at least 2 players.")
        return

    # Start the game
    game_state['game_started'] = True
    if not deal_cards(ctx.guild.id):
        # Deck error occurred
        await send_error(ctx, "🚫 Deck Error", 
                        "Not enough cards in the deck to start the game! This is a bug - please restart the bot.")
        game_state['game_started'] = False
        return
    
    # Randomize the player order for fairness
    player_list = list(game_state['players'].keys())
    random.shuffle(player_list)
    
    # Rebuild the players dict in the new randomized order
    shuffled_players = {}
    for player in player_list:
        shuffled_players[player] = game_state['players'][player]
    game_state['players'] = shuffled_players
    
    # Set the first player in the randomized order
    game_state['current_player'] = player_list[0]

    # Send cards to players with rate limit protection
    async def send_cards_to_player(player):
        """Helper function for safe_send_multiple_dms."""
        try:
            await send_player_cards(player, game_state['players'][player]['cards'], ctx)
            return True
        except discord.Forbidden:
            return False

    failed_players = await safe_send_multiple_dms(
        list(game_state['players'].keys()), 
        send_cards_to_player, 
        delay=0.7  # Longer delay for DMs
    )
    
    # Remove failed players from the game
    for player in failed_players:
        if player in game_state['players']:
            del game_state['players'][player]
    
    # Check if we still have enough players after DM failures
    if len(game_state['players']) < 2:
        await send_error(ctx, "🚫 Not Enough Players", 
                        "Not enough players can receive DMs. The game requires at least 2 players with DMs enabled.")
        game_state['game_started'] = False
        return

    await send_success(ctx, "🎉 Game Started!", "Each player has been dealt 2 cards.")
    
    # Add game start to history
    log_game_action("game_start", ctx.guild.id, list(game_state['players'].keys())[0], details=f"{len(game_state['players'])} players joined")
    record_game_start(ctx.guild.id, list(game_state['players'].keys()))
    
    # Enhanced first turn announcement
    forced_coup = await check_forced_coup(ctx, game_state['current_player'])
    if not forced_coup:
        await enhanced_turn_announcement(ctx, game_state['current_player'], is_forced_coup=False)

@bot.command(name="end")
async def end(ctx):
    game_state = get_game_state(ctx.guild.id)

    if not game_state['game_started']:
        await send_error(ctx, "🚫 No Game in Progress", "No game is currently in progress.")
        return

    record_game_abandoned(ctx.guild.id)
    # Reset all game variables for this guild
    game_state['game_started'] = False
    game_state['players'] = {}
    game_state['current_player'] = None
    game_state['court_deck'] = ["Duke", "Assassin", "Contessa", "Captain", "Ambassador"] * 3
    game_state['discarded_cards'] = []  # Reset discarded cards
    game_state['game_history'] = []  # Reset game history
    shuffle_deck(ctx.guild.id)

    await send_embed(ctx, "🛑 Game Ended",
                    "The game has been ended by an admin. All game data has been reset.",
                    discord.Color.red())

@bot.command(name="income")
async def income(ctx):
    if not await validate_turn(ctx, ctx.author):
        return
    if not await validate_action_allowed(ctx, ctx.author, "income"):
        return

    game_state = get_game_state(ctx.guild.id)
    old_coins = game_state['players'][ctx.author]["coins"]
    game_state['players'][ctx.author]["coins"] += 1
    new_coins = game_state['players'][ctx.author]["coins"]
    
    log_game_action("income", ctx.guild.id, ctx.author, details="Gained 1 coin")
    
    # Enhanced visual result
    embed = await create_embed(
        "💰 INCOME COLLECTED 💰",
        create_action_result("INCOME", ctx.author),
        COLORS['gain'],
        [{"name": "💰 Coins", "value": f"{old_coins} → **{new_coins}** (+1)", "inline": True}]
    )
    await ctx.send(embed=embed)
    await asyncio.sleep(1)
    await advance_turn(ctx, ctx.author)

@bot.command(name="foreign_aid")
async def foreign_aid(ctx):
    if not await validate_turn(ctx, ctx.author):
        return
    if not await validate_action_allowed(ctx, ctx.author, "foreign_aid"):
        return

    game_state = get_game_state(ctx.guild.id)
    log_game_action("foreign_aid_attempt", ctx.guild.id, ctx.author, details="Attempted foreign aid")
    
    fields = [{"name": "Block", "value": "React with 🚫 to block as **Duke** within 5 seconds."}]
    action_message = await send_embed(ctx, "💸 Foreign Aid",
                                     f"**{ctx.author.name}** is attempting to take foreign aid.",
                                     discord.Color.blue(), fields)

    emoji, blocker = await wait_for_reaction(ctx, action_message, ["🚫"], action_initiator=ctx.author)

    if emoji == "🚫":
        log_game_action("block_attempt", ctx.guild.id, blocker, ctx.author, "Blocked foreign aid as Duke")
        # Handle block attempt
        fields = [{"name": "Challenge", "value": "React with ❓ to challenge this claim within 5 seconds."}]
        challenge_message = await send_embed(ctx, "🛑 Block Attempt",
                                           f"**{blocker.name}** is blocking the foreign aid as the **Duke**.",
                                           discord.Color.orange(), fields)
        
        challenge_emoji, challenger = await wait_for_reaction(ctx, challenge_message, ["❓"], action_initiator=blocker)

        if challenge_emoji == "❓":
            claim_legitimate, game_ended, eliminated_player, next_player_result = await handle_challenge(ctx, blocker, challenger, "Duke")
            if game_ended:
                return
            
            # If challenge failed (blocker had Duke), block succeeds
            if claim_legitimate:
                log_game_action("foreign_aid_blocked", ctx.guild.id, ctx.author, blocker, "Foreign aid blocked by Duke")
                await send_info(ctx, "🛑 Block Succeeds", 
                               f"**{blocker.name}**'s block succeeds, and the foreign aid is canceled.")
            else:
                # Challenge succeeded, foreign aid proceeds
                old_coins = game_state['players'][ctx.author]["coins"]
                game_state['players'][ctx.author]["coins"] += 2
                new_coins = game_state['players'][ctx.author]["coins"]
                
                log_game_action("foreign_aid_success", ctx.guild.id, ctx.author, details="Gained 2 coins after failed block")
                
                embed = await create_embed(
                    "💸 FOREIGN AID SUCCESS! 💸",
                    create_action_result("FOREIGN AID", ctx.author),
                    COLORS['gain'],
                    [{"name": "💰 Coins", "value": f"{old_coins} → **{new_coins}** (+2)", "inline": True}]
                )
                await ctx.send(embed=embed)
                await asyncio.sleep(1)
        else:
            log_game_action("foreign_aid_blocked", ctx.guild.id, ctx.author, blocker, "Foreign aid blocked by Duke (unchallenged)")
            await send_info(ctx, "🛑 Block Succeeds",
                           f"No one challenged the block. **{blocker.name}**'s block succeeds, and the foreign aid is canceled.")
    else:
        # No block, foreign aid proceeds
        old_coins = game_state['players'][ctx.author]["coins"]
        game_state['players'][ctx.author]["coins"] += 2
        new_coins = game_state['players'][ctx.author]["coins"]
        
        log_game_action("foreign_aid_success", ctx.guild.id, ctx.author, details="Gained 2 coins (unblocked)")
        
        embed = await create_embed(
            "💸 FOREIGN AID SUCCESS! 💸",
            create_action_result("FOREIGN AID", ctx.author),
            COLORS['gain'],
            [{"name": "💰 Coins", "value": f"{old_coins} → **{new_coins}** (+2)", "inline": True}]
        )
        await ctx.send(embed=embed)
        await asyncio.sleep(1)

    # advance_turn now handles eliminated players safely
    await advance_turn(ctx, ctx.author)

@bot.command(name="coup")
async def coup(ctx, target: discord.Member):
    if not await validate_turn(ctx, ctx.author):
        return
    if not await validate_target(ctx, target):
        return
    if not await validate_self_target(ctx, ctx.author, target, "coup"):
        return
    if not await validate_coins(ctx, ctx.author, 7, "launch a coup"):
        return
    
    # Extra safety check
    game_state = get_game_state(ctx.guild.id)
    if target not in game_state['players']:
        await send_error(ctx, "🚫 Invalid Target", f"{target.name} is not in this game!")
        return

    game_state = get_game_state(ctx.guild.id)
    game_state['players'][ctx.author]["coins"] -= 7
    
    # Add to history
    log_game_action("coup", ctx.guild.id, ctx.author, target, f"Paid 7 coins")
    
    await send_embed(ctx, "💥 COUP LAUNCHED!",
                    f"**{ctx.author.name}** launches a coup against **{target.name}** for 7 coins!",
                    discord.Color.dark_red())
    
    await send_embed(ctx, "💸 Payment Deducted",
                    f"**{ctx.author.name}** pays 7 coins. Remaining coins: **{game_state['players'][ctx.author]['coins']}**",
                    discord.Color.orange())
    await asyncio.sleep(1)

    if len(game_state['players'][target]["cards"]) > 0:
        log_game_action("card_lost", ctx.guild.id, target, ctx.author, f"Lost card to coup")
        card_lost = await lose_influence_with_reveal(ctx, target, "is couped and must discard")
        await asyncio.sleep(1)
        
        eliminated, game_ended, _ = await handle_player_elimination(ctx, target)
        if game_ended:
            return

    await advance_turn(ctx, ctx.author)

@bot.command(name="assassinate")
async def assassinate(ctx, target: discord.Member):
    if not await validate_turn(ctx, ctx.author):
        return
    if not await validate_target(ctx, target):
        return
    if not await validate_self_target(ctx, ctx.author, target, "assassinate"):
        return
    if not await validate_coins(ctx, ctx.author, 3, "assassinate"):
        return
    if not await validate_action_allowed(ctx, ctx.author, "assassinate"):
        return
    
    # Extra safety check
    game_state = get_game_state(ctx.guild.id)
    if target not in game_state['players']:
        await send_error(ctx, "🚫 Invalid Target", f"{target.name} is not in this game!")
        return

    game_state = get_game_state(ctx.guild.id)
    game_state['players'][ctx.author]["coins"] -= 3
    
    # Add to history
    log_game_action("assassinate", ctx.guild.id, ctx.author, target, f"Paid 3 coins")
    
    await send_embed(ctx, "🗡️ ASSASSINATION ATTEMPT",
                    f"**{ctx.author.name}** attempts to assassinate **{target.name}** for 3 coins!",
                    discord.Color.purple())
    
    fields = [{"name": "Actions Available", 
               "value": "React with ❓ to **challenge** this Assassin claim\nReact with 🚫 to **block** as Contessa\n\nYou have 5 seconds to react."}]
    action_message = await send_embed(ctx, "⚔️ Assassination in Progress",
                                     f"**{ctx.author.name}** paid 3 coins to attempt assassination.",
                                     discord.Color.orange(), fields)

    emoji, reactor = await wait_for_reaction(ctx, action_message, ["❓", "🚫"], action_initiator=ctx.author)

    if emoji == "❓":
        log_game_action("assassinate_attempt", ctx.guild.id, ctx.author, target, "Assassination initiated")
        # Handle challenge to assassination claim
        claim_legitimate, game_ended, eliminated_player, next_player_result = await handle_challenge(ctx, ctx.author, reactor, "Assassin")
        if game_ended:
            return
        
        if claim_legitimate:
            # Challenge failed, assassination proceeds
            await send_success(ctx, "🗡️ Assassination Proceeds", "Challenge failed. The assassination proceeds.")
            await asyncio.sleep(1)
            
            # Check if target is still alive before proceeding
            if target in game_state['players'] and len(game_state['players'][target]["cards"]) > 0:
                log_game_action("assassinate_success", ctx.guild.id, ctx.author, target, "Assassination succeeded")
                card_lost = await lose_influence_with_reveal(ctx, target, "is assassinated and must discard")
                await asyncio.sleep(1)
                
                eliminated, game_ended, _ = await handle_player_elimination(ctx, target)
                if game_ended:
                    return
        else:
            log_game_action("assassinate_failed", ctx.guild.id, ctx.author, target, "Challenge succeeded - no Assassin card")
            log_game_action("false_assassin_exposed", ctx.guild.id, ctx.author, reactor, "Caught bluffing Assassin claim")
            # Challenge succeeded, assassination failed
            if eliminated_player == ctx.author:
                # Current player was eliminated, advance to the calculated next player
                game_state['current_player'] = next_player_result
                await send_embed(ctx, "⏭️ Turn Order",
                               f"It's now **{game_state['current_player'].mention}**'s turn.",
                               discord.Color.blue())
            else:
                await advance_turn(ctx, ctx.author)
            return

    elif emoji == "🚫":
        log_game_action("assassinate_blocked", ctx.guild.id, reactor, target, "Attempted block as Contessa")
        # Handle block attempt
        fields = [{"name": "Challenge", "value": "React with ❓ to challenge this claim within 5 seconds."}]
        challenge_message = await send_embed(ctx, "🛑 Block Attempt",
                                           f"**{reactor.name}** is blocking the assassination as **Contessa**.",
                                           discord.Color.orange(), fields)

        challenge_emoji, challenger = await wait_for_reaction(ctx, challenge_message, ["❓"], action_initiator=reactor)

        if challenge_emoji == "❓":
            claim_legitimate, game_ended, eliminated_player, next_player_result = await handle_challenge(ctx, reactor, challenger, "Contessa")
            if game_ended:
                return
            
            if claim_legitimate:
                # Block succeeded
                log_game_action("assassinate_failed", ctx.guild.id, ctx.author, target, "Blocked by Contessa")
                await send_info(ctx, "🛑 Assassination Blocked",
                               f"The assassination is successfully blocked by **{reactor.name}**!")
            else:
                # Block failed, assassination proceeds
                await send_embed(ctx, "🗡️ Assassination Succeeds",
                               "The failed block allows the assassination to proceed!",
                               discord.Color.red())
                
                # Check if target is still alive and in the game before proceeding
                if target in game_state['players'] and len(game_state['players'][target]["cards"]) > 0:
                    log_game_action("assassinate_success", ctx.guild.id, ctx.author, target, "Assassination succeeded")
                    card_lost = await lose_influence_with_reveal(ctx, target, "is assassinated and must discard")
                    await asyncio.sleep(1)
                    
                    eliminated, game_ended, _ = await handle_player_elimination(ctx, target)
                    if game_ended:
                        return
                else:
                    # Target was already eliminated (likely from the failed block challenge)
                    await send_embed(ctx, "💀 Target Already Eliminated",
                                   f"**{target.name}** was already eliminated and cannot be assassinated further.",
                                   discord.Color.red())
        else:
            # No challenge to block, block succeeds
            await send_info(ctx, "🛑 Block Succeeds",
                           f"No one challenged the block. **{reactor.name}**'s block succeeds, and the assassination is canceled.")
    else:
        # No block or challenge, assassination proceeds
        await send_success(ctx, "🗡️ Assassination Proceeds", "No one blocked the assassination. Action proceeds.")
        await asyncio.sleep(1)

        # Check if target is still alive before proceeding
        if target in game_state['players'] and len(game_state['players'][target]["cards"]) > 0:
            log_game_action("assassinate_success", ctx.guild.id, ctx.author, target, "Assassination succeeded")
            card_lost = await lose_influence_with_reveal(ctx, target, "is assassinated and must discard")
            await asyncio.sleep(1)
            
            eliminated, game_ended, _ = await handle_player_elimination(ctx, target)
            if game_ended:
                return

    # Always advance turn at the end, regardless of what happened
    await advance_turn(ctx, ctx.author)

@bot.command(name="tax")
async def tax(ctx):
    if not await validate_turn(ctx, ctx.author):
        return
    if not await validate_action_allowed(ctx, ctx.author, "tax"):
        return

    log_game_action("tax_attempt", ctx.guild.id, ctx.author, details="Claimed Duke for 3 coins")
    game_state = get_game_state(ctx.guild.id)
    fields = [{"name": "Challenge", "value": "React with ❓ to challenge this claim within 5 seconds."}]
    action_message = await send_embed(ctx, "💰 Tax Claim",
                                     f"{ctx.author.name} is claiming the **Duke** to take 3 coins.",
                                     discord.Color.blue(), fields)

    emoji, challenger = await wait_for_reaction(ctx, action_message, ["❓"], action_initiator=ctx.author)

    if emoji == "❓":
        claim_legitimate, game_ended, eliminated_player, next_player_result = await handle_challenge(ctx, ctx.author, challenger, "Duke")
        if game_ended:
            return
        
        if claim_legitimate:
            # Challenge failed, tax proceeds
            game_state['players'][ctx.author]["coins"] += 3
            log_game_action("tax_success", ctx.guild.id, ctx.author, details=f"Gained 3 coins, now has {game_state['players'][ctx.author]['coins']}")
            await send_embed(ctx, "💰 Coins Updated",
                           f"{ctx.author.name} now has **{game_state['players'][ctx.author]['coins']}** coins.",
                           discord.Color.green())
            await asyncio.sleep(1)
        else:
            # Challenge succeeded, turn advances
            log_game_action("tax_failed", ctx.guild.id, ctx.author, challenger, "Challenge succeeded - no Duke card")
            log_game_action("false_duke_exposed", ctx.guild.id, ctx.author, challenger, "Caught bluffing Duke claim")
            if eliminated_player == ctx.author:
                # Current player was eliminated, advance to the calculated next player
                game_state['current_player'] = next_player_result
                await send_embed(ctx, "⏭️ Turn Order",
                               f"It's now **{game_state['current_player'].mention}**'s turn.",
                               discord.Color.blue())
            else:
                await advance_turn(ctx, ctx.author)
            return
    else:
        # No challenge, tax proceeds
        log_game_action("tax_success", ctx.guild.id, ctx.author, details=f"Gained 3 coins unchallenged, now has {game_state['players'][ctx.author]['coins']}")
        await send_success(ctx, "💰 Tax Proceeds", "No one challenged the claim. Action proceeds.")
        await asyncio.sleep(1)
        game_state['players'][ctx.author]["coins"] += 3
        await send_embed(ctx, "💰 Coins Updated",
                        f"{ctx.author.name} now has **{game_state['players'][ctx.author]['coins']}** coins.",
                        discord.Color.green())
        await asyncio.sleep(1)

    await advance_turn(ctx, ctx.author)

@bot.command(name="steal")
async def steal(ctx, target: discord.Member):
    if not await validate_turn(ctx, ctx.author):
        return
    if not await validate_target(ctx, target):
        return
    if not await validate_self_target(ctx, ctx.author, target, "steal"):
        return
    if not await validate_action_allowed(ctx, ctx.author, "steal"):
        return

    # Extra safety check
    game_state = get_game_state(ctx.guild.id)
    if target not in game_state['players']:
        await send_error(ctx, "🚫 Invalid Target", f"{target.name} is not in this game!")
        return

    game_state = get_game_state(ctx.guild.id)
    
    if game_state['players'][target]["coins"] < 1:
        await send_error(ctx, "💸 No Coins to Steal", f"{target.name} has no coins to steal!")
        return

    log_game_action("steal_attempt", ctx.guild.id, ctx.author, target, f"Claimed Captain to steal from {target.name}")
    fields = [{"name": "Your Options", 
               "value": "🚫 - Block as **Captain** or **Ambassador**\n❓ - Challenge **Captain** claim\n\nReact within 5 seconds to take action."}]
    action_message = await send_embed(ctx, "💰 Steal Attempt",
                                     f"{ctx.author.name} is attempting to steal from {target.name} as **Captain**.",
                                     discord.Color.blue(), fields)

    emoji, reactor = await wait_for_reaction(ctx, action_message, ["🚫", "❓"], action_initiator=ctx.author)

    if emoji == "🚫":
        # Handle block attempt
        log_game_action("steal_blocked", ctx.guild.id, reactor, ctx.author, "Attempted block as Captain/Ambassador")
        fields = [{"name": "Challenge", "value": "React with ❓ to challenge this block within 5 seconds."}]
        challenge_message = await send_embed(ctx, "🔒 Block Attempt",
                                           f"{reactor.name} is attempting to block the steal as **Captain** or **Ambassador**!",
                                           discord.Color.orange(), fields)
        challenge_emoji, challenger = await wait_for_reaction(ctx, challenge_message, ["❓"], action_initiator=reactor)

        if challenge_emoji == "❓":
            log_game_action("block_challenged", ctx.guild.id, challenger, reactor, "Challenged Captain/Ambassador block claim")
            # Use dramatic block challenge
            claim_legitimate, game_ended, eliminated_player, next_player_result = await handle_block_challenge(ctx, reactor, challenger, ["Captain", "Ambassador"])
            if game_ended:
                return
            
            if claim_legitimate:
                # Block succeeded
                embed = await create_embed(
                    "🛡️ BLOCK SUCCESSFUL! 🛡️",
                    f"**{reactor.name}** successfully blocks the steal!",
                    COLORS['success']
                )
                await ctx.send(embed=embed)
            else:
                # Block failed, steal proceeds
                # Check if target is still in the game before proceeding with steal
                if target in game_state['players'] and len(game_state['players'][target]["cards"]) > 0:
                    # Steal proceeds after failed block
                    old_coins_stealer = game_state['players'][ctx.author]["coins"]
                    old_coins_target = game_state['players'][target]["coins"]
                    
                    stolen_coins = min(2, game_state['players'][target]["coins"])
                    game_state['players'][ctx.author]["coins"] += stolen_coins
                    game_state['players'][target]["coins"] -= stolen_coins
                    log_game_action("steal_success", ctx.guild.id, ctx.author, target, f"Block failed, stealing {stolen_coins} coins")
                    log_game_action("false_block_exposed", ctx.guild.id, reactor, challenger, "Caught bluffing Captain/Ambassador block")

                    embed = await create_embed(
                        "💸 STEAL SUCCESSFUL! 💸",
                        create_action_result("STEAL", ctx.author, target, f"{stolen_coins} coins stolen"),
                        COLORS['gain'],
                        [{"name": f"👤 {ctx.author.name}", "value": f"{old_coins_stealer} → **{game_state['players'][ctx.author]['coins']}** (+{stolen_coins})", "inline": True},
                         {"name": f"👤 {target.name}", "value": f"{old_coins_target} → **{game_state['players'][target]['coins']}** (-{stolen_coins})", "inline": True}]
                    )
                    await ctx.send(embed=embed)
                else:
                    # Target was already eliminated
                    await send_embed(ctx, "💀 Target Already Eliminated",
                                   f"**{target.name}** was already eliminated and cannot be stolen from.",
                                   discord.Color.red())
        else:
            # Block not challenged - it succeeds
            log_game_action("steal_failed", ctx.guild.id, ctx.author, target, "Blocked by Captain/Ambassador")
            embed = await create_embed(
                "🛡️ BLOCK SUCCESSFUL! 🛡️",
                f"**{reactor.name}** successfully blocks the steal!",
                COLORS['success']
            )
            await ctx.send(embed=embed)

    elif emoji == "❓":
        log_game_action("steal_challenged", ctx.guild.id, reactor, ctx.author, "Challenged Captain claim")
        # Handle direct challenge to steal action
        claim_legitimate, game_ended, eliminated_player, next_player_result = await handle_challenge(ctx, ctx.author, reactor, "Captain")
        if game_ended:
            return
        
        if claim_legitimate:
            # Check if target is still in the game before proceeding
            if target in game_state['players'] and len(game_state['players'][target]["cards"]) > 0:
                # Steal proceeds after successful challenge defense
                old_coins_stealer = game_state['players'][ctx.author]["coins"]
                old_coins_target = game_state['players'][target]["coins"]
                
                stolen_coins = min(2, game_state['players'][target]["coins"])
                game_state['players'][ctx.author]["coins"] += stolen_coins
                game_state['players'][target]["coins"] -= stolen_coins
                log_game_action("steal_success", ctx.guild.id, ctx.author, target, f"Challenge failed, stealing {stolen_coins} coins")

                embed = await create_embed(
                    "💸 STEAL SUCCESSFUL! 💸",
                    create_action_result("STEAL", ctx.author, target, f"{stolen_coins} coins stolen"),
                    COLORS['gain'],
                    [{"name": f"👤 {ctx.author.name}", "value": f"{old_coins_stealer} → **{game_state['players'][ctx.author]['coins']}** (+{stolen_coins})", "inline": True},
                     {"name": f"👤 {target.name}", "value": f"{old_coins_target} → **{game_state['players'][target]['coins']}** (-{stolen_coins})", "inline": True}]
                )
                await ctx.send(embed=embed)
            else:
                # Target no longer exists
                await send_embed(ctx, "💀 Target No Longer Available",
                               f"**{target.name}** is no longer in the game.",
                               discord.Color.red())
        else:
            # Challenge succeeded, steal failed
            log_game_action("steal_failed", ctx.guild.id, ctx.author, reactor, "Challenge succeeded - no Captain card")
            log_game_action("false_captain_exposed", ctx.guild.id, ctx.author, reactor, "Caught bluffing Captain claim")
            if eliminated_player == ctx.author:
                # Current player was eliminated, advance to the calculated next player
                game_state['current_player'] = next_player_result
                await enhanced_turn_announcement(ctx, game_state['current_player'])
                return
            # If current player wasn't eliminated, continue to end and advance normally
    else:
        # No reaction - steal proceeds unchallenged
        # Check if target is still in the game before proceeding
        if target in game_state['players'] and len(game_state['players'][target]["cards"]) > 0:
            old_coins_stealer = game_state['players'][ctx.author]["coins"]
            old_coins_target = game_state['players'][target]["coins"]
            
            stolen_coins = min(2, game_state['players'][target]["coins"])
            game_state['players'][ctx.author]["coins"] += stolen_coins
            game_state['players'][target]["coins"] -= stolen_coins

            log_game_action("steal_success", ctx.guild.id, ctx.author, target, f"Unchallenged, stealing {stolen_coins} coins")

            embed = await create_embed(
                "💸 STEAL SUCCESSFUL! 💸",
                create_action_result("STEAL", ctx.author, target, f"{stolen_coins} coins stolen"),
                COLORS['gain'],
                [{"name": f"👤 {ctx.author.name}", "value": f"{old_coins_stealer} → **{game_state['players'][ctx.author]['coins']}** (+{stolen_coins})", "inline": True},
                 {"name": f"👤 {target.name}", "value": f"{old_coins_target} → **{game_state['players'][target]['coins']}** (-{stolen_coins})", "inline": True}]
            )
            await ctx.send(embed=embed)
        else:
            # Target no longer exists
            await send_embed(ctx, "💀 Target No Longer Available",
                           f"**{target.name}** is no longer in the game.",
                           discord.Color.red())

    # Always advance turn at the end (unless current player was eliminated above)
    await advance_turn(ctx, ctx.author)

@bot.command(name="exchange")
async def exchange(ctx):
    if not await validate_turn(ctx, ctx.author):
        return
    if not await validate_action_allowed(ctx, ctx.author, "exchange"):
        return

    game_state = get_game_state(ctx.guild.id)
    
    # Get initial card count at the beginning
    initial_card_count = len(game_state['players'][ctx.author]["cards"])
    log_game_action("exchange_attempt", ctx.guild.id, ctx.author, details="Claimed Ambassador to exchange cards")

    fields = [{"name": "Challenge", "value": "React with ❓ to challenge this claim within 5 seconds."}]
    action_message = await send_embed(ctx, "🔄 Exchange Action",
                                     f"{ctx.author.name} is attempting to exchange cards as the **Ambassador**.",
                                     discord.Color.blue(), fields)

    emoji, challenger = await wait_for_reaction(ctx, action_message, ["❓"], action_initiator=ctx.author)

    if emoji == "❓":
        log_game_action("exchange_challenged", ctx.guild.id, challenger, ctx.author, "Challenged Ambassador claim")
        claim_legitimate, game_ended, eliminated_player, next_player_result = await handle_challenge(ctx, ctx.author, challenger, "Ambassador")
        if game_ended:
            return
        
        if not claim_legitimate:
            log_game_action("exchange_failed", ctx.guild.id, ctx.author, challenger, "Challenge succeeded - no Ambassador card")
            log_game_action("false_ambassador_exposed", ctx.guild.id, ctx.author, challenger, "Caught bluffing Ambassador claim")
            # Challenge succeeded, exchange failed
            if eliminated_player == ctx.author:
                # Current player was eliminated, advance to the calculated next player
                game_state['current_player'] = next_player_result
                await send_embed(ctx, "⏭️ Turn Order",
                               f"It's now **{game_state['current_player'].mention}**'s turn.",
                               discord.Color.blue())
            else:
                # Current player wasn't eliminated, advance normally
                await advance_turn(ctx, ctx.author)
            return

    log_game_action("exchange_proceeding", ctx.guild.id, ctx.author, details="Exchange proceeding after challenge phase")
    # Exchange proceeds
    await send_info(ctx, "🔄 Exchange Proceeds", 
                   "No one challenged the claim or the challenge failed. Action proceeds.")
    await asyncio.sleep(1)

    # Draw 2 cards from the Court Deck
    if len(game_state['court_deck']) < 2:
        logger.error(f"DECK_ERROR: Not enough cards for exchange! Deck has {len(game_state['court_deck'])} cards")
        await send_error(ctx, "🚫 Deck Error", 
                        "Not enough cards remaining in the deck for exchange! This shouldn't happen - please contact an admin.")
        await advance_turn(ctx, ctx.author)
        return

    new_cards = [game_state['court_deck'].pop(), game_state['court_deck'].pop()]
    all_cards = game_state['players'][ctx.author]["cards"] + new_cards
    log_game_action("exchange_cards_drawn", ctx.guild.id, ctx.author, details=f"Drew {len(new_cards)} cards from deck")
    
    # Randomize the order so players can't tell which are their old cards
    random.shuffle(all_cards)
    all_cards_with_ids = [(card, f"{card} ({i+1})") for i, card in enumerate(all_cards)]

    # Send the drawn cards to the player via DM
    dm_success = True
    try:
        # Send header for exchange
        header_embed = await create_embed(
            "🔄 Exchange Cards Available", 
            f"You drew 2 cards from the deck. Choose **{initial_card_count}** cards to keep:",
            discord.Color.purple()
        )
        success, error_reason = await safe_send_dm(ctx.author, header_embed)
        if not success:
            dm_success = False
        
        # Send each card option with image
        if dm_success:
            for i, (card, card_id) in enumerate(all_cards_with_ids, 1):
                card_embed = await create_embed(
                    f"Option {i}: {card}",
                    f"**{card}** - React with {i}️⃣ to select this card",
                    discord.Color.blue(),
                    image_url=card_images[card]
                )
                success, error_reason = await safe_send_dm(ctx.author, card_embed)
                if not success:
                    dm_success = False
                    break
        
        # Send footer with instructions
        if dm_success:
            footer_embed = await create_embed(
                "📋 Instructions",
                f"React to the message in the channel with the numbers for the cards you want to keep.\n\nYou need to select **{initial_card_count}** cards total.",
                discord.Color.gold()
            )
            footer_embed.set_footer(text="Choose wisely! This could change your strategy! 🎯")
            success, error_reason = await safe_send_dm(ctx.author, footer_embed)
            if not success:
                dm_success = False
        
        if not dm_success:
            log_game_action("exchange_dm_failed", ctx.guild.id, ctx.author, details="Could not send exchange cards via DM")
            await handle_dm_failure(ctx, ctx.author, error_reason, "receive exchange card options")
            # Return cards to deck and advance turn
            game_state['court_deck'].extend(new_cards)
            await advance_turn(ctx, ctx.author)
            return
            
    except Exception as e:
        log_game_action("exchange_error", ctx.guild.id, ctx.author, details=f"Exchange failed due to error: {str(e)}")
        await send_error(ctx, "⚠️ Exchange Error", 
                        f"An error occurred during the exchange: {str(e)}")
        # Return cards to deck and advance turn
        game_state['court_deck'].extend(new_cards)
        await advance_turn(ctx, ctx.author)
        return

    # Display the cards in the server and ask for reactions
    embed = await create_embed("🃏 Choose Your Cards",
                              "Check your DMs for the cards drawn and react with the corresponding emoji to choose your cards.",
                              discord.Color.purple())
    card_message = await ctx.send(embed=embed)

    # Add reactions for each card
    valid_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    for i in range(len(all_cards_with_ids)):
        await card_message.add_reaction(valid_emojis[i])

    # Wait for the player to react
    chosen_cards = []
    selected_indices = []

    while len(chosen_cards) < initial_card_count:
        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in valid_emojis and reaction.message.id == card_message.id

        reaction, user = await bot.wait_for("reaction_add", check=check)
        card_index = valid_emojis.index(str(reaction.emoji))

        if card_index not in selected_indices:
            selected_indices.append(card_index)
            chosen_card = all_cards_with_ids[card_index][0]
            chosen_cards.append(chosen_card)

    # Update the player's cards
    game_state['players'][ctx.author]["cards"] = chosen_cards

    # Return the unchosen cards to the Court Deck - FIXED VERSION
    # Use indices to properly track which specific cards were chosen vs unchosen
    unchosen_cards = []
    for i, (card, card_id) in enumerate(all_cards_with_ids):
        if i not in selected_indices:
            unchosen_cards.append(card)
    
    game_state['court_deck'].extend(unchosen_cards)
    log_game_action("exchange_cards_returned", ctx.guild.id, ctx.author, details=f"Returned {len(unchosen_cards)} cards to deck")

    await send_success(ctx, "🔄 Exchange Complete", "The exchange is now complete.")
    await asyncio.sleep(1)
    await advance_turn(ctx, ctx.author)

@bot.command(name="actions")
async def actions(ctx):
    """Display all available actions with enhanced styling."""
    # Create main embed with general title
    title = "📋 ═══ COUP ACTIONS GUIDE ═══ 📋"
    color = discord.Color.blue()

    embed = discord.Embed(title=title, color=color)
    
    # General Actions - Always Available
    general_actions = ""
    
    # Income
    general_actions += "💰 `!income` - **Take 1 coin**\n"
    general_actions += "└ Safe action, cannot be blocked or challenged\n"
    general_actions += "└ Use when you want to play it safe\n\n"
    
    # Foreign Aid
    general_actions += "💸 `!foreign_aid` - **Take 2 coins**\n"
    general_actions += "└ Can be blocked by players claiming 👑 **Duke**\n"
    general_actions += "└ Good for building wealth quickly\n\n"
    
    # Coup
    general_actions += "💥 `!coup <target>` - **Launch a coup** (7 coins)\n"
    general_actions += "└ Eliminates one of target's cards\n"
    general_actions += "└ Cannot be blocked or challenged\n"
    general_actions += "└ Required when you have 10+ coins!"

    embed.add_field(
        name="🎯 __General Actions__",
        value=general_actions,
        inline=False
    )

    # Character-Specific Actions
    character_actions = ""
    
    # Duke - Tax
    character_actions += "👑 `!tax` - **Take 3 coins as Duke**\n"
    character_actions += "└ Can be challenged if you don't have Duke\n"
    character_actions += "└ Great for building wealth fast\n\n"
    
    # Assassin - Assassinate
    character_actions += "🗡️ `!assassinate <target>` - **Eliminate for 3 coins as Assassin**\n"
    character_actions += "└ Can be challenged if you don't have Assassin\n"
    character_actions += "└ Can be blocked by players claiming Contessa\n"
    character_actions += "└ Cheaper than coup!\n\n"
    
    # Captain - Steal
    character_actions += "⚓ `!steal <target>` - **Steal 2 coins as Captain**\n"
    character_actions += "└ Can be challenged if you don't have Captain\n"
    character_actions += "└ Can be blocked by Captain or Ambassador\n"
    character_actions += "└ Gain coins while weakening opponents\n\n"
    
    # Ambassador - Exchange
    character_actions += "🤝 `!exchange` - **Exchange cards as Ambassador**\n"
    character_actions += "└ Can be challenged if you don't have Ambassador\n"
    character_actions += "└ Draw 2 cards, keep the same amount you had\n"
    character_actions += "└ Get better cards for your strategy"

    embed.add_field(
        name="🃏 __Character Actions__",
        value=character_actions,
        inline=False
    )

    # Defensive Actions
    defensive_actions = "└ **Block Foreign Aid** - Claim 👑 Duke to stop others' foreign aid\n"
    defensive_actions += "└ **Block Assassination** - Claim 🛡️ Contessa to stop assassinations\n"
    defensive_actions += "└ **Block Stealing** - Claim ⚓ Captain or 🤝 Ambassador to stop theft\n"
    defensive_actions += "└ **Challenge Claims** - Challenge others' character claims\n\n"

    embed.add_field(
        name="🛡️ __Defensive Actions__",
        value=defensive_actions,
        inline=False
    )

    # Add the Coup actions reference image
    embed.set_image(url="https://static.wikia.nocookie.net/board-games-galore/images/2/2d/Coup_actions.jpg/revision/latest?cb=20160713201921")

    # Generic footer
    embed.set_footer(
        text="🎭 Master the art of deception • Bluff, challenge, and dominate!",
        icon_url="https://cdn.discordapp.com/emojis/755774680816632987.png"
    )
    
    # Add timestamp
    import datetime
    embed.timestamp = datetime.datetime.now()
    
    await ctx.send(embed=embed)

@bot.command(name="cards")
async def cards(ctx):
    """Display the cards in your hand via a direct message."""
    game_state = get_game_state(ctx.guild.id)
    
    if ctx.author not in game_state['players']:
        await send_error(ctx, "🚫 Not in Game", "You are not part of the current game.")
        return

    if not is_player_alive(ctx.guild.id, ctx.author):
        await send_error(ctx, "💀 Out of the Game", "You are out of the game and have no cards.")
        return

    try:
        cards_message = f"Your cards are: {', '.join(game_state['players'][ctx.author]['cards'])}."
        await ctx.author.send(cards_message)
        await send_success(ctx, "📬 Cards Sent", f"{ctx.author.name}, I've sent you a DM with your cards!")
    except discord.Forbidden:
        await send_error(ctx, "🚫 DM Disabled", 
                        f"{ctx.author.name}, I couldn't send you a DM. Please enable DMs to view your cards.")

@bot.command(name="table")
async def table(ctx):
    """Displays a beautiful, detailed table showing game state."""
    game_state = get_game_state(ctx.guild.id)
    
    if not game_state['players']:
        await send_error(ctx, "🚫 No Players", "No players are currently in the game.")
        return

    # Create turn-ordered player list starting with current player
    if game_state['current_player']:
        player_list = list(game_state['players'].keys())
        current_index = player_list.index(game_state['current_player'])
        ordered_players = player_list[current_index:] + player_list[:current_index]
    else:
        ordered_players = list(game_state['players'].keys())

    # Enhanced player display with better formatting
    player_info = ""
    alive_count = 0
    
    for i, player in enumerate(ordered_players, 1):
        data = game_state['players'][player]
        
        # Enhanced status indicators with descriptions
        if player == game_state['current_player']:
            status = "👑 **CURRENT TURN**"
            status_color = "🟡"
        elif not is_player_alive(ctx.guild.id, player):
            status = "💀 *Eliminated*"
            status_color = "🔴"
        else:
            status = ""  # No status for waiting players
            status_color = "🟢"
        
        if is_player_alive(ctx.guild.id, player):
            alive_count += 1
        
        # Beautiful player entry with visual hierarchy
        player_info += f"**{i}.** {status_color} **{player.name}**\n"
        if status:  # Only show status line if there's a status
            player_info += f"     └ {status}\n"
        player_info += f"     └ 💰 **{data['coins']}** coins │ 🃏 **{len(data['cards'])}** cards\n\n"

    # Create the main embed with enhanced styling
    embed = discord.Embed(
        title="🎮 ═══════ COUP GAME TABLE ═══════ 🎮",
        description="**Current Battle Status**",
        color=COLORS['info']
    )
    
    # Game status header
    game_status = f"🔥 **{alive_count}** players remaining\n"
    if game_state['current_player']:
        game_status += f"🎯 **{game_state['current_player'].name}**'s turn"
        if game_state['players'][game_state['current_player']]['coins'] >= 10:
            game_status += " *(MUST COUP!)*"
    
    embed.add_field(
        name="📊 Game Status",
        value=game_status,
        inline=False
    )
    
    # Player information with beautiful formatting
    embed.add_field(
        name="👥 Players (Turn Order)",
        value=player_info.strip(),
        inline=False
    )
    
    # Enhanced discard pile with visual formatting
    if game_state['discarded_cards']:
        card_counts = {}
        for card in game_state['discarded_cards']:
            card_counts[card] = card_counts.get(card, 0) + 1
        
        # Create visual card display with emojis
        card_emojis = {
            "Duke": "👑",
            "Assassin": "🗡️", 
            "Captain": "⚓",
            "Ambassador": "🤝",
            "Contessa": "🛡️"
        }
        
        discard_lines = []
        for card, count in sorted(card_counts.items()):
            emoji = card_emojis.get(card, "🃏")
            discard_lines.append(f"{emoji} **{card}** ×{count}")
        
        discard_text = "\n".join(discard_lines)
        embed.add_field(
            name="🗑️ Discarded Cards", 
            value=discard_text, 
            inline=True
        )
    else:
        embed.add_field(
            name="🗑️ Discarded Cards", 
            value="*None yet*", 
            inline=True
        )

    # Enhanced deck information
    deck_info = f"📚 **{len(game_state['court_deck'])}** cards"
    if len(game_state['court_deck']) <= 5:
        deck_info += "\n⚠️ *Deck running low!*"
    
    embed.add_field(
        name="📦 Court Deck", 
        value=deck_info, 
        inline=True
    )
    
    # Add action hints for current player
    if game_state['current_player'] and is_player_alive(ctx.guild.id, game_state['current_player']):
        if game_state['players'][game_state['current_player']]['coins'] >= 10:
            action_hint = "🚨 **Must use `!coup <target>`**"
        elif game_state['players'][game_state['current_player']]['coins'] >= 7:
            action_hint = "💥 Can coup with `!coup <target>`"
        elif game_state['players'][game_state['current_player']]['coins'] >= 3:
            action_hint = "🗡️ Can assassinate with `!assassinate <target>`"
        else:
            action_hint = "💡 Use `!actions` for available moves"
        
        embed.add_field(
            name="🎯 Current Player Actions",
            value=action_hint,
            inline=False
        )
    
    # Beautiful footer with turn information
    if len(ordered_players) > 1:
        next_player_index = 1 if len(ordered_players) > 1 else 0
        next_player = ordered_players[next_player_index] if len(ordered_players) > 1 else ordered_players[0]
        if is_player_alive(ctx.guild.id, next_player):
            embed.set_footer(
                text=f"⏭️ Next turn: {next_player.name} • Use !actions to see available moves",
                icon_url="https://cdn.discordapp.com/emojis/755774680816632987.png"
            )
    
    # Add timestamp
    import datetime
    embed.timestamp = datetime.datetime.now()
    
    await ctx.send(embed=embed)



@bot.command(name="coins")
async def coins(ctx):
    """Displays the number of coins the invoking player currently has."""
    game_state = get_game_state(ctx.guild.id)
    
    if ctx.author not in game_state['players']:
        await send_error(ctx, "🚫 Not in Game", "You are not currently in the game.")
        return

    num_coins = game_state['players'][ctx.author]["coins"]
    await send_embed(ctx, "💰 Your Coins",
                    f"{ctx.author.name}, you currently have **{num_coins}** coins.",
                    discord.Color.green())

# @bot.command(name="stats")
# async def stats(ctx, target=None):
#     print(f"DEBUG: Stats command called with target='{target}'")  # ADD THIS
#     """Display server or player statistics."""
#     game_state = get_game_state(ctx.guild.id)
#     stats = game_state['stats']
    
#     if target is None:
#         # Show server stats
#         total_games = stats['games_started']
#         completed_games = stats['games_completed']
#         abandoned_games = stats['games_abandoned']
#         total_players = len(stats['total_participants'])
        
#         completion_rate = (completed_games / total_games * 100) if total_games > 0 else 0
        
#         embed = discord.Embed(
#             title=f"📊 Server Statistics - {ctx.guild.name}",
#             color=discord.Color.blue()
#         )
        
#         embed.add_field(
#             name="🎮 Game Statistics",
#             value=f"**Total Games:** {total_games}\n"
#                   f"**Completed:** {completed_games}\n"
#                   f"**Abandoned:** {abandoned_games}\n"
#                   f"**Completion Rate:** {completion_rate:.1f}%",
#             inline=True
#         )
        
#         # Top 3 players by wins
#         if stats['player_wins']:
#             sorted_winners = sorted(stats['player_wins'].items(), key=lambda x: x[1], reverse=True)[:3]
#             top_players = []
#             for player_id, wins in sorted_winners:
#                 if player := bot.get_user(player_id):
#                     games_played = stats['player_games'].get(player_id, 0)
#                     win_rate = (wins / games_played * 100) if games_played > 0 else 0
#                     top_players.append(f"**{player.name}:** {wins} wins ({win_rate:.1f}%)")
            
#             if top_players:
#                 embed.add_field(
#                     name="🏆 Top Players",
#                     value="\n".join(top_players),
#                     inline=False
#                 )
        
#         await ctx.send(embed=embed)
#         log_game_action("stats_viewed", ctx.guild.id, ctx.author, details="Viewed server stats")
    
#     else:
#         # Show individual player stats - improved player finding
#         target_player = None
        
#         try:
            
#             # Try to convert target to a Member or User
#             if target.startswith('<@') and target.endswith('>'):
#                 # Handle mentions
#                 user_id_str = target[2:-1]  # Remove <@ and >
#                 if user_id_str.startswith('!'):
#                     user_id_str = user_id_str[1:]  # Remove ! if present
#                 user_id = int(user_id_str)
                
#                 # Try guild member first, then user
#                 target_player = ctx.guild.get_member(user_id) or bot.get_user(user_id)
#             else:
#                 for member in ctx.guild.members:
#                     if (member.name.lower() == target.lower() or 
#                         member.display_name.lower() == target.lower() or
#                         member.name.lower().startswith(target.lower()) or
#                         member.display_name.lower().startswith(target.lower())):
#                         target_player = member
#                         break
#         except (ValueError, AttributeError):
#             target_player = None
        
#         if not target_player:
#             await send_error(ctx, "👤 Player Not Found", 
#                             f"Could not find player '{target}' in this server.\n\n"
#                             "**Try:**\n"
#                             "• `!stats @username` (mention them)\n"
#                             "• `!stats username` (exact name)\n"
#                             "• Make sure they're in this server")
#             return
        
#         player_id = target_player.id
        
#         # Get player's stats
#         games_played = stats['player_games'].get(player_id, 0)
#         games_won = stats['player_wins'].get(player_id, 0)
        
#         if games_played == 0:
#             await send_info(ctx, f"👤 {target_player.name}'s Stats", 
#                         f"**{target_player.name}** hasn't played any games yet in this server.")
#             return
        
#         # Calculate stats
#         win_rate = (games_won / games_played * 100) if games_played > 0 else 0
#         games_lost = games_played - games_won
        
#         # Calculate rank among all players
#         all_players_by_wins = sorted(stats['player_wins'].items(), key=lambda x: x[1], reverse=True)
#         player_rank = None
#         for i, (pid, wins) in enumerate(all_players_by_wins, 1):
#             if pid == player_id:
#                 player_rank = i
#                 break
        
#         if player_rank is None:
#             player_rank = len(stats['player_wins']) + 1
        
#         # Calculate rank among all players by win rate (for players with 2+ games)
#         experienced_players = [(pid, stats['player_wins'].get(pid, 0) / stats['player_games'].get(pid, 1) * 100) 
#                             for pid in stats['player_games'] if stats['player_games'][pid] >= 2]
#         experienced_players.sort(key=lambda x: x[1], reverse=True)
        
#         win_rate_rank = None
#         if games_played >= 2:
#             for i, (pid, wr) in enumerate(experienced_players, 1):
#                 if pid == player_id:
#                     win_rate_rank = i
#                     break
        
#         # Create player stats embed
#         embed = discord.Embed(
#             title=f"👤 {target_player.name}'s Statistics",
#             description=f"Performance in **{ctx.guild.name}**",
#             color=discord.Color.green() if win_rate >= 50 else discord.Color.orange()
#         )
        
#         # Set player avatar as thumbnail
#         if target_player.avatar:
#             embed.set_thumbnail(url=target_player.avatar.url)
        
#         # Basic stats
#         embed.add_field(
#             name="🎮 Game Record",
#             value=f"**Games Played:** {games_played}\n"
#                 f"**Games Won:** {games_won}\n"
#                 f"**Games Lost:** {games_lost}\n"
#                 f"**Win Rate:** {win_rate:.1f}%",
#             inline=True
#         )
        
#         # Rankings
#         rank_text = f"**Overall Rank:** #{player_rank} of {len(stats['total_participants'])}"
#         if win_rate_rank and games_played >= 2:
#             rank_text += f"\n**Win Rate Rank:** #{win_rate_rank} of {len(experienced_players)}"
#             rank_text += f"\n*(Players with 2+ games)*"
        
#         embed.add_field(
#             name="🏆 Rankings",
#             value=rank_text,
#             inline=True
#         )
        
#         embed.set_footer(text=f"Use !stats to see server statistics • Requested by {ctx.author.name}")
        
#         await ctx.send(embed=embed)
#         log_game_action("player_stats_viewed", ctx.guild.id, ctx.author, target_player, f"Viewed {target_player.name}'s stats")

# Error Handlers
@bot.event
async def on_command_error(ctx, error):
    """Handle command errors gracefully for all commands."""
    
    if isinstance(error, commands.MemberNotFound):
        # Handle when user provides invalid target - applies to assassinate, steal, coup
        command_emojis = {
            "assassinate": "🗡️",
            "steal": "💰", 
            "coup": "💥"
        }
        
        emoji = command_emojis.get(ctx.command.name, "🚫")
        action = ctx.command.name.title()
        
        await send_error(ctx, f"{emoji} {action} Target Not Found", 
                        f"I couldn't find a player named `{error.argument}` to {ctx.command.name}.\n\n"
                        "**How to target players:**\n"
                        f"• `!{ctx.command.name} @PlayerName` (mention them)\n"
                        f"• `!{ctx.command.name} PlayerName` (exact username)\n"
                        f"• `!{ctx.command.name} Display Name` (their server nickname)\n\n"
                        "**Make sure:**\n"
                        "• They're in this Discord server\n"
                        "• They're part of the current game\n"
                        "• You spelled their name correctly")
        return
    
    elif isinstance(error, commands.MissingRequiredArgument):
        # Handle when user forgets to provide arguments
        if ctx.command.name == 'assassinate':
            await send_error(ctx, "🗡️ Missing Assassination Target", 
                           "You need to specify who to assassinate!\n"
                           "**Usage:** `!assassinate @player` or `!assassinate PlayerName`\n"
                           "**Example:** `!assassinate @Alice`")
        elif ctx.command.name == 'steal':
            await send_error(ctx, "💰 Missing Steal Target", 
                           "You need to specify who to steal from!\n"
                           "**Usage:** `!steal @player` or `!steal PlayerName`\n"
                           "**Example:** `!steal @Bob`")
        elif ctx.command.name == 'coup':
            await send_error(ctx, "💥 Missing Coup Target", 
                           "You need to specify who to coup!\n"
                           "**Usage:** `!coup @player` or `!coup PlayerName`\n"
                           "**Example:** `!coup @Charlie`")
        else:
            await send_error(ctx, "🚫 Missing Arguments", 
                           f"The `!{ctx.command.name}` command requires additional arguments.\n"
                           "Use `!actions` to see all available commands.")
        return
    
    elif isinstance(error, commands.CommandNotFound):
        # Ignore unknown commands (don't spam chat with errors)
        return
    
    elif isinstance(error, commands.BadArgument):
        # Handle other argument conversion errors
        await send_error(ctx, "🚫 Invalid Argument", 
                        f"There was an issue with your command arguments.\n"
                        f"Use `!actions` for help with command usage.")
        return
    
    else:
        # For other unexpected errors, log to console for debugging
        print(f"Unexpected error in command '{ctx.command}': {type(error).__name__}: {error}")
        await send_error(ctx, "⚠️ Command Error", 
                        "Something unexpected went wrong with that command. Please try again.\n"
                        "If this keeps happening, contact an admin.")

# Debug command to check card counts
@bot.command(name="debug_cards")
async def debug_cards(ctx):
    """Debug command to check card distribution."""
    game_state = get_game_state(ctx.guild.id)
    
    if not game_state['game_started']:
        await send_error(ctx, "🚫 No Game", "No game in progress.")
        return
    
    total_player_cards = sum(len(game_state['players'][player]["cards"]) for player in game_state['players'])
    deck_cards = len(game_state['court_deck'])
    discarded_count = len(game_state['discarded_cards'])
    total_cards = total_player_cards + deck_cards + discarded_count
    
    debug_info = f"**Card Distribution:**\n"
    debug_info += f"👥 Players have: **{total_player_cards}** cards\n"
    debug_info += f"📚 Deck has: **{deck_cards}** cards\n"
    debug_info += f"🗑️ Discarded: **{discarded_count}** cards\n"
    debug_info += f"🎯 Total: **{total_cards}** cards (should be 15)\n\n"
    
    debug_info += f"**Player breakdown:**\n"
    for player in game_state['players']:
        debug_info += f"• {player.name}: {len(game_state['players'][player]['cards'])} cards\n"
    
    if game_state['discarded_cards']:
        debug_info += f"\n**Discarded cards:** {', '.join(game_state['discarded_cards'])}"
    
    await send_embed(ctx, "🔍 Card Count Debug", debug_info, discord.Color.orange())

# Load environment variables
load_dotenv()

# Get token from environment variable
TOKEN = os.getenv('BOT_TOKEN')

if not TOKEN:
    print("❌ ERROR: BOT_TOKEN not found in environment variables!")
    print("Make sure you have a .env file with BOT_TOKEN=your_token_here")
    exit(1)

print("✅ Bot token loaded successfully")
bot.run(TOKEN)