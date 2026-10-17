# Дизайн-документ проекта — Flaggen

| Поле | Значение |
|---|---|
| Документ | GDD (design document) проекта |
| Версия документа | 1.0 |
| Статус | В разработке (active development) |
| Последнее обновление | 2026-08-11 |

---

## 1. Концепция

**Flaggen** — процедурный генератор флагов в геральдическом стиле. Программа
составляет флаг из четырёх геральдических слоёв и отрисовывает его как векторную
графику через Cairo.

Флаг собирается автоматически: каждый экземпляр уникален и детерминирован по
сиду. Вместо простого «случайного набора фигур» генератор соблюдает реальные
правила вексиллологии и геральдики: правило тинктуры, контраст цветов,
симметрию, несовместимость элементов и читаемость эмблемы на любом фоне.

Формула проекта: **правила геральдики × случайность = реалистичный флаг**.

---

## 2. Целевая аудитория и сценарии использования

1. **Конструктор флагов** — GUI-режим: выбрать деление, полосу, эмблему и цвета,
   получить «живой» флаг; сохранить результат.
2. **Пакетная генерация** — CLI: сид → PNG/SVG/PDF. Подходит для генерации
   контента (игры, миры, лор, аватары).
3. **Учебный пример** — чистый C++17 + Cairo: слои, контраст, генеративные
   палитры.
4. **Демо-стенд** — «флаговые стенды» для вымышленных государств (миры, кампании).

---

## 3. Основной цикл генерации

Каждая генерация детерминирована сидом `seed` (по умолчанию — время запуска).

```
seed → mt19937 → Config (flags.json)
   ↓
generateFlag(): 12 кандидатов, лучший по оценке
   ├── generatePalette()        — 3 цвета (поле/деление/полоса) с гарантиями контраста
   ├── выбор division           — из 16 делений
   ├── выбор ordinary           — из 20 полос (+ правила несовместимости)
   ├── выбор charge             — из 15 эмблем (+ позиция, масштаб)
   └── scoreFlag()              — штрафы за выход за флаг, низкий контраст, мелкий масштаб
   ↓
resolveCharge()                 — финальная раскладка, цвет эмблемы, фимбриация
   ↓
renderFlag()                    — 4 слоя через Cairo → PNG / SVG / PDF
```

### 3.1. Слои (порядок отрисовки)

| Слой | Палитра | Описание |
|---|---|---|
| `drawField` | `a` | фон флага |
| `drawDivision` | `b` | деление поля (16 видов) |
| `drawOrdinary` | `c` | геральдическая полоса (20 видов) |
| `drawCharge` | `chargeColor` | эмблема (15 видов) |

### 3.2. Палитра

`generatePalette()` строит палитру в HSL и проверяет контраст по WCAG:

| Пара цветов | Минимальный контраст |
|---|---|
| поле `a` / деление `b` | ≥ 1.7 |
| полоса `c` / деление `b` | ≥ 1.6 |
| полоса `c` / поле `a` | ≥ 1.3 |

При неудаче — фолбэк-палитра синий/золото/белый. Это базовое «правило
тинктуры»: металл/цвет не сливаются.

### 3.3. Оценка кандидатов

`generateFlag` создаёт до 12 вариантов и выбирает лучший по `scoreFlag`:
- эмблема внутри флага — бонус, за выход за границы — жёсткий штраф;
- штраф за низкий минимальный контраст эмблемы с фоном;
- штраф за слишком мелкий масштаб (scale < 0.3).

---

## 4. Компоненты

### 4.1. Деления поля (16)

`per_fess`, `per_pale`, `per_bend`, `per_bend_sinister`, `per_saltire`,
`per_quarterly`, `per_chevron`, `per_chevron_inverted`,
`barry_5/7/9`, `paly_5/7`, `bendy_5/7`, `gyronny_8`.

### 4.2. Полосы (ordinaries, 20)

