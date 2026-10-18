"""«Песочница»: разговор с ИИ (Qwen) через настоящий браузер на сервере.

Здесь нет ни API-ключей, ни своего чат-бэкенда: парсер открывает
https://chat.qwen.ai/ в отдельном браузере с постоянным профилем
(data/qwen_profile). Пользователь входит в аккаунт один раз вручную — окно
браузера живёт на сервере, а профиль запоминает сессию между перезапусками.

Сценарий вопроса (см. реальную вёрстку сайта, классы взяты из разметки):

1. ``page.fill("textarea.message-input-textarea", prompt)`` — поле ввода;
2. клик по кнопке отправки (``.message-input-right-button-send``) либо Enter;
3. ждём, пока индикатор ``.response-loading`` (и карточка
   ``.qwen-chat-status-card-title``) исчезнет — то есть генерация закончена;
4. читаем текст ``.qwen-markdown-text``.

Сайт — живой и меняется, поэтому каждый шаг защищён: если селектор не
нашёлся, пользователь получает понятную ошибку, а не немое зависание.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path

from loguru import logger

#: Куда ведём браузер (без / на конце — редиректы чистые).
QWEN_URL = "https://chat.qwen.ai/"

#: Селекторы реальной вёрстки chat.qwen.ai (проверены на живом сайте).
SEL_INPUT = "textarea.message-input-textarea"
SEL_SEND = ".message-input-right-button-send"
SEL_LOADING = ".response-loading"
SEL_STATUS = ".qwen-chat-status-card-title"
SEL_THINKING_TITLE = ".qwen-chat-thinking-status-card-title-text"
#: Модалка «Добро пожаловать в Qwen» / входа (antd) — всплывает без аккаунта.
SEL_LOGIN_MODAL = ".ant-modal-root"
#: Кнопка входа в шапке (встречается русская и английская вёрстка).
SEL_LOGIN_BTN = (
    'button:has-text("Войти"), button:has-text("Sign in"), button:has-text("Log in")'
)

#: Маркеры правок в ответе ИИ: «ФАЙЛ: путь» … содержимое … «КОНЕЦ-ФАЙЛА».
#: Обычный текст (не markdown-заголовок) — иначе бы срезался при рендере.
EDIT_FILE_MARK = re.compile(r"^[ \t]*ФАЙЛ:[ \t]*(.+?)[ \t]*$", re.M)
EDIT_END_MARK = re.compile(r"^[ \t]*КОНЕЦ-ФАЙЛА[ \t]*$", re.M)

#: Скрытие признаков автоматизации: Google читает navigator.webdriver и
#: бренды userAgentData и отклоняет вход («браузер небезопасен»), если видит
#: робота. Работает в паре с --disable-blink-features=AutomationControlled.
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(Navigator.prototype, 'userAgentData', {
  get: () => ({
    brands: [
      {brand: 'Google Chrome', version: '155'},
      {brand: 'Chromium', version: '155'},
      {brand: 'Not_A Brand', version: '24'},
    ],
    mobile: false,
    platform: 'Linux',
  }),
});
Object.defineProperty(navigator, 'languages', {
  get: () => ['ru-RU', 'ru', 'en-US', 'en'],
});
"""

#: Сколько символов ответа возвращаем в API (дальше — «…»).
MAX_ANSWER_CHARS = 40_000
#: Сколько ждём ответа, секунд. ИИ думает по несколько минут на большом коде.
ASK_TIMEOUT_S = 600
#: Раз через сколько секунд смотрим, не закончил ли ИИ отвечать.
POLL_S = 2.0
#: Сколько подряд «тихих» замеров считаем признаком конца генерации.
STABLE_ROUNDS = 3

#: Папки, которые не тащим в контекст вопроса.
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
#: Максимум символов содержимого одного файла в контексте.
MAX_FILE_CHARS = 6_000
#: Общий потолок контекста (вопрос + файлы) — больше Qwen не проглотит осмысленно.
MAX_CONTEXT_CHARS = 120_000
#: Файлов по умолчанию кладём в контекст (дальше — по одному обрезанные).
MAX_CONTEXT_FILES = 400


