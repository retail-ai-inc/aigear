import glob
import importlib
import inspect
from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()


class MLModule:
    def __init__(self, model_path: str | None):
        self.model_path = model_path

    def load_module(self):
        """
        Load the class module in the py file.

        Return class module
        """
        if self.model_path is None:
            logger.error("Missing parameters for model path.")
            return None

        parts = self.model_path.rsplit(".", 1)
        if len(parts) == 1:
            logger.error("Please add the correct model class path: xxx.xxx(class)")
            return None
        else:
            module_part, class_part = parts
            module = importlib.import_module(module_part)
            model_service = getattr(module, class_part)
            return model_service
