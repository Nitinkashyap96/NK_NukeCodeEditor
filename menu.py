# -*- coding: utf-8 -*-
"""
menu.py  -  Nuke Code Editor menu / panel registration  (runs when the Nuke GUI starts)
Author: Nitin Kashyap

Install: copy to  ~/.nuke/menu.py
"""
__author__ = "Nitin Kashyap"

import os
import nuke
import nukescripts
import datetime



version= "v2.0.0"
update_date= "1 Oct 2026"


SHORTCUT = "alt+e"


def create_nuke_code_editor():
    """Factory function for Nuke panel restoration."""
    import nuke_code_editor
    return nuke_code_editor.NukeCodeEditor()


def _install_nuke_code_editor():
    td = nuke.menu("Nuke").addMenu("NK_Nuke Code Editor")

    td.addCommand(
        "Nuke Code Editor",
        "import nuke_code_editor; nuke_code_editor.show_window()",
        SHORTCUT,
        icon="NukeCodeEditor.png",
    )

    try:
        # Register the global wrapper function string instead of the raw module attribute
        nukescripts.panels.registerWidgetAsPanel(
            "create_nuke_code_editor",
            "Nuke Code Editor",
            "com.td.NukeCodeEditor",
        )
        td.addCommand(
            "Nuke Code Editor (panel)",
            "import nukescripts; nukescripts.panels.restorePanel('com.td.NukeCodeEditor')",
            icon="NukeCodeEditor.png",
        )
    except Exception as exc:
        nuke.tprint("[NukeCodeEditor] panel registration skipped: %s" % exc)


try:
    _install_nuke_code_editor()
except Exception as exc:  # never break Nuke startup
    nuke.tprint("[NukeCodeEditor] menu install failed: %s" % exc)



license ="Copyright (C) 2026 by Nitin Kashyap,All rights reserved."
nuke.tprint(f"NK_Nuke Code Editor {version},  build  {update_date}. \n{license}")