class QwenError(RuntimeError):
    """Браузер или сайт Qwen ответили не так, как ожидал парсер."""


# ---------- жизненный цикл браузера ----------

_context = None  # BrowserContext | None (постоянный профиль, окно браузера)
_lock = asyncio.Lock()  # один вопрос за раз — диалог в одном окне


def _executable() -> str:
    """Бинарник браузера: настоящий Chrome предпочтительнее «голого» Chromium.

    Google не пускает через свой OAuth («Войти с Google») небрендированный
    Chromium — поэтому, если установлен Google Chrome, берём его. Путь можно
    задать и вручную: переменная окружения TEAM_SERVER_BROWSER.
    """
    custom = os.environ.get("TEAM_SERVER_BROWSER", "").strip()
    if custom:
        return custom
    for name in ("google-chrome-stable", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    # Локальный Chrome for Testing (брендированный Google Chrome, ставится без
    # sudo распаковкой архива): через него работает «Войти с Google», которое
    # «голый» Chromium получает отказ («браузер небезопасен»).
    cft = Path.home() / ".local" / "share" / "chrome-for-testing" / "chrome-linux64" / "chrome"
    if cft.is_file():
        return str(cft)
    for name in ("chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise QwenError(
        "На сервере не найден браузер (Chrome/Chromium). Установите chromium "
        "или задайте путь переменной TEAM_SERVER_BROWSER."
    )


async def _ensure_page():
    """Возвращает открытую страницу Qwen, поднимая браузер при необходимости."""
    global _context

    if _context is not None and _context.pages:
        page = _context.pages[0]
        if not page.is_closed():
            return page

    from playwright.async_api import async_playwright

    # Постоянный профиль: вход в аккаунт переживает перезапуск сервера.
    profile = Path(__file__).resolve().parent.parent / "data" / "qwen_profile"
    profile.mkdir(parents=True, exist_ok=True)

    pw = await async_playwright().start()
    _context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        executable_path=_executable(),
        headless=False,  # окно видимое: пользователь входит в аккаунт сам
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            # Google не пускает через «Войти с Google» браузеры с признаком
            # автоматизации (navigator.webdriver) — гасим его, иначе отказ
            # «Этот браузер или приложение небезопасны».
            "--disable-blink-features=AutomationControlled",
        ],
        viewport={"width": 1400, "height": 900},
        # Client Hints тоже должны говорить «Google Chrome», а не «Chromium».
        extra_http_headers={
            "Sec-CH-UA": '"Google Chrome";v="155", "Chromium";v="155", "Not_A Brand";v="24"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Linux"',
        },
    )
    # Та же маскировка внутри страниц: webdriver, бренды, языки.
    await _context.add_init_script(script=STEALTH_JS)
    page = _context.pages[0] if _context.pages else await _context.new_page()
    await page.goto(QWEN_URL, wait_until="domcontentloaded", timeout=45_000)
    logger.info("песочница: браузер открыт на {}", page.url)
    return page


async def _login_required(page) -> bool | None:
    """Просит ли сайт войти в аккаунт (модалка приветствия или кнопка «Войти»).

    None — страница недоступна (перезагрузка/сбой), статус неизвестен.
    """
    try:
        modal = page.locator(SEL_LOGIN_MODAL)
        if await modal.count() and await modal.first.is_visible():
            return True
        btn = page.locator(SEL_LOGIN_BTN)
        if await btn.count() and await btn.first.is_visible():
            return True
        return False
    except Exception:
        return None


