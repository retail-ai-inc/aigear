import os
import shutil


class Project:
    def __init__(self, name: str = "template_project", version: str = "0.0.1"):
        """
        Used to manage project related functions and information

        name: project name
        version: Project version to manage infrastructure
        """
        self.name = name
        self.version = version

    def init(self):
        """
        Initialize a template according to the project name
        """
        template_path = os.path.abspath(os.path.dirname(__file__))
        template_folder = os.path.join(template_path, "template")
        prodict_folder = os.path.join(os.getcwd(), self.name)

        if not os.path.exists(prodict_folder):
            shutil.copytree(template_folder, prodict_folder)
