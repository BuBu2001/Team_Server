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
import json
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


def _safe_url(url: str) -> str:
    """URL без query-строки — для логов.

    В параметрах OAuth-редиректов (github.com → qwen и обратно) бывают
    разовые коды и state — в логи они попадать не должны.
    """
    return (url or "").split("?", 1)[0]


def _harden_profile(profile: Path) -> None:
    """Приводит профиль браузера к безопасному виду перед запуском.

    - каталог профиля — только для владельца (700): куки и данные входа не
      читаются другими юзерами машины, папку даже листать нельзя;
    - менеджер паролей выключен: служебный профиль не должен хранить пароли
      от GitHub и прочих аккаунтов — Chrome даже не предложит их сохранить.
      Важно и потому, что Playwright запускает Chrome с ``--password-store=basic``
      (без ключевого хранилища ОС): сохранённое там хранилось бы слабо.
    """
    try:
        profile.chmod(0o700)
    except OSError:
        pass
    prefs_path = profile / "Default" / "Preferences"
    try:
        if prefs_path.is_file():
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
            if not isinstance(prefs, dict):
                raise ValueError("Preferences — не объект")
        else:
            prefs = {}  # свежий профиль — создаём файл со своими ключами
        prefs["credentials_enable_service"] = False  # не предлагать сохранять пароли
        prof = prefs.setdefault("profile", {})
        if isinstance(prof, dict):
            prof["password_manager_enabled"] = False  # и не сохранять их вообще
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        prefs_path.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
        prefs_path.chmod(0o600)
    except (OSError, ValueError):
        pass  # не вышло — просто не применяем (Chrome создаст профиль сам)


def profile_dir() -> Path:
    """Папка постоянного профиля браузера (входы, куки, история, пароли)."""
    return Path(__file__).resolve().parent.parent / "data" / "qwen_profile"


async def _ensure_page():
    """Возвращает открытую страницу Qwen, поднимая браузер при необходимости."""
    global _context

    if _context is not None and _context.pages:
        page = _context.pages[0]
        if not page.is_closed():
            return page

    from playwright.async_api import async_playwright

    # Постоянный профиль: вход в аккаунт переживает перезапуск сервера.
    profile = profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    _harden_profile(profile)  # 700 + менеджер паролей выключен

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
    logger.info("песочница: браузер открыт на {}", _safe_url(page.url))
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


async def forget_profile() -> dict:
    """Стирает профиль браузера: все входы, куки, история, сохранённые пароли.

    Следов прошлого входа не остаётся — следующее открытие браузера чистое
    (в аккаунт Qwen придётся войти заново). Вызывается при входе в веб-панель
    с нового IP. Возвращает статус браузера.
    """
    await close_browser()  # сначала гасим — иначе Chrome держит файлы профиля
    try:
        shutil.rmtree(profile_dir())
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise QwenError(f"Не удалось стереть профиль браузера: {exc}") from exc
    logger.info("песочница: профиль браузера стёр — все входы удалены")
    return await status()


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
        page = await _ensure_chat_page()
        return await _turn(page, full, timeout_s)


async def _ensure_chat_page():
    """Страница Qwen, готовая к вводу (без захвата лока)."""
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
    return page


async def _turn(page, text: str, timeout_s: int) -> str:
    """Отправляет одно сообщение в открытый диалог и возвращает ответ."""
    before = await _answer_text(page)
    await page.fill(SEL_INPUT, text)
    await _send(page)
    return await _wait_answer(page, before, timeout_s)


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


def build_project_context(
    root: Path, project_name: str, limit: int = MAX_CONTEXT_CHARS, rules: str = EDIT_RULES
) -> str:
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
    parts.append(rules)
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


# ---------- режим агента: инструменты вместо ручных правок ----------

