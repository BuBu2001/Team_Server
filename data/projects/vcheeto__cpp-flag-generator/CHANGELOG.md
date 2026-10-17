# История изменений / Changelog

Формат: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/). Версии —
[SemVer](https://semver.org/). / Format:
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/), versions
[SemVer](https://semver.org/).

## [Unreleased]

### Added / Добавлено
- **Фимбриация полос** — геральдическая окантовка ordinary: функционально при
  `min(contrast(c, a), contrast(c, b)) < 2.0`, иначе декоративно (~16%).
  Цвет — противоположная светимость полосы (белый/чёрный), штрих `m*0.03`.
  (Ordinary fimbriation: functional when the band blends with the background,
  decorative otherwise; white/black by band luminance, stroke `m*0.03`.)
- **Клампинг лейаута у краёв флага** — `maxR` ограничивается так, чтобы эмблема
  (включая углы квадрата, фактор 1.414×) полностью помещалась во флаг.
  (Layout clamp: charges including square corners stay fully inside the flag.)
- **Инструмент верификации** — `tools/verify.py` для батч-проверки генератора.
  (Batch verifier `tools/verify.py`.)
- Документация: `GDD.md`, `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`,
  `SECURITY.md`, `LICENSE` (MIT), `.gitignore`.

## [0.2.0] — 2026-08-11

### Added / Добавлено
- **Полевые компоновки эмблем** — 3-в-ряд, 2-в-ряд, 3-в-колонну, 2-в-колонну,
  сетка 2×2 на открытом поле (`none`/`canton`/`orle`/`bordure`).
  (Field charge arrangements: rows, columns and 2×2 grid on open fields.)
- **Новые эмблемы**: `fleur_de_lis`, `trefoil`.
  (New charges: fleur_de_lis, trefoil.)

### Fixed / Исправлено
- Шеврон отрисован как настоящая V-лента: две квады-рукава + круг-колпачок на
  острие; жёлоб под остриём пуст для эмблемы. Устранена дырка на точке Λ.
  (Chevron is now a true V-band of two arm quads plus an apex cap; the trough
  under the apex is empty for the charge.)
- Эмблема шеврона помещается в жёлоб `(w/2, 0.62h)` и масштабируется
  (maxR = 0.11m); шеврон принудительно симметричен (`rigidX`/`rigidY`,
  `"width/2"`, `"height/2"`).
- Устранено дублирование последнего якоря в `resolveCharge`.

## [0.1.0] — 2026-08-01

### Added / Добавлено
- Базовый генератор: 16 делений, 20 полос, 13 эмблем.
- Палитра с гарантиями контраста (WCAG) и фолбэком синий/золото/белый.
- Фимбриация эмблем при контрасте < 1.8 (кроме crescent/annulet).
- Ряды эмблем вдоль полос (2–3) для `fess`/`chief`/`base`/`pale` и вариантов.
- CLI: PNG/SVG/PDF, `--seed`/`--width`/`--height`/`--output`, строка-метаданные.
- GUI-конструктор (GTK3): случайный флаг, применение слоёв/цветов, сохранение.