`none`, `fess`, `pale`, `pale_dexter`, `pale_sinister`, `bend`,
`bend_sinister`, `cross`, `nordic`, `saltire`, `chevron`, `pile`, `pall`,
`bordure`, `orle`, `chief`, `base`, `canton`, `lozenge`,
`lozenge_throughout`.

Особенности реализации:
- **chevron** — настоящая V-лента: две квады-рукава перпендикулярной ширины +
  круг-колпачок на острие (полуширина ленты). Жёлоб под остриём остаётся пустым
  для эмблемы.
- **pall** — три штриха (вертикальный + два плеча) со скруглёнными/квадратными
  концами.
- **bordure/orle** — заливка по правилу even-odd (донат).

### 4.3. Эмблемы (charges, 15)

`star_5`, `star_6`, `star_8`, `crescent`, `sun`, `annulet`, `circle`,
`triangle`, `square`, `diamond`, `hexagon`, `octagon`, `cross_patee`,
`fleur_de_lis`, `trefoil`.

Особенности:
- **crescent** — серп: внешний диск минус смещённый внутренний (offsetX).
  Контрольные точки верификатора: рог (x−0.9R, y) и верх (x, y−0.9R);
  полость — центр внутреннего диска.
- **annulet** — кольцо (внешний/внутренний радиус).
- **fleur_de_lis** — центральный заострённый лепесток + два завитка + основание
  с каплей.
- **trefoil** — три лопасти по 120° + стебель; центр между лопастями — полость.

---

## 5. Правила и ограничения

### 5.1. Несовместимость
Из `config/flags.json`: списки `incompatible`. При конфликте генератор откатывается
на безопасный вариант (обычно `ordinary = none`). Например, шеврон несовместим
с `per_chevron` и `per_chevron_inverted`.

### 5.2. Симметрия
Флаг с шевроном и рядом делений принудительно симметричен: `rigidX`/`rigidY` +
выражения выравнивания (`"width/2"`, `"height/2"`).

### 5.3. Фимбриация (окантовка)

Два уровня:

1. **Эмблема** — тонкая обводка (цвет противоположной светимости фона), когда
   `chargeMinContrast < 1.8`. Ширина ~`m*0.018`. Для crescent/annulet отключена
   (они и так читаются на любом фоне).
2. **Полоса** — геральдическая окантовка полосы:
   - *функционально*: `min(contrast(c, a), contrast(c, b)) < 2.0` — полоса
     сливается с фоном;
   - *декоративно*: ~16% случаев, как в классических флагах (Union Jack,
     Бретань).
   - Цвет: белый, если светимость полосы < 0.5, иначе чёрный.
   - Ширина штриха `m*0.03` (видимая ~12px на 800px).

### 5.4. Раскладка эмблем

- **На полосе** (`canRow`): ряды из 2–3 эмблем вдоль
  `fess`/`chief`/`base`/`pale`/`pale_dexter`/`pale_sinister`.
- **На открытом поле** (`none`/`canton`/`orle`/`bordure`, позиция «центр»,
  P≈0.6): компоновки 3-в-ряд, 2-в-ряд, 3-в-колонну, 2-в-колонну, сетка 2×2.
- **Шеврон**: эмблема в жёлобе под остриём `(w/2, 0.62h)`, `maxR = 0.11m`.
- **Безопасность**: `maxR` клампится так, чтобы эмблема (включая углы квадрата —
  фактор 1.414×) полностью помещалась внутри флага: `maxR ≤ minDist / extentFactor`.

### 5.5. Выбор цвета эмблемы
`pickChargeColorAgainst()` перебирает цвета палитры и выбирает тот, который
максимизирует худший контраст с фоном (по выборкам точек фона, включая полосу и
её фимбру).

---

## 6. Визуальный стиль

- Векторная отрисовка, масштабируется без потерь (PNG/SVG/PDF).
- Чистые геральдические цвета (HSL-палитра, контраст WCAG).
- Геометрия: чёткие квады, правильные многоугольники, сглаживание Cairo.
- Формат по умолчанию 1200×800 (3:2), любой размер по запросу.

---

## 7. Интерфейс

