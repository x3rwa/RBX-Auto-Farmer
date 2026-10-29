Roblox Fishing + Inventory - Fixed 20-second Break (Windows)
==========================================================

Automatic repeat:
1) Fish for 60 seconds of active Roblox foreground time.
2) Stop ALL fishing mouse movement and clicking for the full 20-second break.
3) During those 20 seconds, attempt: Items -> Equip Best -> Close (X).
4) If Equip Best (or another button) cannot be found, the fishing bot
   stays OFF for the full remaining break. It does NOT cut the break short
   and it does NOT disable the overall automation.
5) If Items opened but Equip Best was not found, attempt Close (X)
   within the remaining break, if there is time.
6) When the 20 seconds have elapsed, start fishing for 60 seconds again,
   even if an inventory button wasn't found. Repeat.

If any inventory action takes longer than the break, cancel remaining
inventory actions when the 20 seconds expire; fishing and inventory actions
are never intentionally performed together.

Controls:
F6 or Start: start/resume, preserving current countdown.
F8 or Stop: manual pause of bot and countdown.
Close (X) on the bot window: exit.
Top-left screen corner: PyAutoGUI emergency stop.

Notes:
- Requires Windows; Roblox must be the active visible window.
- Countdown advances only while Roblox is active and automation is running.
- The script uses the real mouse cursor.
- Inventory cycle count measures successful complete sequences.
- It does NOT guarantee that missing Equip Best means Roblox performed the equip.

Install and run:
  python -m pip install -r requirements.txt
  python roblox_fishing_inventory.py

Images must stay next to the script in this structure:
  images/items.png
  images/equip_best.png
  images/close.png

Settings at the top of the Python file:
  FISHING_SECONDS = 60
  INVENTORY_BREAK_SECONDS = 20

Create a single Windows EXE (must include button images):
  python -m pip install pyinstaller
  python -m PyInstaller --onefile --windowed --name RobloxFishingInventory --add-data "images;images" roblox_fishing_inventory.py

Syntax-checked and phase logic tested with simulated timing; not tested
inside Roblox itself.
