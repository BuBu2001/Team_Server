# Вклад в проект / Contributing

Спасибо, что хотите улучшить Flaggen! Ниже — как собрать, изменить и проверить
проект.

## 1. Сборка

```sh
cmake -S . -B build
cmake --build build -j4
```

Ошибок компиляции и предупреждений в `src/` быть не должно. `flaggen_gui`
собирается только при наличии GTK3.

## 2. Как добавить новую эмблему (charge)

1. **Конфиг**: в `config/flags.json`, в массив `charges`, добавить запись,
   например:

   ```json
   {
     "id": "my_shape",
     "render": "my_shape",
     "outerRadius": 0.2,
     "innerRadius": 0.1
   }
   ```

2. **Рендер**: в `src/layers.cpp`, в `drawCharge()`, добавить ветку
   `else if (r == "my_shape")`, рисуя путь через Cairo и завершая его
   `finishFill(cr, c, f, scale)` (это учитывает фимбриацию).

3. **Проверка**: расширить `tools/verify.py` (см. ниже) — добавить радиус
   `EXT["my_shape"]` и, если фигура полая, контрольные точки.

Если фигура асимметрична или имеет полости — это важно для верификатора:
для него нужно точное значение максимального extent и точки «твёрдого» ядра.

## 3. Как добавить деление (division) или полосу (ordinary)

1. Запись в `config/flags.json` (`divisions` / `ordinaries`).
2. Ветка в `drawDivision()` / `drawOrdinary()` в `src/layers.cpp`.
3. Для полосы: окантовка делается через `fillFimb()` (или `fillBand(...)`
   с фимброй); если полоса влияет на раскладку эмблем — проверьте
   `isBandOrdinary()`, `canRow`, `computeBandAnchors()` в `src/generator.cpp`.
4. Несовместимости — через `incompatible` в конфиге.

## 4. Стиль кода

- C++17, соответствие существующему стилю (имена, отступы, порядок включений).
- Без лишних комментариев: комментируйте только нетривиальную геометрию/логику.
- Не меняйте API (заголовки `*.h`) без необходимости; если меняете — обновите
  все места вызова.
- Верификатор и метаданные-строка (`src/main.cpp`) — часть интерфейса: при
  изменении формата обновите README и `tools/verify.py`.

## 5. Проверка изменений (обязательно)

Каждое изменение генератора должно проходить батч-проверку:

```sh
# 1. Сгенерировать 500 флагов и собрать метаданные + рендеры
mkdir -p /tmp/batch && rm -f /tmp/batch/*
for s in $(seq 1 500); do
  ./build/flaggen --seed $s --width 1200 --height 800 \
    --output /tmp/batch/f_$s.png >> /tmp/batch/run.txt
done

# 2. Проверить
python3 tools/verify.py /tmp/batch/run.txt
```

Критерии приёмки:
- `solid_fail: 0`, `hollow_fail: 0`, `oob_fail: 0`;
- `scale` минимум ≥ 0.3, `contrast` минимум ≥ 3 (допускаются редкие
  пограничные случаи, но не систематические);
- распределение рядов/компоновок не вырождается (по `row`/`multi`).

Пояснения к проверкам — в `GDD.md`, раздел «Качество и верификация».

## 6. Pull request

1. Форк + ветка с осмысленным именем.
2. Один PR — одно изменение (или связанный набор).
3. В описании: что меняется, почему, результаты батч-проверки.
4. `CHANGELOG.md` обновить записью в «Unreleased».
5. Поддерживайте совместимость формата метаданных или задокументируйте
   ломающее изменение.

---

# Contributing (English)

Thanks for wanting to improve Flaggen!

## 1. Build

```sh
cmake -S . -B build
cmake --build build -j4
```

No compiler errors or warnings in `src/`. `flaggen_gui` builds only with GTK3.

## 2. Adding a new charge

1. **Config**: add an entry to the `charges` array in `config/flags.json`.
2. **Rendering**: add an `else if (r == "my_shape")` branch in `drawCharge()`
   in `src/layers.cpp`, draw the path with Cairo and finish with
   `finishFill(cr, c, f, scale)` (handles fimbriation).
3. **Verify**: extend `tools/verify.py` — add `EXT["my_shape"]` radius and, for
   hollow shapes, the solid core points.

## 3. Adding a division or ordinary

1. Entry in `config/flags.json`.
2. Branch in `drawDivision()` / `drawOrdinary()` in `src/layers.cpp`.
3. For ordinaries use `fillFimb()` / `fillBand(...)` so the outline works; if the
   band affects charge layout, check `isBandOrdinary()`, `canRow`,
   `computeBandAnchors()` in `src/generator.cpp`.
4. Incompatibilities via `incompatible` in the config.

## 4. Code style

- C++17, match the existing style.
- Comment only non-trivial geometry/logic.
- Don't change public headers (`*.h`) unless necessary; update all call sites.
- The metadata line and verifier are part of the interface: update README and
  `tools/verify.py` when they change.

## 5. Verification (required)

Run the batch check for every generator change:

```sh
mkdir -p /tmp/batch && rm -f /tmp/batch/*
for s in $(seq 1 500); do
  ./build/flaggen --seed $s --width 1200 --height 800 \
    --output /tmp/batch/f_$s.png >> /tmp/batch/run.txt
done
python3 tools/verify.py /tmp/batch/run.txt
```

Acceptance: `solid_fail: 0`, `hollow_fail: 0`, `oob_fail: 0`; scale min ≥ 0.3,
contrast min ≥ 3; row/multi distributions not degenerate.

## 6. Pull request

1. Fork + a meaningful branch name.
2. One PR per change.
3. Describe what/why and include the batch results.
4. Update `CHANGELOG.md` under "Unreleased".
5. Keep the metadata format compatible or document the breaking change.