#: Маркеры вызова инструмента: «ИНСТРУМЕНТ: имя» отдельной строкой, аргументы
#: «ФАЙЛ:»/«ЗАПРОС:», конец блока — «КОНЕЦ-ИНСТРУМЕНТА». Как и маркеры EDIT_RULES,
#: обычный текст: markdown при рендере теряет заголовки, а мы читаем inner_text.
AGENT_TOOL_MARK = re.compile(r"^[ \t]*ИНСТРУМЕНТ:[ \t]*(\S+)[ \t]*$", re.M | re.I)
AGENT_END_MARK = re.compile(r"КОНЕЦ-ИНСТРУМЕНТА", re.I)
AGENT_FILE_ARG = re.compile(r"^[ \t]*ФАЙЛ:[ \t]*(.+?)[ \t]*$", re.M)
AGENT_QUERY_ARG = re.compile(r"^[ \t]*ЗАПРОС:[ \t]*(.+?)[ \t]*$", re.M)
AGENT_NAME_ARG = re.compile(r"^[ \t]*НАЗВАНИЕ:[ \t]*(.+?)[ \t]*$", re.M)

#: Имена инструментов, которые понимает агент.
AGENT_TOOLS = {"список", "чтение", "запись", "поиск", "создать", "конец"}
#: Сколько шагов (инструментов) максимум в одном ответе — защита от зацикливания.
AGENT_MAX_STEPS = 8
#: Потолок содержимого одной «записи» (согласовано с MAX_FILE_EDIT_CHARS в main).
AGENT_MAX_WRITE_CHARS = 400_000
#: Сколько файлов смотрим в поиске и сколько совпадений отдаём ИИ.
AGENT_SEARCH_FILES = 400
AGENT_MAX_HITS = 60
#: Сколько записей отдаём в «список» одной папки.
AGENT_MAX_LIST = 200

#: Инструкция ИИ для режима агента (вместо EDIT_RULES добавляется в контекст).
AGENT_RULES = """
Режим агента. Ты не просто отвечаешь — ты работаешь инструментами над файлами
проекта на сервере. Действие выполняется блоком ровно в таком виде (строки
пиши без изменений, каждая на своей строке):

ИНСТРУМЕНТ: имя
ФАЙЛ: путь/к/файлу       (для «список», «чтение», «запись»)
ЗАПРОС: строка поиска    (только для «поиск»)
НАЗВАНИЕ: имя            (только для «создать»)
кодовый блок с полным содержимым файла (только для «запись»)
КОНЕЦ-ИНСТРУМЕНТА

Доступные инструменты:
- «список» — показать содержимое папки (ФАЙЛ: путь, «.» — корень проекта);
- «чтение» — показать содержимое файла (ФАЙЛ: путь);
- «поиск» — найти строки во всех файлах проекта (ЗАПРОС: строка);
- «запись» — записать файл целиком (ФАЙЛ: путь, ниже — кодовый блок с полным
  новым содержимым файла). Файл сохраняется на сервере сразу, без
  подтверждения — предлагай только то, что действительно нужно изменить;
- «создать» — создать новый репозиторий (проект) на сервере (НАЗВАНИЕ: имя).
  Он сразу появляется в списке проектов, и дальше инструменты работают уже
  в нём — можно писать файлы в новый репозиторий;
- «конец» — закончить работу.

После блока с инструментом придёт результат — обычный текст, после которого
действуй дальше, пока задача не выполнена. За один ответ можно давать
несколько блоков подряд. Если менять файлы не нужно — ответь сразу обычным
текстом, без блоков инструментов. Закончив, кратко ответь, что сделано."""

#: Инструкция для обычного (неагентского) чата: только создание репозитория —
#: файловые правки там идут по старому протоколу «ФАЙЛ: … КОНЕЦ-ФАЙЛА».
#: Важно, чтобы она стояла в самом конце промпта (после файлов проекта):
#: прочитанная последней, она срабатывает намного надёжнее — иначе ИИ отвечает
#: обычным текстом и выдаёт команды терминала вместо блока.
CREATE_RULES = """
СОЗДАНИЕ РЕПОЗИТОРИЯ (главное правило). Если просят создать репозиторий, репо
или новый проект — обязательно добавь в ответ блок ровно в таком виде
(строки пиши без изменений, каждая на своей строке):

ИНСТРУМЕНТ: создать
НАЗВАНИЕ: имя репозитория
КОНЕЦ-ИНСТРУМЕНТА

Этот блок выполняется на сервере сам: репозиторий создаётся сразу и
появляется в списке проектов. Поэтому НЕ выдавай пользователю команды
терминала (mkdir, git init, git add, git commit, git push), НЕ предлагай
GitHub/GitLab и НЕ спрашивай ссылку на удалённый репозиторий — просто выполни
блок и напиши, что репозиторий создан. Если просят ещё и залить его на
удалённый сервер или в GitHub — создай репозиторий блоком, а про push к
своему remote скажи, что пользователь сделает это сам со своего компьютера.
Если не просят создать репозиторий — не добавляй такие блоки."""