async def status() -> dict:
    """Что сейчас с браузером: открыт ли, на какой странице, вошли ли в аккаунт."""
    if _context is None or not _context.pages:
        return {"open": False, "url": None, "logged_in": None}
    page = _context.pages[0]
    if page.is_closed():
        return {"open": False, "url": None, "logged_in": None}
    try:
        url = page.url
    except Exception:
        return {"open": True, "url": None, "logged_in": None}
    if "chat.qwen.ai" not in (url or ""):
        # Страница ушла с чата (например, открыт вход через Google) —
        # про аккаунт Qwen снаружи ничего утверждать нельзя.
        return {"open": True, "url": url, "logged_in": None}
    need_login = await _login_required(page)
    return {
        "open": True,
        "url": url,
        "logged_in": None if need_login is None else not need_login,
    }


async def open_browser() -> dict:
    """Открывает (или пробуждает) окно Qwen и ждёт поле ввода."""
    async with _lock:
        page = await _ensure_page()
        try:
            await page.bring_to_front()
        except Exception:
            pass
        try:
            await page.wait_for_selector(SEL_INPUT, timeout=20_000)
        except QwenError:
            raise
        except Exception as exc:
            raise QwenError(
                "Страница Qwen открылась, но поле ввода не появилось. "
                "Войдите в аккаунт в открывшемся окне браузера."
            ) from exc
    return await status()


