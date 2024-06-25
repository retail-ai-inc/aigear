import importlib
import glob
import importlib.util
import os


class MLModule:
    def __init__(self, tag):
        self.tag = tag
        self.ml_module_path = ""

    def load_module(self):
        """
        Load the class module in the py file.

        Return class module
        """
        module_path = self.find_module_path()
        self.ml_module_path = module_path
        if module_path:
            parts = module_path.rsplit(".", 1)
            model_service = self.import_module_from_file(parts, module_path)
            return model_service

    @staticmethod
    def import_module_from_file(parts, module_path):
        module_parts = parts[0]
        module = importlib.import_module(module_path)
        if len(parts) == 1:
            model_service = getattr(module, module_parts)
        else:
            module_parts = parts[1]
            model_service = getattr(module, module_parts)
        return model_service

    def find_module_path(self):
        """
        Find the unique py file for the model in models_modules/{tag code}.
        
        Return path
        """
        module_paths = [file.replace("\\", ".").replace("/", ".").replace(".py", "") for file in
                        glob.glob(f'{self.tag}/*.py', recursive=True)]
        if module_paths:
            return module_paths[0]
        else:
            return ""
