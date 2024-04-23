import importlib
import glob


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
            module = importlib.import_module(module_path)
            parts = module_path.rsplit(".", 1)
            if len(parts) == 1:
                model_service = getattr(module, parts[0])
            else:
                model_service = getattr(module, parts[1])
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
