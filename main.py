# Entry point for the browser build (pygbag looks for main.py).
# On desktop you can run either this file or F1Race.py.
import asyncio

# pygbag decides which packages to download by reading the imports in this
# file, so pygame has to be imported here even though F1Race.py does the work.
import pygame  # noqa: F401

import F1Race

asyncio.run(F1Race.main())