def parse_agent_calls(answer: str) -> list[dict[str, str]]:
    """Достаёт из ответа ИИ блоки «ИНСТРУМЕНТ: имя … КОНЕЦ-ИНСТРУМЕНТА».

    Возвращает список вызовов ``[{"tool", "path", "query", "name", "content"}, …]``.
    Это только разбор текста: не выходит ли путь за корень проекта и само
    действие проверяются заново при выполнении (``run_tool`` → ``_agent_child``).
    """
    if not answer or not AGENT_TOOL_MARK.search(answer):
        return []
    marks = list(AGENT_TOOL_MARK.finditer(answer))
    calls: list[dict[str, str]] = []
    for i, mark in enumerate(marks):
        tool = mark.group(1).strip().strip("`").lower().rstrip(":.,;")
        end_m = AGENT_END_MARK.search(answer, mark.end())
        next_m = marks[i + 1].start() if i + 1 < len(marks) else len(answer)
        # Область вызова: до «КОНЕЦ-ИНСТРУМЕНТА», либо до следующего блока/конца.
        stop = next_m
        if end_m and end_m.start() < next_m:
            stop = end_m.start()
        region = answer[mark.end() : stop]

        file_m = AGENT_FILE_ARG.search(region)
        query_m = AGENT_QUERY_ARG.search(region)
        name_m = AGENT_NAME_ARG.search(region)
        path = file_m.group(1).strip().strip("`") if file_m else ""
        query = query_m.group(1).strip() if query_m else ""
        name = name_m.group(1).strip().strip("`") if name_m else ""
        content = _agent_content(region[file_m.end() :]) if file_m else ""
        calls.append(
            {
                "tool": tool,
                "path": path,
                "query": query,
                "name": name,
                "content": content,
            }
        )
    return calls


def strip_tool_calls(answer: str, tools: set[str] | None = None) -> str:
    """Убирает из текста ответа блоки «ИНСТРУМЕНТ: …» (все или только ``tools``).

    Нужно, чтобы сырой протокол не показывался пользователю: например, в
    обычном чате блок «создать» выполняется на сервере, а в ответе остаётся
    только человеческий текст.
    """
    if not answer or not AGENT_TOOL_MARK.search(answer):
        return answer
    marks = list(AGENT_TOOL_MARK.finditer(answer))
    pieces: list[str] = []
    last = 0
    for i, mark in enumerate(marks):
        tool = mark.group(1).strip().strip("`").lower().rstrip(":.,;")
        if tools is not None and tool not in tools:
            continue  # чужой блок не трогаем: он попадёт в соседний сохраняемый кусок
        end_m = AGENT_END_MARK.search(answer, mark.end())
        next_m = marks[i + 1].start() if i + 1 < len(marks) else len(answer)
        stop = next_m
        if end_m and end_m.start() < next_m:
            stop = end_m.end()
        pieces.append(answer[last : mark.start()])
        last = stop
    pieces.append(answer[last:])
    return re.sub(r"\n{3,}", "\n\n", "".join(pieces)).strip("\n")