async def new_chat() -> dict:
    """Начинает свежий диалог: перезагружает страницу Qwen в пустой чат.

    Контекст проекта подклеивается к каждому вопросу, а старая переписка на
    странице Qwen путает ИИ при смене проекта — поэтому «Новый диалог»
    сбрасывает и страницу, и локальную историю чата на фронте.
    """
    async with _lock:
        page = await _ensure_page()
        try:
            await page.bring_to_front()
        except Exception:
            pass
        await page.goto(QWEN_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_selector(SEL_INPUT, timeout=25_000)
        except Exception as exc:
            raise QwenError(
                "Страница Qwen перезагрузилась, но поле ввода не появилось. "
                "Войдите в аккаунт в окне браузера на сервере."
            ) from exc
    return await status()


async def close_browser() -> None:
    """Гасит браузер (при остановке сервера)."""
    global _context
    ctx, _context = _context, None
    if ctx is not None:
        try:
            await ctx.close()
        except Exception:
            pass


# ---------- сам вопрос ----------


async def ask(prompt: str, context: str = "", timeout_s: int = ASK_TIMEOUT_S) -> str:
    """Вводит промпт в Qwen и возвращает текст готового ответа.

    Окно браузера при этом открыто на сервере — пользователь видит, как ИИ
    печатает. Ответ берём из последнего ``.qwen-markdown-text`` после того,
    как индикатор загрузки погас и текст перестал меняться. ``context``
    (файлы проекта) подклеивается к вопросу, чтобы ИИ видел код.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        raise QwenError("Пустой промпт — введите вопрос к ИИ.")
    full = prompt
    if context:
        full = f"{prompt}\n\n{context.strip()}"

    async with _lock:  # диалог идёт в одном окне, не пускаем два вопроса сразу
        page = await _ensure_page()
        try:
            await page.bring_to_front()
        except Exception:
            pass

        # На всякий случай возвращаемся на чат: пользователь мог уйти на другой сайт.
        if "chat.qwen.ai" not in page.url:
            await page.goto(QWEN_URL, wait_until="domcontentloaded", timeout=45_000)

        try:
            await page.wait_for_selector(SEL_INPUT, timeout=25_000)
        except Exception as exc:
            raise QwenError(
                "Не вижу поля ввода Qwen. Войдите в аккаунт в окне браузера "
                "на сервере и повторите вопрос."
            ) from exc

        before = await _answer_text(page)

        await page.fill(SEL_INPUT, full)
        await _send(page)

        text = await _wait_answer(page, before, timeout_s)
    return text


async def _send(page) -> None:
    """Отправляет сообщение: кнопкой отправки, либо Enter как запасной вариант."""
    try:
        btn = page.locator(SEL_SEND)
        if await btn.count() and await btn.first.is_visible():
            await btn.first.click()
            return
    except Exception:
        pass
    await page.press(SEL_INPUT, "Enter")


async def _answer_text(page) -> str:
    """Текст последнего ответа ИИ целиком.

    В новой вёрстке Qwen каждый фрагмент — отдельный span, поэтому берём
    контейнер последнего ассистентского ответа (.response-message-content),
    который даёт полный текст целиком.
    """
    try:
        loc = page.locator(
            ".qwen-chat-message-assistant .response-message-content"
        )
        if await loc.count():
            text = (await loc.last.inner_text() or "").strip()
            if text:
                return text
    except Exception:
        pass

    # запасной: если контейнер ответа сильно изменился — склеиваем все
    # .qwen-markdown-text внутри последней .custom-qwen-markdown
    try:
        scope = page.locator(".custom-qwen-markdown")
        if await scope.count():
            texts: list[str] = []
            markdown = scope.last
            for span in await markdown.locator(".qwen-markdown-text").all():
                t = (await span.inner_text() or "").strip()
                if t:
                    texts.append(t)
            if texts:
                return "\n".join(texts)
    except Exception:
        pass

    # совсем старый запасной вариант
    try:
        blocks = page.locator(SEL_ANSWER)
        if await blocks.count():
            return (await blocks.last.inner_text() or "").strip()
    except Exception:
        return ""
    return ""


async def _busy(page) -> bool:
    """Горит ли индикатор «ИИ думает» (загрузка, карточка статуса, размышление)."""
    try:
        load = page.locator(SEL_LOADING)
        if await load.count() and await load.first.is_visible():
            return True
    except Exception:
        pass

    # Карточка размышления: есть текст и нет слова «Завершено/Отменено»
    try:
        title = page.locator(SEL_THINKING_TITLE)
        if await title.count():
            for t in await title.all():
                try:
                    txt = (await t.inner_text() or "").strip().lower()
                except Exception:
                    continue
                if not txt:
                    continue
                if "заверш" not in txt and "отмен" not in txt:
                    return True
    except Exception:
        pass

    try:
        st = page.locator(SEL_STATUS)
        if await st.count() and await st.first.is_visible():
            try:
                txt = (await st.first.inner_text() or "").strip().lower()
                if txt and "заверш" in txt:
                    return False
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False


async def _wait_answer(page, before: str, timeout_s: int) -> str:
    """Ждёт конца генерации: индикатор погас + текст не менялся несколько раз."""
    deadline = time.monotonic() + timeout_s
    seen_activity = False   # замечали ли вообще загрузку/смену текста
    last = before
    stable = 0

    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_S)

        # Без входа отправка всплывает модалкой «Добро пожаловать в Qwen» —
        # ловим сразу, а не ждём таймаута в минут.
        if await _login_required(page):
            raise QwenError(
                "Qwen просит войти в аккаунт: в окне браузера на сервере нажмите "
                "«Войти», завершите вход и повторите вопрос."
            )

        busy = await _busy(page)
        current = await _answer_text(page)

        changed = current != last and current != before
        if busy or changed:
            seen_activity = True
        if current != last:
            last, stable = current, 0
            continue
        if not busy and seen_activity:
            stable += 1
            if stable >= STABLE_ROUNDS and last and last != before:
                return _clip(last)
        elif not seen_activity and not busy:
            # Ответ мог прийти мгновенно, без индикатора.
            if current and current != before:
                stable += 1
                if stable >= STABLE_ROUNDS:
                    return _clip(current)

    raise QwenError(
        f"Qwen не закончила ответ за {timeout_s} с. "
        "Проверьте окно браузера на сервере (возможно, появился капча или запрос входа)."
    )


def _clip(text: str) -> str:
    text = text.strip()
    if len(text) > MAX_ANSWER_CHARS:
        text = text[:MAX_ANSWER_CHARS] + "\n\n… (ответ обрезан)"
    return text


# ---------- контекст проекта для вопроса ----------


#: Инструкция ИИ о том, как помечать правки файлов в ответе (иначе разбор
#: ответа не сложить: markdown-заголовки при рендере теряют «###», поэтому
#: маркеры — обычные строки текста, которые переживают inner_text).
EDIT_RULES = """
Как предлагать правки файлов. Если нужно изменить какой-то файл проекта, добавь
в конец ответа блок ровно в таком виде (отдельными строками):

ФАЙЛ: имя/файла.py
```python
полное новое содержимое файла целиком
```
КОНЕЦ-ФАЙЛА

Строки «ФАЙЛ:» и «КОНЕЦ-ФАЙЛА» пиши без изменений, содержимое всегда клади в
кодовый блок. Таких блоков может быть несколько — каждый заканчивай строкой
«КОНЕЦ-ФАЙЛА». Если правки не нужны — не добавляй такие блоки."""


def _looks_binary(chunk: bytes) -> bool:
    """Хвастаются ли в начале файла нули (признак бинарника, который не тащим)."""
    return b"\x00" in chunk


def build_project_context(root: Path, project_name: str, limit: int = MAX_CONTEXT_CHARS) -> str:
    """Собирает файлы проекта в текст, чтобы ИИ видел код перед ответом.

    Возвращает пустую строку, если читать нечего. Каждый файл кладётся в
    блок вида ``### путь`` + содержимое; слишком большие файлы обрезаются,
    а общий объём упирается в ``limit`` — вопрос не должен теряться в коде.
    """
    try:
        root = root.resolve()
        if not root.is_dir():
            return ""
    except OSError:
        return ""

    parts: list[str] = [f"Файлы проекта «{project_name}» (контекст для ответа):"]
    used = len(parts[0])
    count = 0
    skipped = 0

    for path in sorted(root.rglob("*")):
        if count >= MAX_CONTEXT_FILES or used >= limit:
            break
        if path.is_dir():
            continue  # служебные каталоги отсекаем ниже, по частям пути
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            skipped += 1
            continue

        try:
            with path.open("rb") as fh:
                raw = fh.read(MAX_FILE_CHARS * 4)
            if _looks_binary(raw[:1024]):
                skipped += 1
                continue
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue

        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n… (файл обрезан)"
        block = f"\n### {path.relative_to(root).as_posix()}\n{text}\n"
        if used + len(block) > limit:
            break
        parts.append(block)
        used += len(block)
        count += 1

    if count == 0:
        return ""
    note = f"\n(в контекст вошло файлов: {count}"
    if skipped:
        note += f", пропущено служебных/бинарных: {skipped}"
    parts.append(note + ")")
    parts.append(EDIT_RULES)
    return "\n".join(parts)


def parse_file_edits(answer: str) -> list[dict[str, str]]:
    """Достаёт из ответа ИИ блоки «ФАЙЛ: путь» … «КОНЕЦ-ФАЙЛА».

    Возвращает список ``[{"path": ..., "content": ...}, …]``. Это только
    разбор текста: существует ли файл и не выходит ли путь за корень проекта —
    проверяется заново при применении (main._safe_child).
    """
    if not answer or "ФАЙЛ:" not in answer:
        return []
    edits: list[dict[str, str]] = []
    marks = list(EDIT_FILE_MARK.finditer(answer))
    for i, mark in enumerate(marks):
        path = mark.group(1).strip().strip("`").strip()
        if not path:
            continue
        stop = len(answer)
        end_m = EDIT_END_MARK.search(answer, mark.end())
        next_m = marks[i + 1].start() if i + 1 < len(marks) else len(answer)
        if end_m and end_m.start() < next_m:
            stop = end_m.start()
        else:
            stop = next_m  # «КОНЕЦ» нет или дальше — до следующего «ФАЙЛ:»/конца
        # Убираем только перевод строки после маркера — хвостовой \n файла сохраняем.
        content = answer[mark.end() : stop].lstrip("\n")
        if content:
            edits.append({"path": path, "content": content})
    return edits
