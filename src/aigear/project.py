import os
import shutil


class Project:
    def __init__(self, name: str = "template_project", version: str = "0.0.1"):
        """
        Set projects and use versions

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
        shutil.copytree(template_folder, prodict_folder)
