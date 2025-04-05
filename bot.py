import os
import asyncio
import logging
from datetime import datetime, timedelta
from telegram import Update, InputFile, User
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from pymongo import MongoClient
import random

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- MongoDB Setup ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://Ishuxd:ishusomuxd@ishuxd.78ljc.mongodb.net/?retryWrites=true&w=majority&appName=Ishuxd")
client = MongoClient(MONGO_URI)
db = client["tictactoe_db"]
stats_col = db["user_stats"]
history_col = db["game_history"]

# --- In-Memory Game Storage ---
games = {}
user_emojis = {}
active_users = {}

# --- Constants ---
DEFAULT_EMOJIS = ['❌', '⭕']
WELCOME_IMG_PATH = 'welcome.jpg'
TIMEOUT = 60

# --- Utility Functions ---
def format_board(board):
    return "\n".join([
        f"{board[0]} | {board[1]} | {board[2]}",
        f"{board[3]} | {board[4]} | {board[5]}",
        f"{board[6]} | {board[7]} | {board[8]}"
    ])

def check_winner(board):
    win_pos = [
        [0, 1, 2], [3, 4, 5], [6, 7, 8],
        [0, 3, 6], [1, 4, 7], [2, 5, 8],
        [0, 4, 8], [2, 4, 6]
    ]
    for a, b, c in win_pos:
        if board[a] == board[b] == board[c] and board[a] != ' ':
            return True
    return False

def is_draw(board):
    return ' ' not in board

async def timeout_check(chat_id, app):
    await asyncio.sleep(TIMEOUT)
    game = games.get(chat_id)
    if game and game['active']:
        if datetime.now() - game['last_move_time'] > timedelta(seconds=TIMEOUT):
            user = game['players'][game['turn']]
            await app.bot.send_message(
                chat_id,
                f"{user.mention_html()} took too long! Game ended.",
                parse_mode="HTML"
            )
            cleanup_game(chat_id)

def update_stats(user_id, result):
    stats_col.update_one(
        {'user_id': user_id},
        {'$inc': {result: 1}},
        upsert=True
    )

def get_stats(user_id):
    stats = stats_col.find_one({'user_id': user_id})
    return stats or {'win': 0, 'loss': 0, 'draw': 0}

def save_history(chat_id, record):
    history_col.update_one(
        {'chat_id': chat_id},
        {'$push': {'history': record}},
        upsert=True
    )

def get_history(chat_id):
    doc = history_col.find_one({'chat_id': chat_id})
    return doc['history'] if doc and 'history' in doc else []

def cleanup_game(chat_id):
    if chat_id in games:
        for player in games[chat_id]['players']:
            active_users.pop(player.id, None)
        games.pop(chat_id)

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_photo(photo=WELCOME_IMG_PATH,
        caption="Welcome to Tic Tac Toe Bot! Use /join to participate.")

async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    game = games.setdefault(chat_id, {
        'players': [],
        'board': [' '] * 9,
        'turn': 0,
        'active': False,
        'last_move_time': datetime.now()
    })

    if user in game['players']:
        await update.message.reply_text("You already joined.")
    elif len(game['players']) < 2:
        game['players'].append(user)
        active_users[user.id] = chat_id
        await update.message.reply_text(f"{user.first_name} joined the game.")
        if len(game['players']) == 2:
            await update.message.reply_text("2 players joined! Use /new to start the game.")
    else:
        await update.message.reply_text("Game already has 2 players.")

async def new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = games.get(chat_id)
    if not game or len(game['players']) != 2:
        await update.message.reply_text("Need 2 players to start. Use /join.")
        return
    game['board'] = [' '] * 9
    game['turn'] = 0
    game['active'] = True
    game['last_move_time'] = datetime.now()
    await update.message.reply_text(
        f"Game started!\n{game['players'][0].mention_html()} vs {game['players'][1].mention_html()}\n\n{format_board(game['board'])}\n\n{game['players'][0].mention_html()}'s turn.",
        parse_mode="HTML"
    )
    asyncio.create_task(timeout_check(chat_id, context.application))

async def end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    cleanup_game(chat_id)
    await update.message.reply_text("Game ended and data cleared.")

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in games:
        games[chat_id]['board'] = [' '] * 9
        games[chat_id]['turn'] = 0
        await update.message.reply_text("Board reset.")