### 7.1. CLI (`flaggen`)

```
flaggen [--seed N] [--width W] [--height H] --output <файл.png|svg|pdf>
```

В `stdout` печатается строка-метаданные:

```
seed=N division=X ordinary=Y charge=Z (pos) row=N palette=(#A #B #C)
charge=#HEX scale=S contrast=C [fimbriation] [ofimb] anchors=(x,y);… -> out
```

### 7.2. GUI (`flaggen_gui`, GTK3)

Режим конструктора:
- **Случайный флаг** — новая генерация;
- **Применить** — пересчитать раскладку после ручного изменения слоёв/цветов
  (`resolveCharge`);
- **Стереть рисунок** — очистить канвас;
- **Сохранить…** — сохранить текущий флаг.

---

## 8. Архитектура и технологии

- **Язык**: C++17.
- **Графика**: Cairo (PNG/SVG/PDF).
- **GUI**: GTK3.
- **Конфигурация**: `config/flags.json` (JSON, опционально nlohmann_json).
- **Сборка**: CMake ≥ 3.16.

| Файл | Назначение |
|---|---|
| `src/color.*` | палитра, контраст (WCAG), HSL→RGB |
| `src/config.*` | загрузка конфигурации |
| `src/layers.*` | отрисовка слоёв, эмблем, фимбриация |
| `src/generator.*` | генерация кандидатов, раскладка, оценка, рендер |
| `src/main.cpp` | CLI |
| `src/gui.cpp` | GTK-конструктор |

### Пайплайн разрешения эмблемы (`resolveCharge`)
1. Определить позицию и `maxR` (полоса / открытое поле / шеврон / угол).
2. `computeBandAnchors` — якоря рядов.
3. Клампинг `maxR` по границам флага (`chargeExtentFactor`).
4. `scale = clamp(maxR / baseR, 0.05, 1.0)`, где `baseR = m * outerRadius`
   (для `cross_patee` — `0.42m`).
5. Выборка фона → цвет эмблемы → контраст → фимбриация.

---

## 9. Качество и верификация

Каждое изменение проверяется батчем:
- генерация 500 флагов (`--seed 1..500`, 1200×800);
- попиксельный чек (PIL): цвет эмблемы в якорях (кольца/рога для полых фигур),
  пустые полости, выход за границы флага;
- метрики: `scale` (мин/сред/макс), `contrast` (мин/сред/макс);
- текущие ориентиры: 0 solid/hollow/OOB fail; контраст ≥ 3; scale ≥ 0.3.

---

## 10. Дорожная карта

- [ ] Больше делений: `per_pall`, `gyronny_4/12`, волны/шевроны.
- [ ] Больше полос: `per_chevron` как ordinary, `gyronny`, «поднимающиеся»
  полосы.
- [ ] Больше эмблем: животные, оружие, геометрия; поддержка path-анимаций.
- [ ] Заряженные полосы (эмблемы внутри полос, repeating patterns).
- [ ] Экспорт метаданных в JSON (рядом с PNG).
- [ ] Пресеты реальных флагов и «стили эпох».
- [ ] Web-версия (WASM + cairo).

---

# Design Document — Flaggen (English)

## 1. Concept

**Flaggen** is a procedural heraldic flag generator. It composes a flag from four
heraldic layers and renders it as vector graphics via Cairo.

Every flag is unique yet deterministic per seed. Instead of random shapes, the
generator honours real vexillology: the rule of tincture, color contrast,
symmetry, element incompatibility, and emblem legibility on any background.

Formula: **heraldic rules × randomness = believable flag**.

## 2. Audience & use cases

1. **Flag constructor** — GUI mode: pick a division, ordinary, charge and colors,
   get a live flag; save the result.
2. **Batch generation** — CLI: seed → PNG/SVG/PDF (game/world/lore content).
3. **Educational example** — clean C++17 + Cairo: layers, contrast, generative
   palettes.
4. **Demo** — flag walls for fictional states.

## 3. Core generation loop

