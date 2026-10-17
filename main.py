# Entry point for the browser build (pygbag looks for main.py).
# On desktop you can run either this file or F1Race.py.
import asyncio

import F1Race

asyncio.run(F1Race.main())
