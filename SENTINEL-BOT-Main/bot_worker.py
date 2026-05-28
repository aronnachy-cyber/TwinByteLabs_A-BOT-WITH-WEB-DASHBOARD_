import os
os.environ.setdefault("BOT_ONLY", "1")

import main

main.set_bot_restart_callback(main.start_bot_thread)
main.run_bot()
