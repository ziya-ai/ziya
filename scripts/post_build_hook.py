#!/usr/bin/env python3
"""
A Poetry plugin that runs after the build process to convert the wheel to a platform-independent one
and include templates.
"""

from poetry.plugins.plugin import Plugin
from poetry.plugins.application_plugin import ApplicationPlugin
from poetry.console.application import Application
from poetry.console.commands.build import BuildCommand
from cleo.events.console_command_event import ConsoleCommandEvent
from cleo.events.console_events import TERMINATE
import os
import subprocess
import sys

class PostBuildPlugin(ApplicationPlugin):
    def activate(self, application: Application):
        application.event_dispatcher.add_listener(
            TERMINATE,
            self.on_command_terminate
        )
    
    def on_command_terminate(self, event: ConsoleCommandEvent):
        command = event.command
        if not isinstance(command, BuildCommand):
            return
        
        # Run the post-build script
        script_path = os.path.join(os.path.dirname(__file__), 'post_build.py')
        if os.path.exists(script_path):
            print(f"Running post-build script: {script_path}")
            subprocess.run([sys.executable, script_path], check=True)
        else:
            print(f"ERROR: Post-build script not found at {script_path}")