async def move(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = games.get(chat_id)
    if not game or not game['active']:
        return

    user = update.effective_user
    if user != game['players'][game['turn']]:
        return

    try:
        pos = int(update.message.text.strip()) - 1
        if not 0 <= pos <= 8 or game['board'][pos] != ' ':
            return await update.message.reply_text("Invalid move.")

        emoji = user_emojis.get(user.id, DEFAULT_EMOJIS[game['turn']])
        game['board'][pos] = emoji
        game['last_move_time'] = datetime.now()

        if check_winner(game['board']):
            winner = user
            loser = game['players'][1 - game['turn']]
            await update.message.reply_text(f"{format_board(game['board'])}\n\nCongrats {winner.mention_html()}! You win!", parse_mode="HTML")
            update_stats(winner.id, 'win')
            update_stats(loser.id, 'loss')
            save_history(chat_id, f"{winner.first_name} defeated {loser.first_name}")
            cleanup_game(chat_id)
            return

        if is_draw(game['board']):
            await update.message.reply_text(f"{format_board(game['board'])}\n\nIt's a draw!")
            for player in game['players']:
                update_stats(player.id, 'draw')
            save_history(chat_id, "Game ended in draw")
            cleanup_game(chat_id)
            return

        game['turn'] = 1 - game['turn']
        await update.message.reply_text(f"{format_board(game['board'])}\n\n{game['players'][game['turn']].mention_html()}'s turn.", parse_mode="HTML")
    except:
        return

async def single(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in active_users:
        return await update.message.reply_text("You are already in a game!")

    board = [' '] * 9
    games[user.id] = {
        'players': [user, 'bot'],
        'board': board,
        'turn': 0,
        'active': True,
        'last_move_time': datetime.now()
    }
    active_users[user.id] = user.id
    await update.message.reply_text(f"Game vs Computer started!\n\n{format_board(board)}\n\nYour turn (1-9):")

async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in active_users:
        return await update.message.reply_text("You are already in a game!")

    if not context.args:
        return await update.message.reply_text("Usage: /invite @username")

    try:
        username = context.args[0].lstrip('@')
        invited_user = await context.bot.get_chat_member(update.effective_chat.id, username)
        invited_user = invited_user.user

        if invited_user.id in active_users:
            return await update.message.reply_text("That user is already in a game! Try inviting someone else or start a new room.")

        game_id = f"{user.id}_{invited_user.id}"
        games[game_id] = {
            'players': [user, invited_user],
            'board': [' '] * 9,
            'turn': 0,
            'active': True,
            'last_move_time': datetime.now()
        }
        active_users[user.id] = game_id
        active_users[invited_user.id] = game_id

        await update.message.reply_text(f"{user.first_name} invited {invited_user.first_name} to play!\nGame started!\n\n{format_board(games[game_id]['board'])}\n\n{user.first_name}'s turn.")
    except Exception as e:
        await update.message.reply_text("Couldn't find or invite that user.")

# --- Additional Commands ---
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = stats_col.find().sort([("win", -1)]).limit(10)
    text = "\n".join([f"{doc['user_id']}: {doc.get('win',0)}W-{doc.get('loss',0)}L-{doc.get('draw',0)}D" for doc in top])
    await update.message.reply_text("Leaderboard:\n" + text)

async def mystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    stats = get_stats(user.id)
    await update.message.reply_text(
        f"Your stats:\nWins: {stats.get('win', 0)}\nLosses: {stats.get('loss', 0)}\nDraws: {stats.get('draw', 0)}"
    )

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    hist = get_history(chat_id)
    if hist:
        await update.message.reply_text("Game History:\n" + "\n".join(hist))
    else:
        await update.message.reply_text("No history yet.")

async def set_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    if len(args) != 1:
        return await update.message.reply_text("Usage: /emoji [emoji]")
    user_emojis[user.id] = args[0]
    await update.message.reply_text(f"Your emoji is now: {args[0]}")

# --- Main ---
def main():
    token = os.getenv("BOT_TOKEN", "7801621884:AAHmK4MjTuEanUftEhQJezANh0fiF1cLGTY")
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("new", new_game))
    app.add_handler(CommandHandler("end", end))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("mystats", mystats))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("emoji", set_emoji))
    app.add_handler(CommandHandler("single", single))
    app.add_handler(CommandHandler("invite", invite))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, move))

    print("Bot is running...")
    app.run_polling()

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = games.get(chat_id)
    if game and game['active']:
        await update.message.reply_text(f"Current board:\n{format_board(game['board'])}")
    else:
        await update.message.reply_text("No active game.")

if __name__ == "__main__":
    main()