import os
import sqlite3
import logging
import requests
from typing import List, Dict
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# Инициализация
load_dotenv()
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Проверка токенов при старте
if not os.getenv("TELEGRAM_BOT_TOKEN"):
    logger.error("Telegram token not set!")
    exit(1)

if not os.getenv("SPOONACULAR_API_KEY"):
    logger.warning("Spoonacular API key not set - using local recipes")

# Состояния бота
SEARCH, FAVORITES = range(2)


# Инициализация базы данных
def init_db():
    conn = sqlite3.connect("recipes.db")
    cursor = conn.cursor()

    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS products
                   (
                       id INTEGER PRIMARY KEY, name TEXT, added_date TEXT DEFAULT CURRENT_TIMESTAMP, user_id INTEGER
                   )
                   """)

    cursor.execute("""
                   CREATE TABLE IF NOT EXISTS favorites
                   (
                       id INTEGER PRIMARY KEY, recipe_id INTEGER, title TEXT, image TEXT, used_ingredients INTEGER, missed_ingredients INTEGER, user_id INTEGER
                   )
                   """)

    conn.commit()


init_db()


# Функции для работы с Spoonacular API
async def _call_spoonacular_api(ingredients: str) -> List[Dict]:
# Функция для запросов к Spoonacular API
    api_key = os.getenv("SPOONACULAR_API_KEY")
    if not api_key:
        logger.warning("Spoonacular API key not found")
        return []

    ingredients_cleaned = ingredients.strip().replace(" ", "+").lower()
# работа со сторонним апи
    url = "https://api.spoonacular.com/recipes/findByIngredients"
    params = {
        "ingredients": ingredients_cleaned,
        "apiKey": api_key,
        "number": 10,
        "ranking": 2,
        "ignorePantry": True
    }

    try:
        logger.info(f"Requesting Spoonacular API with: {params}")
        response = requests.get(url, params=params, timeout=10)

        if response.status_code == 402:
            logger.error("API limit reached (402 Payment Required)")
            return []

        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return []


async def get_recipe_details(recipe_id: int) -> Dict:
# Получаем детальную информацию о рецепте
    api_key = os.getenv("SPOONACULAR_API_KEY")
    if not api_key:
        return {}

    url = f"https://api.spoonacular.com/recipes/{recipe_id}/information"
    params = {
        "apiKey": api_key,
        "includeNutrition": False
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Форматируем ингредиенты
        ingredients = "\n".join([f"- {i['name']}" for i in data.get("extendedIngredients", [])])

        # Форматируем инструкции
        instructions = ""
        if data.get("analyzedInstructions"):
            for step in data["analyzedInstructions"][0]["steps"]:
                instructions += f"{step['number']}. {step['step']}\n"

        return {
            "ingredients": ingredients or "Не указаны",
            "instructions": instructions or "Не указаны"
        }
    except Exception as e:
        logger.error(f"Error getting recipe details: {str(e)}")
        return {}


def get_local_recipes(ingredients: str) -> List[Dict]:
# Локальная база простых рецептов
    ingredients_lower = ingredients.lower()

    basic_recipes = [
        {
            "id": 1,
            "title": "Омлет с молоком",
            "image": "https://spoonacular.com/recipeImages/1-312x231.jpg",
            "usedIngredientCount": 2,
            "missedIngredientCount": 1
        },
        {
            "id": 2,
            "title": "Блины на молоке",
            "image": "https://spoonacular.com/recipeImages/2-312x231.jpg",
            "usedIngredientCount": 3,
            "missedIngredientCount": 2
        }
    ]

    return [r for r in basic_recipes if
            ("яйца" in ingredients_lower and "молоко" in ingredients_lower)]


def get_local_recipe_details(recipe_id: int) -> Dict:
# Локальные данные о рецепте для демо
    local_recipes = {
        1: {
            "ingredients": "- Яйца\n- Молоко\n- Соль",
            "instructions": "1. Взбить яйца с молоком\n2. Добавить соль\n3. Жарить на сковороде"
        },
        2: {
            "ingredients": "- Молоко\n- Мука\n- Яйца\n- Сахар",
            "instructions": "1. Смешать ингредиенты\n2. Жарить на раскаленной сковороде"
        }
    }
    return local_recipes.get(recipe_id, {})


async def search_recipes(ingredients: str) -> List[Dict]:
    try:
        api_recipes = await _call_spoonacular_api(ingredients)
        if api_recipes:
            return api_recipes

        return get_local_recipes(ingredients)

    except Exception as e:
        logger.error(f"Search failed: {str(e)}")
        return get_local_recipes(ingredients)


# работа с базами данных
def add_favorite(recipe: Dict, user_id: int):
    conn = sqlite3.connect("recipes.db")
    cursor = conn.cursor()

    cursor.execute("""
                   INSERT INTO favorites (recipe_id, title, image, used_ingredients, missed_ingredients, user_id)
                   VALUES (?, ?, ?, ?, ?, ?)
                   """, (
                       recipe["id"],
                       recipe["title"],
                       recipe["image"],
                       recipe["usedIngredientCount"],
                       recipe["missedIngredientCount"],
                       user_id
                   ))

    conn.commit()


def remove_favorite(recipe_id: int, user_id: int):
    conn = sqlite3.connect("recipes.db")
    cursor = conn.cursor()

    cursor.execute("""
                   DELETE
                   FROM favorites
                   WHERE recipe_id = ?
                     AND user_id = ?
                   """, (recipe_id, user_id))

    conn.commit()


def get_favorites(user_id: int) -> List[Dict]:
    conn = sqlite3.connect("recipes.db")
    cursor = conn.cursor()

    cursor.execute("""
                   SELECT recipe_id          as id,
                          title,
                          image,
                          used_ingredients   as usedIngredientCount,
                          missed_ingredients as missedIngredientCount
                   FROM favorites
                   WHERE user_id = ?
                   """, (user_id,))

    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def is_favorite(recipe_id: int, user_id: int) -> bool:
    conn = sqlite3.connect("recipes.db")
    cursor = conn.cursor()

    cursor.execute("""
                   SELECT 1
                   FROM favorites
                   WHERE recipe_id = ?
                     AND user_id = ?
                   """, (recipe_id, user_id))

    return cursor.fetchone() is not None


# Обработчики команд бота
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}!\n\n"
        "Я бот для поиска рецептов по ингредиентам.\n\n"
        "Отправьте мне список ингредиентов, которые у вас есть (например: яйца, молоко, мука), "
        "и я найду подходящие рецепты!\n\n"
        "Доступные команды:\n"
        "/search - поиск рецептов\n"
        "/favorites - просмотр избранных рецептов\n"
        "/help - справка"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Как использовать бота:\n\n"
        "1. Отправьте мне список ингредиентов на английском например: eggs, banana, milk)\n"
        "2. Я найду рецепты, которые можно приготовить\n"
        "3. Сохраняйте понравившиеся рецепты в избранное\n\n"
        "Команды:\n"
        "/search - начать поиск\n"
        "/favorites - избранные рецепты\n"
        "/help - эта справка"
    )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Введите ингредиенты через запятую (например: яйца,молоко,мука):"
    )
    return SEARCH


async def process_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ingredients = update.message.text
    user_id = update.effective_user.id

    await update.message.reply_text(f"Ищу рецепты с: {ingredients}...")

    try:
        recipes = await search_recipes(ingredients)
        if not recipes:
            await update.message.reply_text("Hе найдено рецептов. Попробуйте другие ингредиенты.")
            return ConversationHandler.END
# работа с контекстом пользователя
        context.user_data["recipes"] = recipes
        context.user_data["current_recipe_index"] = 0

        await show_recipe(update, context)
        return SEARCH

    except Exception as e:
        logger.error(f"Error processing search: {str(e)}")
        await update.message.reply_text("Ошибка при поиске. Попробуйте позже.")
        return ConversationHandler.END


async def show_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recipes = context.user_data.get("recipes", [])
    current_index = context.user_data.get("current_recipe_index", 0)
    user_id = update.effective_user.id

    if not recipes or current_index >= len(recipes):
        await update.message.reply_text("Это все рецепты по вашему запросу!")
        return ConversationHandler.END

    recipe = recipes[current_index]
    is_fav = is_favorite(recipe["id"], user_id)

    # Получаем полную информацию о рецепте
    full_recipe_info = await get_recipe_details(recipe["id"]) if os.getenv(
        "SPOONACULAR_API_KEY") else get_local_recipe_details(recipe["id"])

    caption = (
        f"<b>{recipe['title']}</b>\n\n"
        f"<b>Ингредиенты:</b>\n{full_recipe_info.get('ingredients', 'Не указаны')}\n\n"
        f"<b>Инструкции:</b>\n{full_recipe_info.get('instructions', 'Не указаны')}\n\n"
        f"Использовано ингредиентов: {recipe['usedIngredientCount']}\n"
        f"Не хватает: {recipe['missedIngredientCount']}"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "В избранном" if is_fav else "Добавить в избранное",
                callback_data=f"fav_{recipe['id']}"
            )
        ],
        [
            InlineKeyboardButton("Назад", callback_data="prev"),
            InlineKeyboardButton(f"{current_index + 1}/{len(recipes)}", callback_data="count"),
            InlineKeyboardButton("Далее", callback_data="next")
        ],
        [
            InlineKeyboardButton("Закончить", callback_data="done")
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Удаляем предыдущее фото (если есть)
    if "last_photo_message_id" in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data["last_photo_message_id"]
            )
        except:
            pass

    # Отправляем новое фото
    if recipe.get("image"):
        message = await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=recipe["image"],
            caption=caption,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        context.user_data["last_photo_message_id"] = message.message_id
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=caption,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )


async def handle_recipe_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action = query.data
    current_index = context.user_data.get("current_recipe_index", 0)
    recipes = context.user_data.get("recipes", [])

    if action == "prev" and current_index > 0:
        context.user_data["current_recipe_index"] = current_index - 1
    elif action == "next" and current_index < len(recipes) - 1:
        context.user_data["current_recipe_index"] = current_index + 1
    elif action.startswith("fav_"):
        recipe_id = int(action.split("_")[1])
        user_id = query.from_user.id
        recipe = next((r for r in recipes if r["id"] == recipe_id), None)

        if recipe:
            if is_favorite(recipe_id, user_id):
                remove_favorite(recipe_id, user_id)
            else:
                add_favorite(recipe, user_id)

            await query.edit_message_reply_markup(
                reply_markup=get_recipe_keyboard(recipe, user_id, current_index, len(recipes)))
        return
    elif action == "done":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Поиск завершен. Используйте /search для нового поиска.")
        return ConversationHandler.END

    await show_recipe(update, context)


def get_recipe_keyboard(recipe, user_id, current_index, total_recipes):
    is_fav = is_favorite(recipe["id"], user_id)

    keyboard = [
        [
            InlineKeyboardButton(
                "В избранном" if is_fav else "Добавить в избранное",
                callback_data=f"fav_{recipe['id']}"
            )
        ],
        [
            InlineKeyboardButton("Назад", callback_data="prev"),
            InlineKeyboardButton(f"{current_index + 1}/{total_recipes}", callback_data="count"),
            InlineKeyboardButton("Далее", callback_data="next")
        ],
        [
            InlineKeyboardButton("Закончить", callback_data="done")
        ]
    ]

    return InlineKeyboardMarkup(keyboard)


async def show_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    favorites = get_favorites(user_id)

    if not favorites:
        await update.message.reply_text("У вас пока нет избранных рецептов.")
        return

    context.user_data["favorites"] = favorites
    context.user_data["current_favorite_index"] = 0

    await show_favorite_recipe(update, context)
    return FAVORITES


async def show_favorite_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    favorites = context.user_data.get("favorites", [])
    current_index = context.user_data.get("current_favorite_index", 0)
    user_id = update.effective_user.id

    if not favorites or current_index >= len(favorites):
        await update.message.reply_text("Это все ваши избранные рецепты!")
        return ConversationHandler.END

    recipe = favorites[current_index]
    full_recipe_info = await get_recipe_details(recipe["id"]) if os.getenv(
        "SPOONACULAR_API_KEY") else get_local_recipe_details(recipe["id"])

    caption = (
        f"<b>{recipe['title']}</b>\n\n"
        f"<b>Ингредиенты:</b>\n{full_recipe_info.get('ingredients', 'Не указаны')}\n\n"
        f"<b>Инструкции:</b>\n{full_recipe_info.get('instructions', 'Не указаны')}\n\n"
        f"Использовано ингредиентов: {recipe['usedIngredientCount']}\n"
        f"Не хватает: {recipe['missedIngredientCount']}"
    )

    keyboard = [
        [InlineKeyboardButton("Удалить", callback_data=f"remove_{recipe['id']}")],
        [
            InlineKeyboardButton("Назад", callback_data="fav_prev"),
            InlineKeyboardButton(f"{current_index + 1}/{len(favorites)}", callback_data="fav_count"),
            InlineKeyboardButton("Далее", callback_data="fav_next")
        ],
        [InlineKeyboardButton("Закончить", callback_data="fav_done")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Удаляем предыдущее сообщение с фото
    if "last_fav_photo_message_id" in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data["last_fav_photo_message_id"]
            )
        except:
            pass

    # Отправляем новое сообщение
    if recipe.get("image"):
        message = await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=recipe["image"],
            caption=caption,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        context.user_data["last_fav_photo_message_id"] = message.message_id
    else:
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=caption,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        context.user_data["last_fav_photo_message_id"] = message.message_id


async def handle_favorites_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action = query.data
    current_index = context.user_data.get("current_favorite_index", 0)
    favorites = context.user_data.get("favorites", [])

    if action == "fav_prev" and current_index > 0:
        context.user_data["current_favorite_index"] = current_index - 1
    elif action == "fav_next" and current_index < len(favorites) - 1:
        context.user_data["current_favorite_index"] = current_index + 1
    elif action.startswith("remove_"):
        recipe_id = int(action.split("_")[1])
        user_id = query.from_user.id
        remove_favorite(recipe_id, user_id)

        favorites = [f for f in favorites if f["id"] != recipe_id]
        context.user_data["favorites"] = favorites

        if not favorites:
            await query.message.reply_text("Рецепт удален. Избранных рецептов больше нет.")
            return ConversationHandler.END

        if current_index >= len(favorites):
            context.user_data["current_favorite_index"] = len(favorites) - 1

    elif action == "fav_done":
        await query.edit_message_reply_markup(reply_markup=None)
        return ConversationHandler.END

    await show_favorite_recipe(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END


def main():
    application = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

    # Обработчик поиска
    search_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("search", search)],
        states={
            SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_search)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Обработчик избранного
    favorites_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("favorites", show_favorites)],
        states={
            FAVORITES: [CallbackQueryHandler(handle_favorites_navigation)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(search_conv_handler)
    application.add_handler(favorites_conv_handler)
    application.add_handler(CallbackQueryHandler(handle_recipe_navigation))


    # Запуск бота
    application.run_polling()


if __name__ == "__main__":
    main()