`seed → mt19937 → Config`; `generateFlag()` tries up to 12 candidates and keeps
the best by `scoreFlag` (bonus for in-bounds charge, penalties for out-of-bounds,
low contrast, tiny scale). Then `resolveCharge()` finalizes layout/color/fimbriation,
then `renderFlag()` draws the 4 layers to PNG/SVG/PDF.

### 3.1 Layers
`drawField(a) → drawDivision(b) → drawOrdinary(c) → drawCharge(chargeColor)`.

### 3.2 Palette
HSL palette with WCAG guarantees: `a–b ≥ 1.7`, `c–b ≥ 1.6`, `c–a ≥ 1.3`;
fallback blue/gold/white.

## 4. Components

- **Divisions (16):** per_fess, per_pale, per_bend, per_bend_sinister,
  per_saltire, per_quarterly, per_chevron, per_chevron_inverted, barry_5/7/9,
  paly_5/7, bendy_5/7, gyronny_8.
- **Ordinaries (20):** none, fess, pale, pale_dexter, pale_sinister, bend,
  bend_sinister, cross, nordic, saltire, chevron, pile, pall, bordure, orle,
  chief, base, canton, lozenge, lozenge_throughout.
- **Charges (15):** star_5/6/8, crescent, sun, annulet, circle, triangle, square,
  diamond, hexagon, octagon, cross_patee, fleur_de_lis, trefoil.

Highlights: the **chevron** is a true V-band (two arm quads + apex cap) with the
charge placed in the empty trough; **crescent** = outer disk minus offset inner
disk; **trefoil**'s center is hollow (between lobes).

## 5. Rules & constraints

- **Incompatibility** — from `config/flags.json`; on conflict the generator
  falls back to a safe ordinary (`none`).
- **Symmetry** — chevron flags are forced symmetric via `rigidX`/`rigidY` and
  alignment expressions (`"width/2"`, `"height/2"`).
- **Fimbriation** — two levels: charge outline when contrast < 1.8 (skipped for
  crescent/annulet); ordinary outline when the band blends with the background
  (`min(ccA,ccB) < 2.0`) or decoratively (~16%). Outline color = opposite
  luminance of the band; stroke width `m*0.03` (~12 px visible).
- **Layout** — rows of 2–3 charges along band ordinaries; field arrangements
  (3-in-row, 2-in-row, 3-in-column, 2-in-column, 2×2 grid) on open fields;
  chevron charge in the trough; `maxR` clamped to keep every charge (corners
  included) inside the flag.
- **Charge color** — palette color that maximizes worst-case contrast against
  sampled background.

## 6. Visual style
Vector, losslessly scalable (PNG/SVG/PDF), clean heraldic HSL colors, default
1200×800 (3:2), any size on request.

## 7. Interface

**CLI** `flaggen [--seed N] [--width W] [--height H] --output <png|svg|pdf>` —
prints a metadata line (`seed division ordinary charge pos row palette charge
scale contrast [fimbriation] [ofimb] anchors -> out`).

**GUI** (`flaggen_gui`, GTK3) — constructor with Random flag / Apply / Erase /
Save.

## 8. Architecture

C++17, Cairo (PNG/SVG/PDF), GTK3, `config/flags.json`, CMake ≥ 3.16.
Files: `color.*`, `config.*`, `layers.*`, `generator.*`, `main.cpp`, `gui.cpp`.

Charge resolution: position & `maxR` → band anchors → edge clamp → scale
(`clamp(maxR/baseR, 0.05, 1.0)`) → background sampling → charge color → contrast
→ fimbriation.

## 9. Quality & verification

Batch of 500 flags (seeds 1..500): pixel checks for charge color at anchors
(rings/horns for hollow shapes), hollow interiors, out-of-bounds; metrics for
scale and contrast. Targets: 0 solid/hollow/OOB fails, contrast ≥ 3, scale ≥ 0.3.

## 10. Roadmap

More divisions/ordinaries/charges, charged bands (repeating patterns), JSON
metadata export, historical presets, WASM web build.
