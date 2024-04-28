import os
import shutil
from .constant import (
    PROJECT_NAME,
    PROJECT_VERSION,
    PROJECT_DESCRIBE,
)


class Project:
    def __init__(self, name: str = PROJECT_NAME, version: str = PROJECT_VERSION, describe: str = PROJECT_DESCRIBE):
        """
        Used to manage project related functions and information

        name: project name
        version: Project version to manage infrastructure
        """
        self.name = name
        self.version = version
        self.describe = describe

    def init(self):
        """
        Initialize a template according to the project name
        """
        template_path = os.path.abspath(os.path.dirname(__file__))
        template_folder = os.path.join(template_path, "template")
        prodict_folder = os.path.join(os.getcwd(), self.name)

        if not os.path.exists(prodict_folder):
            shutil.copytree(template_folder, prodict_folder)