def _agent_content(raw: str) -> str:
    """Содержимое «записи»: строка «ФАЙЛ:» уже снята, снимаем кодовый блок."""
    text = raw.strip("\n")
    if not text:
        return ""
    # Хвостовой маркер мог остаться в той же области (без перевода строки).
    text = re.sub(r"\s*КОНЕЦ-ИНСТРУМЕНТА\s*$", "", text, flags=re.I).strip("\n")
    if text.startswith("```"):
        first, sep, rest = text.partition("\n")
        if not sep:  # блок без содержимого — просто снимаем сами обратные кавычки
            return text[3:].rstrip("`")
        text = rest
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip("\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def _agent_child(root: Path, rel: str) -> Path:
    """Разрешает ``rel`` строго внутри ``root`` (как main._safe_child, но локально)."""
    rel = (rel or "").strip().lstrip("/")
    target = (root / rel).resolve() if rel else root
    if target != root and root not in target.parents:
        raise ValueError("недопустимый путь — только файлы внутри проекта")
    return target


def _tool_error(msg: str) -> tuple[str, str]:
    """(результат для ИИ, запись в журнал) при ошибке вызова."""
    return f"Ошибка: {msg}. Исправь аргументы и повтори вызов.", f"ошибка: {msg}"


def _tool_list(call: dict[str, str], root: Path) -> tuple[str, str]:
    rel = call.get("path") or "."
    target = _agent_child(root, rel)
    if not target.is_dir():
        raise ValueError(f"«{rel}» — не папка")
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines: list[str] = []
    for child in entries:
        if child.name in _SKIP_DIRS:
            continue
        if len(lines) >= AGENT_MAX_LIST:
            lines.append("… (список обрезан)")
            break
        lines.append(child.name + "/" if child.is_dir() else child.name)
    shown = "." if target == root else target.relative_to(root).as_posix()
    return "\n".join(lines) or "(папка пуста)", f"показана папка {shown}"


def _tool_read(call: dict[str, str], root: Path) -> tuple[str, str]:
    rel = (call.get("path") or "").strip()
    if not rel:
        raise ValueError("не указан ФАЙЛ")
    target = _agent_child(root, rel)
    if not target.is_file():
        raise ValueError(f"файл «{rel}» не найден")
    raw = target.read_bytes()[: MAX_FILE_CHARS * 4]
    if _looks_binary(raw[:1024]):
        raise ValueError(f"«{rel}» — бинарный файл, его не читаю")
    text = raw.decode("utf-8", errors="replace")
    truncated = len(text) > MAX_FILE_CHARS
    if truncated:
        text = text[:MAX_FILE_CHARS] + "\n… (файл обрезан)"
    note = f"прочитан {rel} ({len(text)} символов" + (", обрезан)" if truncated else ")")
    return text, note


def _tool_search(call: dict[str, str], root: Path) -> tuple[str, str]:
    query = (call.get("query") or "").strip()
    if not query:
        raise ValueError("не указан ЗАПРОС")
    needle = query.casefold()
    hits: list[str] = []
    scanned = 0
    for path in sorted(root.rglob("*")):
        if len(hits) >= AGENT_MAX_HITS or scanned >= AGENT_SEARCH_FILES:
            break
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        scanned += 1
        try:
            raw = path.read_bytes()[: 256 * 1024]
        except OSError:
            continue
        if _looks_binary(raw[:1024]):
            continue
        for no, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            if needle in line.casefold():
                hits.append(
                    f"{path.relative_to(root).as_posix()}:{no}: {line.strip()[:200]}"
                )
                if len(hits) >= AGENT_MAX_HITS:
                    break
    if not hits:
        return (
            f"Совпадений для «{query}» нет (просмотрено файлов: {scanned}).",
            f"поиск «{query[:40]}»: совпадений нет",
        )
    extra = "\n(могут быть ещё — сузь запрос)" if len(hits) >= AGENT_MAX_HITS else ""
    return (
        "\n".join(hits) + extra,
        f"поиск «{query[:40]}»: {len(hits)} совпадений",
    )


def _tool_write(call: dict[str, str], root: Path) -> tuple[str, str]:
    rel = (call.get("path") or "").strip()
    if not rel:
        raise ValueError("не указан ФАЙЛ")
    content = call.get("content") or ""
    if len(content) > AGENT_MAX_WRITE_CHARS:
        raise ValueError(
            f"содержимое слишком большое (максимум {AGENT_MAX_WRITE_CHARS} символов)"
        )
    target = _agent_child(root, rel)
    if target.is_dir():
        raise ValueError(f"«{rel}» — папка, а не файл")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return (
        f"Файл {rel} записан ({len(content)} символов).",
        f"записан {rel} ({len(content)} символов)",
    )


def run_tool(call: dict[str, str], root: Path | None) -> tuple[str, str]:
    """Выполняет один вызов агента. Возвращает (результат для ИИ, шаг журнала).

    «запись» применяется к файлам проекта сразу — режим агента работает без
    ручного «Применить». Пути проверяются ``_agent_child`` (только внутри root).
    Без выбранного проекта (``root is None``) файловые инструменты просят
    сначала создать репозиторий — их выполняет вызов «создать» (см. ``run_agent``).
    """
    tool = (call.get("tool") or "").lower()
    if root is None and tool in {"список", "чтение", "поиск", "запись"}:
        return _tool_error("проект не выбран — сначала создай репозиторий (инструмент «создать»)")
    try:
        if tool == "список":
            return _tool_list(call, root)
        if tool == "чтение":
            return _tool_read(call, root)
        if tool == "поиск":
            return _tool_search(call, root)
        if tool == "запись":
            return _tool_write(call, root)
        if tool == "конец":
            return "Работа завершена.", "завершил работу"
        return _tool_error(f"неизвестный инструмент «{tool}»")
    except ValueError as exc:
        return _tool_error(str(exc))
    except OSError as exc:
        return _tool_error(f"не удалось выполнить: {exc}")


def _tool_create(call: dict[str, str], create_repo) -> tuple[str, str, Path | None]:
    """Инструмент «создать» — новый репозиторий (проект) на сервере.

    Возвращает (результат для ИИ, шаг журнала, папка нового репозитория или
    None, если не создалось). ``create_repo(name)`` — колбэк из main: только
    он знает про БД, права пользователя и каталог проектов; сам разбор имени
    («НАЗВАНИЕ:», иначе «ФАЙЛ:») — здесь.
    """
    name = (call.get("name") or call.get("path") or "").strip()
    if not name:
        result, note = _tool_error("не указано НАЗВАНИЕ репозитория")
        return result, note, None
    if create_repo is None:
        result, note = _tool_error("создание репозиториев сейчас недоступно")
        return result, note, None
    try:
        result, note, new_root = create_repo(name)
    except ValueError as exc:
        result, note = _tool_error(str(exc))
        return result, note, None
    except OSError as exc:
        result, note = _tool_error(f"не удалось создать репозиторий: {exc}")
        return result, note, None
    return result, note, Path(new_root)


# ---------- родной режим агента на сайте Qwen ----------

#: Кнопка открытия меню режимов в поле ввода (aria-label «Выбрать режим»).
SEL_MODE_OPEN = ".mode-select-open"
#: Пункты открытого меню режимов и их названия (вёрстка .mode-select).
SEL_MODE_ITEM = ".mode-select-dropdown-item"
SEL_MODE_ITEM_NAME = ".mode-select-dropdown-item-name"
#: Класс галочки у выбранного пункта меню.
SEL_MODE_ITEM_CHECKED = ".mode-select-dropdown-item-icon-checked"
#: Чип «текущий режим» — появляется, когда режим уже включён (в меню или у поля ввода).
SEL_MODE_CURRENT = ".mode-select-current-mode"
#: Крестик выключения на чипе текущего режима.
SEL_MODE_CURRENT_CLOSE = ".mode-select-current-mode-close"
#: Иконка внутри кнопки меню режимов — показывает текущий режим.
SEL_MODE_OPEN_ICON = ".mode-select-open-icon"
#: Контейнер текущего режима внутри кнопки меню (пуст, пока режим не включён).
SEL_MODE_OPEN_MODE = ".mode-select-open-mode-select"
#: Класс «кнопка меню в активном состоянии» (режим поверх обычного чата).
SEL_MODE_OPEN_ACTIVE = ".mode-select-open-active"

#: Сколько секунд ждём подтверждения переключения (UI обновляется не мгновенно).
AGENT_MODE_WAIT_S = 8.0

#: Как узнать пункт «режим агента»: имя в любой локали либо иконка вида qwpcicon-agentMode.
_AGENT_ITEM_NAME_RE = re.compile(r"agent|агент", re.I)
_AGENT_ITEM_ICON_RE = re.compile(r"(appicon|qwpcicon)-agent", re.I)


async def _mode_menu_open(page) -> bool:
    try:
        return await page.locator(SEL_MODE_ITEM).count() > 0
    except Exception:
        return False


async def _mode_agent_item(page):
    """Пункт «режим агента» в открытом меню режимов (None — пункта нет)."""
    items = page.locator(SEL_MODE_ITEM)
    for i in range(await items.count()):
        item = items.nth(i)
        try:
            html = await item.inner_html()
            if _AGENT_ITEM_ICON_RE.search(html):
                return item
            name = item.locator(SEL_MODE_ITEM_NAME)
            if await name.count():
                text = (await name.first.inner_text() or "").strip()
                if _AGENT_ITEM_NAME_RE.search(text):
                    return item
        except Exception:
            continue
    return None


async def _close_mode_menu(page) -> None:
    """Закрывает меню режимов, если оно открыто (Escape не трогает черновик)."""
    if await _mode_menu_open(page):
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass


async def _open_mode_menu(page) -> None:
    """Открывает меню режимов (не делает ничего, если оно уже открыто)."""
    if await _mode_menu_open(page):
        return
    trigger = page.locator(SEL_MODE_OPEN)
    if not await trigger.count():
        raise QwenError(
            "На странице Qwen нет меню режимов (кнопка «Выбрать режим») — "
            "возможно, сайт изменил вёрстку."
        )
    await trigger.first.click(timeout=10_000)
    await page.wait_for_selector(SEL_MODE_ITEM, timeout=5_000)


async def _menu_agent_checked(page) -> bool | None:
    """Галочка пункта «режим агента» в открытом меню (None — меню/пункта нет)."""
    if not await _mode_menu_open(page):
        return None
    item = await _mode_agent_item(page)
    if item is None:
        return None
    try:
        return await item.locator(SEL_MODE_ITEM_CHECKED).count() > 0
    except Exception:
        return None


async def _page_has(page, sel: str) -> bool:
    try:
        return await page.locator(sel).count() > 0
    except Exception:
        return False


async def _agent_sign_on_page(page) -> bool | None:
    """Агент включён — видно без меню: чип режима либо иконка кнопки (None — не ясно)."""
    for sel in (SEL_MODE_CURRENT, SEL_MODE_OPEN_MODE, SEL_MODE_OPEN_ICON):
        try:
            loc = page.locator(sel)
            for i in range(await loc.count()):
                if _AGENT_ITEM_ICON_RE.search(await loc.nth(i).inner_html()):
                    return True
        except Exception:
            continue
    return None


def _norm_html(html: str) -> str:
    """Убирает генерируемые React id (``_r_7i``) — они меняются при каждом рендере."""
    return re.sub(r"_r_[a-z0-9]+", "_r_", html)


async def _mode_agent_item_sig(page) -> str | None:
    """outerHTML пункта «режим агента» — дифф до/после нажатия как последний аргумент."""
    try:
        if not await _mode_menu_open(page):
            return None
        item = await _mode_agent_item(page)
        if item is None:
            return None
        return _norm_html(await item.evaluate("el => el.outerHTML"))
    except Exception:
        return None


async def _click_mode_control(page, on: bool) -> bool:
    """Жмёт то, что переключает режим (пункт меню либо «×» чипа). False — нажать нечего."""
    try:
        # пункт в открытом меню
        if await _mode_menu_open(page):
            item = await _mode_agent_item(page)
            if item is not None:
                checked = await item.locator(SEL_MODE_ITEM_CHECKED).count() > 0
                if checked == on:
                    return True  # состояние уже нужное (гонка чтения) — второй клик не нужен
                await item.click(timeout=10_000)
                return True
        # выключаем: активный режим может быть чипом с крестиком (пункт в меню отсутствует)
        if on is False:
            close_btn = page.locator(SEL_MODE_CURRENT_CLOSE)
            if await close_btn.count():
                await close_btn.first.click(timeout=10_000)
                return True
        # меню закрыто — открываем и жмём пункт
        await _open_mode_menu(page)
        item = await _mode_agent_item(page)
        if item is None:
            return False
        await item.click(timeout=10_000)
        return True
    except Exception:
        return False


async def _menu_debug(page) -> str:
    """Снимок пунктов меню режимов в журнал — помогает чинить вёрстку при неудаче."""
    try:
        if not await _mode_menu_open(page):
            await _open_mode_menu(page)
        items = page.locator(SEL_MODE_ITEM)
        out: list[str] = []
        for i in range(min(await items.count(), 10)):
            item = items.nth(i)
            html = await item.inner_html()
            name_loc = item.locator(SEL_MODE_ITEM_NAME)
            name = (
                (await name_loc.first.inner_text() or "").strip()
                if await name_loc.count()
                else "?"
            )
            flags = "checked" if SEL_MODE_ITEM_CHECKED in html else "-"
            if _AGENT_ITEM_ICON_RE.search(html):
                flags += ",agentIcon"
            # какие вообще классы состояния есть у пункта — это и есть подсказка для правки
            marks = re.findall(r'class="([^"]*(?:check|current|active|select)[^"]*)"', html)
            if marks:
                flags += " [" + " | ".join(m.strip()[:48] for m in marks[:3]) + "]"
            out.append(f"{name[:24]!r}({flags})")
        extra = f"; chip={await page.locator(SEL_MODE_CURRENT).count()}"
        return (("; ".join(out) or "пусто") + extra)
    except Exception as exc:
        return f"не прочитать: {exc}"


async def _set_agent_mode(page, on: bool) -> bool:
    """Включает/выключает родный «Agent Mode» на странице Qwen.

    UI Qwen показывает активный режим по-разному (галочка в пункте меню, чип
    «текущий режим», иконка кнопки), поэтому после нажатия ждём до
    ``AGENT_MODE_WAIT_S`` и принимаем любой из сигналов как подтверждение.
    """
    # 1) меню режимов должно открыться (или агент уже включён — тогда хватит чипа)
    try:
        await _open_mode_menu(page)
    except Exception as exc:
        if await _agent_sign_on_page(page) == on:
            return on
        if isinstance(exc, QwenError):
            raise
        raise QwenError("Меню режимов Qwen не открылось — повторите попытку.") from exc

    # 2) нажимаем и ждём подтверждения: галочка пункта либо чип/иконка на странице
    confirmed = False
    attempts = 0
    # «активная» кнопка меню засчитывается как подтверждение, только если её
    # не было до нашего нажатия (иначе это может быть чужой режим, не агент)
    active_before = await _page_has(page, SEL_MODE_OPEN_ACTIVE)
    # HTML пункта до нажатия — если он перерисовался после клика, состояние изменилось
    sig_before = await _mode_agent_item_sig(page)
    deadline = time.monotonic() + AGENT_MODE_WAIT_S
    while time.monotonic() < deadline:
        state = await _menu_agent_checked(page)
        sign = await _agent_sign_on_page(page)
        if state is None and sign is None:
            # меню закрылось и «тихих» индикаторов нет — открываем и читаем заново
            try:
                await _open_mode_menu(page)
            except Exception:
                pass
            state = await _menu_agent_checked(page)
            sign = await _agent_sign_on_page(page)
        current = sign if sign is not None else state
        if current is not None and bool(current) == on:
            confirmed = True
            break
        if on and attempts and not active_before and await _page_has(page, SEL_MODE_OPEN_ACTIVE):
            confirmed = True  # после нажатия кнопка меню стала «активной» — режим подхватился
            break
        if attempts and sig_before and await _mode_menu_open(page):
            sig_now = await _mode_agent_item_sig(page)
            if sig_now and sig_now != sig_before:
                confirmed = True  # пункт перерисовался после нажатия — состояние изменилось
                break
        if attempts < 2 and await _click_mode_control(page, on):
            attempts += 1
            await asyncio.sleep(1.5)  # даём UI обновиться — второй клик может сбросить режим
            continue
        await asyncio.sleep(0.5)

    if not confirmed:
        detail = await _menu_debug(page)
        logger.warning("Qwen mode (on={}): подтверждения нет, меню режимов: {}", on, detail)
        await _close_mode_menu(page)
        action = "включить" if on else "выключить"
        raise QwenError(
            f"Не удалось {action} режим агента на сайте Qwen: пункт меню не сработал "
            "(возможно, режим недоступен для этой модели или чата)."
        )
    await _close_mode_menu(page)
    return on


async def set_site_agent_mode(on: bool) -> bool:
    """Переключает родной «Agent Mode» на странице Qwen (захватывает общий лок)."""
    async with _lock:
        page = await _ensure_chat_page()
        return await _set_agent_mode(page, on)


async def run_agent(
    prompt: str,
    root: Path | None,
    project_name: str = "",
    timeout_s: int = ASK_TIMEOUT_S,
    max_steps: int = AGENT_MAX_STEPS,
    create_repo=None,
) -> tuple[str, list[dict[str, str]]]:
    """Цикл агента: вопрос → вызовы инструментов → результаты → … → финал.

    Каждый шаг «запись» применяется к файлам проекта сразу (без подтверждения).
    ``root is None`` — проект не выбран: доступен инструмент «создать»
    (``create_repo`` — колбэк из main, он создаёт проект и отдаёт новую папку;
    дальше агент работает уже в нём). Возвращает (финальный ответ, журнал
    шагов), где шаг — ``{"tool": ..., "path": ..., "note": ...}``. Один вопрос
    за раз — как в ``ask``: весь цикл держит общий лок браузера.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        raise QwenError("Пустой промпт — введите задачу для агента.")
    root_path: Path | None = Path(root).resolve() if root is not None else None
    if root_path is not None and not root_path.is_dir():
        raise QwenError("Файлы проекта недоступны: папка не найдена.")

    if root_path is not None:
        context = build_project_context(
            root_path, project_name or root_path.name, rules=AGENT_RULES
        )
        question = f"{prompt}\n\n{context.strip()}" if context else prompt
        if AGENT_RULES not in question:  # файлов нет — инструкция всё равно нужна
            question = f"{question}\n\n{AGENT_RULES.strip()}"
    else:
        # Проект не выбран — начинаем с создания репозитория, остальные
        # инструменты скажут выбрать проект, как только он появится.
        question = (
            f"{prompt}\n\n{AGENT_RULES.strip()}\n\n"
            "Проект не выбран. Пока доступен только инструмент «создать» — он создаёт "
            "новый репозиторий (проект) на сервере, и дальше файловые инструменты "
            "работают уже в нём. Если просят создать репозиторий — начни с него, "
            "не выдавай команды терминала и не спрашивай ссылки."
        )

    log: list[dict[str, str]] = []
    async with _lock:
        page = await _ensure_chat_page()
        # Подстраховка: держим включённым родный режим агента и на сайте Qwen
        # (переключатель в «Песочнице» уже щёлкает им — здесь просто синхронизация;
        # сбой клика не критичен: наш протокол инструментов работает и без него).
        try:
            await _set_agent_mode(page, True)
        except Exception as exc:
            logger.warning("Режим агента на сайте Qwen не включился: {}", exc)
        answer = await _turn(page, question, timeout_s)
        for _step in range(max_steps):
            calls = parse_agent_calls(answer)
            if not calls:
                break  # блоков инструментов нет — это финальный ответ агента
            results: list[str] = []
            finished = False
            for call in calls:
                if call["tool"] == "создать":
                    result, note, new_root = _tool_create(call, create_repo)
                    if new_root is not None:
                        root_path = new_root  # дальше агент работает в новом репозитории
                else:
                    result, note = run_tool(call, root_path)
                log.append(
                    {
                        "tool": call["tool"],
                        "path": call.get("name") or call["path"] or call["query"],
                        "note": note,
                    }
                )
                if call["tool"] == "конец":
                    finished = True
                    break
                results.append(result)
            if finished:
                break
            answer = await _turn(
                page, "Результаты инструментов:\n\n" + "\n\n".join(results), timeout_s
            )
        else:
            # Шаги кончились — просим короткий итог без новых инструментов.
            answer = await _turn(
                page,
                "Инструменты больше не используй. Кратко подведи итог сделанного.",
                timeout_s,
            )

    # В финальном тексте не должно остаться блоков вызовов (если ответ обрезан).
    mark = AGENT_TOOL_MARK.search(answer)
    if mark:
        answer = answer[: mark.start()].rstrip() or answer
    return _clip(answer), log
