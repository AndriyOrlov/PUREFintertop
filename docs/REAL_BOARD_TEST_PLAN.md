# Перевірка на реальних дошках

Перевіряємо **копії**. Нативні тести вже пройдено на Linux (1.10.4–2.1.3) і на
Windows з PureRef 2.1.3 (див. `NATIVE_WINDOWS.md`). Тепер черга реальних дошок.

1. Вибери 3–5 дошок: просту; велику; з групами; з нотатками й олівцем; окремо
   з GIF, обрізаними, віддзеркаленими або прив'язаними (не вбудованими)
   зображеннями; з фото з телефона.
2. Збережи вихідні копії та скрін кожної дошки в PureRef.
3. Встанови пакет за README (або візьми `pur-adapter.exe`). Команди нижче — для
   Python із `.venv`; з `.exe` просто заміни початок на `pur-adapter.exe`.

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m pur_adapter inspect "C:\Refs\copy.pur"
& .\.venv\Scripts\python.exe -X utf8 -m pur_adapter render "C:\Refs\copy.pur" "C:\Refs\preview.png"
& .\.venv\Scripts\python.exe -X utf8 -m pur_adapter export-package "C:\Refs\copy.pur" "C:\Refs\package-test"
& .\.venv\Scripts\python.exe -X utf8 -m pur_adapter create "C:\Refs\package-test\board.json" "C:\Refs\roundtrip.pur"
& .\.venv\Scripts\python.exe -X utf8 -m pur_adapter export-legacy "C:\Refs\copy.pur" "C:\Refs\simplified.pur"
& .\.venv\Scripts\python.exe -X utf8 -m pur_adapter linked "C:\Refs\copy.pur"
```

4. Відкрий `roundtrip.pur` у PureRef і порівняй з оригіналом: положення,
   поворот, масштаб, обрізку, віддзеркалення, порядок шарів, нотатки (текст і
   колір), групи (колір), коментарі, прозорість. Тут не має бути жодних
   відмінностей.
5. Відкрий `simplified.pur` (спрощення для 1.x): групи розгорнуті, олівець став
   картинкою, порядок шарів той самий. Звір зміни зі звітом `.report.json`.
6. Переглянь `preview.png` — це приблизний рендер (шрифти відрізняються), але
   все має стояти на своїх місцях.
7. Збережи дошку повторно самим PureRef і знову прочитай її адаптером
   (`inspect`). Для дошок із даними FrameRef поряд лежить `*.pur.host.json`.
8. Для пошкодженого файлу (якщо є): `repair broken.pur recovered-folder`.
9. Запиши версію PureRef, розмір файлу, кількість елементів, час, пам'ять і
   результат у `examples/real-board-results.csv`. Додай скріни відмінностей.

Критерії: джерело не змінене; вихід відкривається; всі елементи враховані;
позиції й трансформації збігаються; жодної тихої втрати; звіт відповідає
змінам. Відмінності згладжування олівця допустимі, якщо вони видимі й погоджені.

Після твоєї перевірки — виправлення знайденого й повторення тестів. Лише потім
готуємо пропозицію Polygonflow. Лист на цьому етапі не надсилаємо.
