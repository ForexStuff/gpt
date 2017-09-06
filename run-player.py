#!/usr/bin/env python
# -*- coding: utf-8 -*-

import herostuff.modules

try:
    herostuff.modules.app.load_player_window()
    herostuff.modules.app.main()
except EOFError:
    raise
finally:
    print("The End Is Nigh!")
