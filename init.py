# -*- coding: utf-8 -*-
"""
init.py  -  Nuke Code Editor bootstrap  (runs at Nuke startup, before the GUI)
Author: Nitin Kashyap

Install: copy to  ~/.nuke/init.py
If you already have an init.py, just append the block below to it.
"""
__author__ = "Nitin Kashyap"

import nuke

# Make the "Nuke Code Editor" folder (next to this file) importable.
# Nuke resolves the relative path against the folder this init.py lives in.
try:
    nuke.pluginAddPath("./Nuke Code Editor")
except Exception as exc:
    nuke.tprint("[NukeCodeEditor] could not add Nuke Code Editor to the plugin path: %s" % exc)
